"""Naming the recommender's topic clusters, without a query per cluster.

The clusters come out of k-means over question embeddings, so they are
integers. Knowledge Depth is unreadable until they have names -- "cluster 7 is
your weakest" tells a player nothing they can act on.

**This is the module the desktop's known N+1 lives in, and the reason it is a
module.** `stats_manager.get_cluster_names` does, per cluster: one query for
the cached label, one query for example answers, and -- before the batching fix
-- one AI call. A player with a dozen clusters paid for that a dozen times over
on every profile open. Here every database step is done once for the whole
set:

    one query -> every cached label
    one query -> example answers for every uncached cluster at once

The scoring that picks *which* answers represent a cluster is ported from
`stats_manager.cluster_examples` unchanged -- it is pure Python over rows
already fetched, and it was never the expensive part.

**Nothing here calls Gemini.** The one AI call (`GeminiGetter.name_topic_clusters`,
still batched into one request for every cluster that needs a name) is made by
the caller, outside any transaction -- the same read/network/write split
`routes/answers.py` and `routes/ai.py` use, and for the same reason: a network
call held inside a Postgres transaction pins a pooled connection for however
long Gemini takes to answer. `routes/stats.py`'s knowledge-depth route owns
that split; this module only ever touches the database or plain Python.

**Only AI names are cached** (see 0005_cluster_label_writes.sql). The fallback
label -- a few representative answers, used when a player has no Gemini key or
the call fails -- is rebuilt per request, which is what lets the write policy
be insert-only: there is no cached-fallback row that later needs upgrading to
a real name.
"""

import re
import traceback
from collections import Counter

# How many answers to pull per cluster before scoring them. The desktop's own
# figure; enough to find the shared vocabulary, small enough that a dozen
# clusters is a few thousand short rows rather than a table scan.
SAMPLE_PER_CLUSTER = 300

# How many representative answers to hand the model per cluster.
EXAMPLES_PER_CLUSTER = 10

# How many of those go into a fallback label when there is no AI name.
FALLBACK_EXAMPLES = 3

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")
_TOKEN = re.compile(r"[a-z][a-z'-]+")


def _head(answer):
    """The answerline's main form, before any bracketed directive."""
    return re.split(r"[\[\(<]", str(answer))[0].strip(" ,;:\"“”").strip()


def representative(answers, want=EXAMPLES_PER_CLUSTER):
    """The answers that best show what a cluster is about.

    Scored by shared vocabulary: an answer whose words recur across the rest of
    the cluster describes the cluster, and one whose words appear nowhere else
    is an outlier that would mislead the namer. Answers that merely restate one
    already picked are skipped, so ten examples are ten *different* ones.
    """
    heads = [h for h in (_head(a) for a in answers) if h]
    if not heads:
        return []

    frequency = Counter()
    for head in heads:
        for word in set(_WORD.findall(head.lower())):
            frequency[word] += 1

    def score(head):
        words = set(_WORD.findall(head.lower()))
        return sum(frequency[w] for w in words) / len(words) if words else 0

    picked, picked_tokens = [], []
    for head in sorted(heads, key=score, reverse=True):
        tokens = set(_TOKEN.findall(head.lower()))
        if not tokens or any(tokens <= seen or seen <= tokens
                             for seen in picked_tokens):
            continue
        picked.append(head)
        picked_tokens.append(tokens)
        if len(picked) >= want:
            break
    return picked


def cached_labels(conn, pairs):
    """{(subcategory, cluster_id): label} for whatever is already AI-named.

    One query for every pair, not one per pair. A join against `unnest`
    rather than a chain of `or`s, so this stays one index probe per pair
    instead of a sequential scan the planner can't turn into anything better.
    """
    if not pairs:
        return {}
    subcategories = [sub for sub, _ in pairs]
    cluster_ids = [str(cid) for _, cid in pairs]
    rows = conn.execute(
        """select cl.subcategory, cl.cluster_id, cl.label
             from public.cluster_labels cl
             join unnest(%s::text[], %s::text[]) as w(subcategory, cluster_id)
               on w.subcategory = cl.subcategory and w.cluster_id = cl.cluster_id
            where cl.source = 'ai'""",
        (subcategories, cluster_ids)).fetchall()
    return {(r["subcategory"], int(r["cluster_id"])): r["label"] for r in rows}


# {(subcategory, cluster_id): [representative answer, ...]}, cached per
# process -- same reasoning, same safety, as stats.py's `_tier_labels`: this
# is derived purely from the shared, read-only `questions` table, so it is
# identical for every user and only changes when the question bank is
# replaced. It matters more here than there: `knowledge_depth()` calls this
# with every cluster that has no *AI-named* label yet, which for any player
# without a Gemini key is every cluster, on every single request -- a player
# with 3 subcategories worth of clusters was re-sampling ~14,400 answer rows
# from Supabase on every Knowledge Depth open the AI naming path never got a
# chance to skip.
#
# Holds the *raw* sample, not the scored one -- see the split below. Scoring
# (`representative`, below) is CPU-bound and was, it turned out, the second
# half of the same bug: knowledge_depth() used to make a live Gemini call for
# every uncached cluster (fixed separately, see routes/stats.py), but even
# after deferring that call to a background thread, this module was still
# running the full word-frequency scoring pass synchronously for every one of
# those clusters just to build an instant fallback label -- for a player with
# many unnamed clusters (no key configured, or a just-reset cache after a
# deploy) that's real, request-blocking CPU time the scoring was never meant
# to cost in the first place, since a fallback label only needs *some*
# distinct examples, not the best-scored ones.
_raw_examples_cache = {}


def _raw_examples(conn, pairs):
    """{(subcategory, cluster_id): [answer, ...]}, unscored, for every pair.

    Answered from `_raw_examples_cache` first; only the pairs not already
    cached cost a query, and it is one query for all of them regardless of
    how many that is -- the window function is what makes this one round trip
    instead of one per cluster: `row_number()` partitioned by cluster caps
    each group at SAMPLE_PER_CLUSTER rows, so a dozen clusters costs one
    indexed scan rather than a dozen. Ordered by `rand_key` so the sample
    spreads through the cluster rather than favouring whichever rows were
    inserted first.
    """
    if not pairs:
        return {}

    missing = [p for p in pairs if p not in _raw_examples_cache]
    if missing:
        subcategories = [sub for sub, _ in missing]
        cluster_ids = [int(cid) for _, cid in missing]

        rows = conn.execute(
            """with wanted as (
                   select * from unnest(%s::text[], %s::int[])
                       as w(subcategory, cluster_id)
               )
               select subcategory, cluster_label, answer
                 from (select q.subcategory, q.cluster_label, q.answer,
                              row_number() over (
                                  partition by q.subcategory, q.cluster_label
                                  order by q.rand_key) as rn
                         from public.questions q
                         join wanted w
                           on w.subcategory = q.subcategory
                          and w.cluster_id  = q.cluster_label
                        where q.answer is not null) sampled
                where rn <= %s""",
            (subcategories, cluster_ids, SAMPLE_PER_CLUSTER)).fetchall()

        raw = {}
        for row in rows:
            raw.setdefault(
                (row["subcategory"], row["cluster_label"]), []).append(row["answer"])
        for pair in missing:
            _raw_examples_cache[pair] = raw.get(pair, [])

    return {pair: _raw_examples_cache[pair] for pair in pairs}


def representative_examples(conn, pairs):
    """{(subcategory, cluster_id): [answer, ...]}, scored (`representative`).

    For the background naming pass only, where the quality of what gets
    handed to Gemini actually matters. `knowledge_depth()`'s synchronous,
    instant-fallback path uses `quick_examples` instead -- see its own
    comment for why the scoring pass doesn't belong on that path.
    """
    raw = _raw_examples(conn, pairs)
    return {pair: representative(raw[pair]) for pair in pairs}


def quick_examples(conn, pairs):
    """{(subcategory, cluster_id): [answer, ...]}, cheaply and unscored, for
    knowledge_depth()'s instant fallback label.

    Shares `_raw_examples_cache` with `representative_examples` -- whichever
    of the two runs first for a given pair pays the one query both need, and
    the other reuses it for free from cache. Only the *ranking* differs: this
    skips `representative`'s word-frequency pass (a Counter build plus an
    O(n^2) pairwise token-subset check per candidate) entirely, since a
    fallback label just needs a few genuinely different examples, not the
    single best-scored set -- the distinction only the AI-bound path in
    `representative_examples` needs to care about.
    """
    raw = _raw_examples(conn, pairs)
    return {pair: _quick_pick(raw[pair]) for pair in pairs}


def _quick_pick(answers, want=FALLBACK_EXAMPLES):
    """A handful of distinct answerlines, first-seen order, no scoring."""
    seen, picked = set(), []
    for answer in answers:
        head = _head(answer)
        if head and head not in seen:
            seen.add(head)
            picked.append(head)
            if len(picked) >= want:
                break
    return picked


def cache_names(conn, named):
    """Write newly AI-named clusters into the shared table, best effort.

    `on conflict do nothing` because the write policy is insert-only and two
    players can legitimately open the panel at the same moment: both try to
    write, one wins, the other is a no-op, neither is an error. Failing to
    cache is not worth failing the panel over -- the names are already
    computed and about to be returned to this request regardless -- so this
    swallows rather than raises.
    """
    if not named:
        return
    try:
        conn.cursor().executemany(
            """insert into public.cluster_labels
                   (subcategory, cluster_id, label, source)
               values (%s, %s, %s, 'ai')
               on conflict (subcategory, cluster_id) do nothing""",
            [(sub, str(cid), label) for (sub, cid), label in named.items()])
    except Exception:
        traceback.print_exc()
