"""Adaptive Learning without a session in memory.

The recommender and the skill model used to be imported from `forge_backend`
unchanged, since `rec_logic/recommender.py` and `rec_logic/user.py` pull in
nothing but numpy and the standard library and there was no reason to copy
them the way `answerline.py` had to be copied -- one implementation, and the
web and desktop builds could not drift apart on the maths.

**That stopped being true once `web/` got deployed on its own.** `web/`
lives at its own git remote (`Forge_WebApp`), separate from `Forge_Group`
(see NEXT_SESSION_PROMPT.md) -- a production host cloning `Forge_WebApp`
alone has no `forge_backend/` sibling to reach for. `rec_logic/` here is
now a vendored copy (`web/api/rec_logic/`), the same accepted tradeoff
`answerline.py` already made: **if the scoring/skill maths in
`forge_backend/rec_logic/` ever changes, this copy needs the same change
made by hand, or the two builds' skill models drift.** Worth checking
whenever that code moves; not otherwise different from the answerline.py
situation this file used to point at as the thing it was avoiding.

What is rewritten from the desktop's version is the part around them: where
the model lives between questions. See 0003_adaptive_state.sql for why the
desktop's in-memory dict cannot survive a web server, and what had to start
being written down.

The shape here is:

    load_state()  ->  a `user` object rebuilt from one row
    ... recommend / update ...
    save_state()  ->  the same row, written back

both inside the caller's transaction, so a question served and the skill update
that follows it cannot half-commit.
"""

import json

from rec_logic.recommender import recommender
from rec_logic.user import user as UserModel

MAX_RETRIES = 5
DEFAULT_SKILL = 5.0

# Cached per process: the question set does not change while the server runs,
# and this groups the whole table.
_cluster_counts = None
_category_groups = None


def cluster_counts(conn):
    """{subcategory: number of real clusters}, read from the data.

    The desktop build hardcodes this as CATEGORY_CLUSTERS in merged_api.py.
    Deriving it means a re-clustering run cannot leave the recommender pointing
    at a cluster count that no longer exists -- it would sample cluster ids that
    return nothing, burn all five retries, and 500. (Checked: the hardcoded dict
    currently matches the database exactly, so this changes no behaviour today.)

    Noise (-1) is excluded. It is not a topic, and the recommender already
    treats -1 as its own explore signal.

    MIN_CLUSTERS is cluster_all.py's own acceptance criterion, borrowed rather
    than invented: that script tries 20 hyperparameter settings and takes the
    first that yields more than 10 clusters, so a subject sitting below that
    line never had a clustering it was willing to accept. Applying the same
    bar here means the recommender is offered exactly the subjects the
    clustering step considered a success.

    On the current database this matches the desktop's hardcoded
    CATEGORY_CLUSTERS list with one addition, Other Fine Arts (16 clusters),
    which is a real clustering the hardcoded dict simply never had a line
    added for. It drops Other Science, whose 2 clusters are not a topic model.
    """
    global _cluster_counts
    if _cluster_counts is None:
        MIN_CLUSTERS = 10
        _cluster_counts = {
            row["subcategory"]: row["clusters"]
            for row in conn.execute(
                """select subcategory, count(distinct cluster_label) as clusters
                     from public.questions
                    where cluster_label >= 0 and subcategory is not null
                 group by subcategory
                   having count(distinct cluster_label) >= %s""",
                (MIN_CLUSTERS,)
            ).fetchall()
        }
    return _cluster_counts


def category_groups(conn):
    """{'Literature': ['American Literature', ...]} for the general categories.

    So a user can pick "Literature" and get all five of its shelves. The
    recommender is per subcategory -- each has its own cluster count and its own
    model of your skill -- so a general pick has to be resolved to those before
    anything else happens.

    The minimum count drops the handful of mislabelled rows in the question set,
    which otherwise file Ancient History under Fine Arts.
    """
    global _category_groups
    if _category_groups is None:
        known = cluster_counts(conn)
        groups = {}
        for row in conn.execute(
            """select category, subcategory, count(*) as n
                 from public.questions
                where category is not null and subcategory is not null
             group by category, subcategory
               having count(*) >= 50"""
        ).fetchall():
            if row["subcategory"] in known:
                groups.setdefault(row["category"], set()).add(row["subcategory"])
        _category_groups = {k: sorted(v) for k, v in groups.items()}
    return _category_groups


def resolve_selection(conn, names):
    """What the client picked, resolved to recommender subcategories.

    Returns [(index, picked_name, [subcategories]), ...] in the order given,
    keeping the original index so a parallel list of weights stays aligned even
    when some picks are dropped. A subcategory already covered by an earlier
    pick is not repeated, so "Literature" plus "British Literature" does not
    give British Literature two shares.
    """
    known = cluster_counts(conn)
    groups = category_groups(conn)
    out, seen = [], set()
    for i, name in enumerate(names):
        if not name or not name.strip():
            continue
        name = name.strip()
        candidates = [name] if name in known else groups.get(name, [])
        subs = [s for s in candidates if s not in seen]
        seen.update(subs)
        if subs:
            out.append((i, name, subs))
    return out


def restore_key(names):
    """The single string a selection's saved state is filed under.

    It has to be the selection as the user picked it -- joined in order when
    there is more than one -- and not the subcategory drawn for any one
    question, which changes every request, and not the expanded pool, which
    would turn "Literature" into five names the client never sent. renderer.js
    builds its sessionCategory with the same rule, so a desktop session and a
    web one for the same picks land on the same key.
    """
    cleaned = [n.strip() for n in names if n and n.strip()]
    return cleaned[0] if len(cleaned) == 1 else " + ".join(cleaned)


def load_state(conn, user_id, key):
    """Rebuild the skill model for one selection from its stored row."""
    row = conn.execute(
        """select user_data, start_difficulty, random_skill, recently_seen,
                  questions_served
             from public.category_user_state
            where user_id = %s and category = %s""",
        (user_id, key)).fetchone()

    start = DEFAULT_SKILL if row is None or row["start_difficulty"] is None \
        else row["start_difficulty"]
    model = UserModel(start)

    if row is not None:
        # restore_stats coerces the cluster ids back to ints -- they are ints in
        # memory and strings once they have been through JSON, and without the
        # coercion every lookup misses and the session silently starts over.
        model.restore_stats(row["user_data"])
        if row["random_skill"] is not None:
            model.random_skill = row["random_skill"]
        for qid in (row["recently_seen"] or []):
            model.recently_seen.append(qid)

    return model, (row["questions_served"] if row else 0), (row is not None)


def save_state(conn, user_id, key, model, served):
    """Write the model back. Upsert, because the first question creates it."""
    conn.execute(
        """insert into public.category_user_state
               (user_id, category, user_data, start_difficulty, random_skill,
                recently_seen, questions_served, last_updated)
           values (%s, %s, %s, %s, %s, %s, %s, now())
           on conflict (user_id, category) do update set
               user_data        = excluded.user_data,
               random_skill     = excluded.random_skill,
               recently_seen    = excluded.recently_seen,
               questions_served = excluded.questions_served,
               last_updated     = now()""",
        (user_id, key,
         json.dumps({c: {str(k): float(v) for k, v in clusters.items()}
                     for c, clusters in model.get_stats().items()}),
         float(model.reported_skill),
         float(model.get_random_skill()),
         json.dumps([int(q) for q in model.get_recently_seen()]),
         served))


def cluster_skills(conn, user_id, category=None):
    """Every subject this account has played, with a skill per topic cluster.

    Reads the same `category_user_state.user_data` the recommender itself
    updates on every answer (see `save_state` above) -- Knowledge Depth is a
    view onto the model that already picks a player's next question, not a
    second measurement of anything.

    `category` matches either level, the same as `routes/stats.py`'s
    `_scope()` -- a subject here is keyed by *subcategory* ("Biology"), while
    the profile's own filter offers the parent *category* ("Science"), and
    matching subcategory alone would make every other panel's filter silently
    return nothing for this one. Applied after the parent lookup below, since
    the parent is exactly what a category-level filter needs to compare
    against.

    Returns unlabelled: `[{subcategory, category, clusters: [{cluster, skill}]}]`.
    Naming a cluster means a database read and possibly an AI call, neither of
    which belongs in the module that owns the skill model -- see `clusters.py`.

    **One row's `user_data` can hold more than one subject.** A row is keyed
    by the *selection* an adaptive session was started on (`_session_key`,
    above) -- "Biology" alone, or "Biology + Chemistry" when several were
    picked together -- while `user_data` itself is `{subcategory: {cluster:
    skill}}` for every subcategory the model actually served questions from.
    Gating on `row's own key == a key inside user_data`, which is what the
    desktop's `get_knowledge_depth` does, only ever matches a single-category
    session; a multi-category one's data sits under keys that don't match the
    row's own and is silently dropped. Same shape of bug as the profile filter
    noted in `routes/stats.py` -- so this reads every subject `user_data`
    actually contains, not just the one named by the row it came from.
    """
    rows = conn.execute(
        "select category, user_data from public.category_user_state "
        "where user_id = %s", (user_id,)).fetchall()

    by_subcategory = {}
    for row in rows:
        # jsonb comes back from psycopg already parsed into a dict -- unlike
        # the desktop's SQLite TEXT column, there is no string here to
        # json.loads(). Calling it anyway raises TypeError on every row,
        # which the broad except below would have swallowed silently.
        parsed = row["user_data"] or {}
        if not isinstance(parsed, dict):
            continue
        for subcategory, cluster_skills_raw in parsed.items():
            if not isinstance(cluster_skills_raw, dict) or not cluster_skills_raw:
                continue
            # A selection played twice (once solo, once alongside another
            # category) would otherwise show the same subject's clusters
            # twice; the later row's numbers are the current ones and win.
            by_subcategory[subcategory] = cluster_skills_raw

    if not by_subcategory:
        return []

    # The parent category for each subject, in one query rather than one
    # lookup per subject -- the same batching rule `clusters.py` follows for
    # the same reason.
    parents = {r["subcategory"]: r["category"] for r in conn.execute(
        "select distinct on (subcategory) subcategory, category "
        "from public.questions where subcategory = any(%s)",
        (list(by_subcategory),)).fetchall()}

    subjects = []
    for subcategory, cluster_skills_raw in by_subcategory.items():
        parent = parents.get(subcategory, subcategory)
        if category and subcategory != category and parent != category:
            continue
        entries = []
        for cluster_id, skill in cluster_skills_raw.items():
            try:
                entries.append({"cluster": int(cluster_id), "skill": round(float(skill), 1)})
            except (TypeError, ValueError):
                continue
        if not entries:
            continue
        subjects.append({
            "subcategory": subcategory,
            "category": parent,
            "clusters": entries,
        })
    return subjects


def pick_question(conn, model, subcategory, clusters):
    """Ask the recommender for a cluster, then find a question in it.

    A cluster can come back empty -- exhausted for this difficulty range, or
    everything in it recently seen -- so an empty answer masks that cluster and
    asks again, up to MAX_RETRIES. That masking is what redo_rec's fallback
    exists for: with every cluster masked it returns the explore pick rather
    than reducing over an empty array, which used to take the whole request
    down with "zero-size array to reduction operation maximum".
    """
    cluster, difficulty_range = recommender.recommend_question(
        model, subcategory, clusters)

    masked, row = [], None
    for _ in range(MAX_RETRIES):
        row = _one_question(conn, subcategory, cluster, difficulty_range,
                            model.get_recently_seen())
        if row is not None:
            break
        masked.append(int(cluster))
        cluster, difficulty_range = recommender.redo_rec(
            model, subcategory, clusters, masked)

    if row is None:
        return None, None, None
    # numpy ints all the way out of the recommender; JSON and psycopg both
    # want the real thing.
    return row, int(cluster), [int(d) for d in difficulty_range]


def _one_question(conn, subcategory, cluster, difficulty_range, exclude):
    low, high = int(difficulty_range[0]), int(difficulty_range[1])
    exclude = [int(q) for q in exclude]

    # rand_key rather than `order by random()`, for the reason given in
    # 0001_content.sql -- and it matters more here than on the plain reader,
    # because this query runs up to MAX_RETRIES times for a single question.
    # questions_rec_idx covers (subcategory, cluster_label, difficulty,
    # rand_key), so the equality columns lead and the range comes last.
    import random as _random
    sql = """select id, question, answer, cluster_label, difficulty,
                    category, subcategory, set_name, set_year
               from public.questions
              where subcategory = %s and cluster_label = %s
                and difficulty between %s and %s"""
    params = [subcategory, int(cluster), low, high]
    if exclude:
        sql += " and id <> all(%s)"
        params.append(exclude)

    seek = sql + " and rand_key >= %s order by rand_key limit 1"
    row = conn.execute(seek, params + [_random.random()]).fetchone()
    if row is None:
        row = conn.execute(seek, params + [0.0]).fetchone()
    return row
