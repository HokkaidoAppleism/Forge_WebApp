"""Adaptive Learning endpoints.

Three, where the desktop build has five. Two of its five are gone because the
stateless design removes the thing they existed for:

  * `/startLearn` created the in-memory session. There is no longer one to
    create -- the first question builds the row, and resuming is just loading
    it, so starting and resuming stopped being different operations.
  * `/updateUser` took the client's word for `isCorrect` and updated the skill
    model from it. That is a scoreboard the player can edit, the same problem
    that moved scoring server-side, so the skill update now happens inside
    POST /api/answers where the server has already decided the answer itself.
    (`/checkRecAnswer`, its third relative, has had no caller since it was
    written and raised UnboundLocalError on every call until recently; it is
    not ported.)
"""

import random

from flask import Blueprint, g, jsonify, request

import adaptive
import db
from auth import require_user

bp = Blueprint("adaptive", __name__, url_prefix="/api/adaptive")

_PUBLIC_COLUMNS = ("id", "question", "category", "subcategory", "difficulty",
                   "set_name", "set_year")


@bp.get("/categories")
@require_user
def categories():
    """What Adaptive Learning can actually serve, and where each user stands.

    The general categories and their shelves, each with its cluster count, plus
    whatever skill model already exists for this user -- so the setup screen can
    say "Biology, you left off at 6.2" rather than offering every subject as
    though it were new.
    """
    with db.user_tx(g.user_id) as conn:
        counts = adaptive.cluster_counts(conn)
        groups = adaptive.category_groups(conn)
        saved = {
            row["category"]: {
                "startDifficulty": row["start_difficulty"],
                "questionsServed": row["questions_served"],
                "lastPlayed": row["last_updated"].isoformat(),
            }
            for row in conn.execute(
                "select category, start_difficulty, questions_served, last_updated "
                "from public.category_user_state where user_id = %s",
                (g.user_id,)).fetchall()
        }

    return jsonify({
        "categories": [
            {"name": name,
             "subcategories": [{"name": s, "clusters": counts[s]} for s in subs]}
            for name, subs in sorted(groups.items())
        ],
        "inProgress": saved,
    })


@bp.get("/question")
@require_user
def question():
    """The next question the recommender wants this user to see."""
    requested = [c for c in request.args.getlist("category") if c.strip()]
    if not requested:
        return jsonify({"error": "Pick at least one category."}), 400

    raw_weights = request.args.getlist("weight")

    with db.user_tx(g.user_id) as conn:
        selection = adaptive.resolve_selection(conn, requested)
        if not selection:
            return jsonify({
                "error": "None of those is a subject Adaptive Learning can use.",
                "empty": True,
            }), 404

        # One weight per *pick*, in the order the picks were sent. The weight
        # belongs to the pick rather than to each subcategory underneath it:
        # "Literature at 3, Chemistry at 1" means three Literature questions
        # per Chemistry one, not three of each of Literature's five shelves,
        # which would be fifteen. So the weighted draw chooses a pick, then the
        # shelves inside it share that pick's turn evenly.
        def weight_for(index):
            try:
                return max(0.0, float(raw_weights[index]))
            except (IndexError, TypeError, ValueError):
                return 1.0

        weights = [weight_for(i) for i, _name, _subs in selection]
        # Everything dragged to zero would make random.choices raise rather
        # than pick. The user did ask for these subjects, so an even split is
        # closer to the intent than refusing to serve anything.
        if sum(weights) <= 0:
            weights = [1.0] * len(selection)

        _i, _name, subs = random.choices(selection, weights=weights, k=1)[0]
        subcategory = random.choice(subs)

        key = adaptive.restore_key(requested)
        model, served, resumed = adaptive.load_state(conn, g.user_id, key)

        row, cluster, difficulty_range = adaptive.pick_question(
            conn, model, subcategory, adaptive.cluster_counts(conn)[subcategory])

        if row is None:
            return jsonify({
                "empty": True,
                "error": f"Nothing left to serve in {subcategory} at your level "
                         "right now. Try another subject.",
            }), 404

        adaptive.save_state(conn, g.user_id, key, model, served + 1)

    payload = {k: row[k] for k in _PUBLIC_COLUMNS}
    payload.update({
        # Echoed back so the answer can be attributed to the right cluster
        # without the client having to remember it. It is checked against the
        # question row on the way in, not trusted -- see routes/answers.py.
        "adaptive": {
            "restoreKey": key,
            "subcategory": subcategory,
            "clusterId": cluster,
            "difficultyRange": difficulty_range,
            # The running mean across every cluster with data, not this one
            # cluster's number -- the per-cluster value visibly jumped around
            # question to question even though nothing about the player changed.
            "skill": round(model.overall_skill(), 2),
            "questionsServed": served + 1,
            "resumed": resumed,
        },
    })
    return jsonify(payload)


@bp.post("/end")
@require_user
def end():
    """Save & Quit: record the session in the records book.

    The skill model is already saved -- it is written on every question and
    every answer, so quitting has nothing to flush and closing the tab loses
    nothing. This only writes the summary row the Records page lists.
    """
    payload = request.get_json(silent=True) or {}
    key = (payload.get("restoreKey") or "").strip()
    session_id = (payload.get("sessionId") or "").strip()
    if not key or not session_id:
        return jsonify({"error": "restoreKey and sessionId are required."}), 400

    with db.user_tx(g.user_id) as conn:
        model, served, _ = adaptive.load_state(conn, g.user_id, key)

        # Counted from the answers actually recorded for this session rather
        # than from a counter on the model. user_stats is the authority on what
        # was answered, and a counter that only lives alongside the skill model
        # would drift the moment anything failed between the two writes.
        totals = conn.execute(
            """select count(*) as answered,
                      count(*) filter (where outcome in ('power','ten')) as correct
                 from public.user_stats
                where user_id = %s and session_id = %s""",
            (g.user_id, session_id)).fetchone()

        skills = [s for clusters in model.get_stats().values()
                  for s in clusters.values()]
        end_difficulty = sum(skills) / len(skills) if skills else None

        conn.execute(
            """insert into public.adaptive_sessions
                   (user_id, session_id, category, questions_answered,
                    correct_answers, start_difficulty, end_difficulty,
                    started_at, ended_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, now())""",
            (g.user_id, session_id, key, totals["answered"], totals["correct"],
             model.reported_skill, end_difficulty, payload.get("startedAt")))

    return jsonify({
        "saved": True,
        "questionsAnswered": totals["answered"],
        "correctAnswers": totals["correct"],
        "startDifficulty": model.reported_skill,
        "endDifficulty": end_difficulty,
    })


PAGE_SIZE = 25


@bp.get("/sessions")
@require_user
def sessions():
    """The records book: every Adaptive Learning sitting this account saved.

    `category_user_state` only ever holds the *latest* skill per subject, so
    these summary rows are the only history there is -- which is why they are
    written by `/end` rather than derived on the way out.

    Three queries, and three however many sessions there are. The totals come
    from an aggregate over the whole filtered set rather than by summing the
    page, so paging cannot quietly change what "3,412 questions" means; and
    the category picker is built from every category ever played rather than
    from the filtered rows, so filtering can never hide the option that would
    undo it.
    """
    category = (request.args.get("category") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    clause, params = "", []
    if category and category.lower() != "all":
        clause = "and category = %s"
        params = [category]

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select id, session_id, category, questions_answered, correct_answers,
                       start_difficulty, end_difficulty, started_at, ended_at
                  from public.adaptive_sessions
                 where user_id = %s {clause}
              order by ended_at desc
                 limit %s offset %s""",
            [g.user_id] + params + [PAGE_SIZE + 1, (page - 1) * PAGE_SIZE]
        ).fetchall()

        has_more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]

        totals = conn.execute(
            f"""select count(*)                             as sessions,
                       coalesce(sum(questions_answered), 0) as answered,
                       coalesce(sum(correct_answers), 0)    as correct
                  from public.adaptive_sessions
                 where user_id = %s {clause}""",
            [g.user_id] + params).fetchone()

        played = conn.execute(
            "select distinct category from public.adaptive_sessions "
            "where user_id = %s and category is not null order by category",
            (g.user_id,)).fetchall()

    def shape(row):
        item = dict(row)
        for field in ("started_at", "ended_at"):
            item[field] = row[field].isoformat() if row[field] else None
        return {
            "id": item["id"],
            # The id the stats panels filter on. A session recorded before
            # anything tracked one has none, and the page must be able to tell
            # that apart from "recorded, but you answered nothing" -- one has
            # no detail to show, the other has an empty one.
            "sessionId": item["session_id"],
            "category": item["category"],
            "questionsAnswered": item["questions_answered"],
            "correctAnswers": item["correct_answers"],
            "startDifficulty": item["start_difficulty"],
            "endDifficulty": item["end_difficulty"],
            "startedAt": item["started_at"],
            "endedAt": item["ended_at"],
        }

    answered = totals["answered"]
    return jsonify({
        "sessions": [shape(row) for row in rows],
        "page": page,
        "pageSize": PAGE_SIZE,
        "hasMore": has_more,
        "summary": {
            "totalSessions": totals["sessions"],
            "totalQuestions": answered,
            "totalCorrect": totals["correct"],
            "accuracy": round(totals["correct"] / answered * 100, 1) if answered else 0.0,
            "categories": [row["category"] for row in played],
        },
    })


@bp.post("/sessions/<int:record_id>/delete")
@require_user
def delete_session(record_id):
    """Remove one sitting from the records book.

    **The answers themselves are deliberately left alone.** This row is a
    summary of rows in `user_stats`, not their owner -- deleting it takes the
    sitting off the records page and changes no lifetime number, which is what
    "delete this record" should mean. Wiping the answers too is Reset Stats,
    a different button that does not exist yet.

    An id in the URL is not permission to touch that row: the delete carries
    `user_id = %s` on top of RLS and returns what it actually deleted, so a
    request for somebody else's record is a 404 rather than a cheerful
    success on nothing.
    """
    with db.user_tx(g.user_id) as conn:
        deleted = conn.execute(
            "delete from public.adaptive_sessions "
            "where id = %s and user_id = %s returning id",
            (record_id, g.user_id)).fetchone()

    if deleted is None:
        return jsonify({"error": "No saved session with that id."}), 404
    return jsonify({"deleted": True, "id": deleted["id"]})
