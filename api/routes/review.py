"""The review queue: what you got wrong, and when it comes back.

This module is also where the N+1 problem is easiest to fall into, so it is
worth naming. The obvious way to build the review list is:

    rows = select * from review_queue where user_id = ...      -- 1 query
    for row in rows:
        row.question = select * from questions where id = ...  -- + 1 each
        row.history  = select * from review_answers where ...  -- + 1 each

25 rows on a page is 51 round trips instead of 3, and the count grows with the
page rather than staying flat. It is invisible in development, where the
database is on the same machine and each trip costs microseconds, and it is the
whole latency budget once the database is in another data centre. Both loops
are collapsed below: the question comes from a join, and the histories for the
entire page are fetched in one query keyed by `= any(%s)` and grouped in
Python.
"""

from flask import Blueprint, g, jsonify, request

import db
import review_settings
from answerline import clean_answerline
from auth import require_user

bp = Blueprint("review", __name__, url_prefix="/api/review")

PAGE_SIZE = 25


def _categories():
    return [c for c in request.args.getlist("category") if c.strip()]


@bp.get("/next")
@require_user
def next_question():
    """The question most worth drilling right now.

    Due questions first, most overdue leading; a null due date reads as "due
    now", which is how rows that predate SM-2 migrate without disappearing.
    When nothing is due the queue falls through to whatever is scheduled
    soonest rather than declaring an empty session -- "No questions to review"
    over a list of forty visible questions reads as a bug.

    Ties are broken by `last_seen`, least recently seen first, and then by id
    so the order is total. Without that tiebreak the sort could not separate
    rows sharing a due date -- and every row added before SM-2 has no due date
    at all, so they all tie. One account currently has twenty rows tied for
    first. The planner is free to return any of them and in practice returns
    the same one every time, so the queue served one arbitrary question over
    and over while nineteen equally due questions sat behind it. The
    `last_seen` write below was added to stop exactly that and could not,
    because nothing ordered by the column it was writing.
    """
    categories = _categories()
    params = [g.user_id]
    category_clause = ""
    if categories:
        category_clause = "and (q.category = any(%s) or q.subcategory = any(%s))"
        params += [categories, categories]

    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            f"""select q.id, q.question, q.category, q.subcategory, q.difficulty,
                       q.set_name, q.set_year,
                       rq.attempts, rq.correct_streak, rq.sm2_due,
                       (rq.sm2_due is null or rq.sm2_due <= now()) as is_due
                  from public.review_queue rq
                  join public.questions q on q.id = rq.question_id
                 where rq.user_id = %s
                   and rq.learned_at is null
                   {category_clause}
              order by is_due desc, rq.sm2_due asc nulls first,
                       rq.last_seen asc nulls first, rq.question_id
                 limit 1""",
            params).fetchone()

        if row is None:
            return jsonify({
                "empty": True,
                "error": "No questions to review"
                         + (f" in {', '.join(categories)}" if categories else "")
                         + ".",
            }), 404

        # Marked seen when served, not when answered. A question that is read
        # out and skipped would otherwise keep its due date and be served
        # again immediately, forever -- which is exactly what happened on the
        # desktop build before this line existed.
        conn.execute(
            "update public.review_queue set last_seen = now() "
            "where user_id = %s and question_id = %s", (g.user_id, row["id"]))

    return jsonify(dict(row))


@bp.get("/queue")
@require_user
def queue():
    """One page of the review list, with each question's answer history."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    status = request.args.get("status", "all")
    category = (request.args.get("category") or "").strip()

    offset = (page - 1) * PAGE_SIZE

    with db.user_tx(g.user_id) as conn:
        # "Stuck" is not a column -- it is "missed this many times without
        # learning it", against a threshold the player sets. So the filter and
        # the flag below both come from the same number, read once here.
        stuck_at = review_settings.load(conn, g.user_id)["stuckAfterMissed"]
        status_clause = {
            "learned": "and rq.learned_at is not null",
            "queued": "and rq.learned_at is null",
            "due": "and rq.learned_at is null and (rq.sm2_due is null or rq.sm2_due <= now())",
            "stuck": "and rq.learned_at is null and "
                     "(coalesce(rq.attempts, 0) - coalesce(rq.total_correct, 0)) >= %s",
        }.get(status, "")
        status_params = [stuck_at] if status == "stuck" else []

        # Matches either level of the category, same as the stats panels --
        # Adaptive Learning logs a subcategory against a filter built from
        # categories, and the two must agree or a filter silently empties.
        category_clause = ""
        category_params = []
        if category and category.lower() != "all":
            category_clause = "and (q.category = %s or q.subcategory = %s)"
            category_params = [category, category]

        # One row past the page, so "is there a next page" is answered without
        # a count(*) over the whole queue -- the total is the expensive part
        # and nothing on screen needs it.
        rows = conn.execute(
            f"""select rq.question_id, rq.source, rq.attempts, rq.correct_streak,
                       rq.total_correct, rq.learned_at, rq.sm2_due, rq.sm2_interval,
                       q.question, q.answer, q.category, q.subcategory, q.difficulty
                  from public.review_queue rq
                  join public.questions q on q.id = rq.question_id
                 where rq.user_id = %s {status_clause} {category_clause}
              order by rq.added_at desc
                 limit %s offset %s""",
            [g.user_id] + status_params + category_params
            + [PAGE_SIZE + 1, offset]).fetchall()

        has_more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]

        # The N+1 that would otherwise live here, collapsed into one query.
        histories = {}
        if rows:
            ids = [r["question_id"] for r in rows]
            for entry in conn.execute(
                """select question_id, user_answer, was_correct, celerity, answered_at
                     from public.review_answers
                    where user_id = %s and question_id = any(%s)
                 order by answered_at asc""",
                (g.user_id, ids)).fetchall():
                histories.setdefault(entry["question_id"], []).append({
                    "guess": entry["user_answer"],
                    "correct": entry["was_correct"],
                    "celerity": entry["celerity"],
                    "at": entry["answered_at"].isoformat(),
                })

    items = []
    for row in rows:
        item = dict(row)
        item["answer"] = clean_answerline(row["answer"])
        # Missed this often without learning it: repetition clearly is not
        # working and the question needs reading rather than drilling.
        item["timesMissed"] = max(
            0, (row["attempts"] or 0) - (row["total_correct"] or 0))
        item["stuck"] = (row["learned_at"] is None
                         and item["timesMissed"] >= stuck_at)
        item["history"] = histories.get(row["question_id"], [])
        # An attempt count higher than the recorded history means the question
        # was answered before the history was being kept. Saying "no attempts
        # yet" there would contradict the count on the same row.
        item["unrecordedAttempts"] = max(
            0, (row["attempts"] or 0) - len(item["history"]))
        items.append(item)

    return jsonify({"page": page, "pageSize": PAGE_SIZE,
                    "hasMore": has_more, "items": items})


@bp.get("/counts")
@require_user
def counts():
    """The four numbers the Review Settings panel shows, and what is left to
    drill in each category.

    Two queries whatever the queue holds. The obvious shape -- one query per
    tile, then one per category -- is five round trips growing to twenty-five,
    for numbers Postgres will produce in a single grouped pass.

    Only categories with something *unlearned* in them are offered, so a
    subject drilled to zero drops out of the picker on its own rather than
    sitting there serving nothing.
    """
    with db.user_tx(g.user_id) as conn:
        stuck_at = review_settings.load(conn, g.user_id)["stuckAfterMissed"]

        totals = conn.execute(
            """select count(*) filter (where learned_at is null)          as to_review,
                      count(*) filter (where learned_at is not null)      as learned,
                      count(*) filter (where learned_at is null
                                         and (sm2_due is null
                                              or sm2_due <= now()))       as due,
                      count(*) filter (where learned_at is null
                                         and (coalesce(attempts, 0)
                                              - coalesce(total_correct, 0)) >= %s)
                                                                          as stuck
                 from public.review_queue
                where user_id = %s""",
            (stuck_at, g.user_id)).fetchone()

        categories = conn.execute(
            """select q.category, count(*) as waiting
                 from public.review_queue rq
                 join public.questions q on q.id = rq.question_id
                where rq.user_id = %s and rq.learned_at is null
             group by q.category
             order by q.category""",
            (g.user_id,)).fetchall()

    return jsonify({
        "due": totals["due"],
        "toReview": totals["to_review"],
        "learned": totals["learned"],
        "stuck": totals["stuck"],
        "stuckAfterMissed": stuck_at,
        "categories": [dict(row) for row in categories],
    })


@bp.post("/add")
@require_user
def add():
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("questionId")
    if not isinstance(question_id, int):
        return jsonify({"error": "questionId is required."}), 400

    with db.user_tx(g.user_id) as conn:
        exists = conn.execute(
            "select 1 from public.questions where id = %s", (question_id,)).fetchone()
        if exists is None:
            return jsonify({"error": "No question with that id."}), 404

        conn.execute(
            "insert into public.review_queue (user_id, question_id, source) "
            "values (%s, %s, 'manual') on conflict do nothing",
            (g.user_id, question_id))

    return jsonify({"added": True}), 201


@bp.post("/remove")
@require_user
def remove():
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("questionId")
    if not isinstance(question_id, int):
        return jsonify({"error": "questionId is required."}), 400

    with db.user_tx(g.user_id) as conn:
        # Both rows explicitly. No foreign key with ON DELETE CASCADE ties
        # review_answers to the queue entry, and the answer history exists to
        # show how you have been getting a question wrong *while you are still
        # working on it* -- so it goes when the question leaves the list, and
        # stays when the question is merely relearned.
        conn.execute("delete from public.review_answers "
                     "where user_id = %s and question_id = %s",
                     (g.user_id, question_id))
        # `returning` so the answer reflects what actually happened. Reporting
        # removed:true for a question that was never in the list is the API
        # stating something untrue, and it leaves a caller no way to tell a
        # real removal from a no-op -- which the desktop client, whose own
        # /reviewQueue/remove has always 404'd on a miss, needs to know.
        removed = conn.execute(
            "delete from public.review_queue "
            "where user_id = %s and question_id = %s returning question_id",
            (g.user_id, question_id)).fetchone() is not None

    if not removed:
        return jsonify({"removed": False,
                        "error": "That question isn't in your review list."}), 404
    return jsonify({"removed": True})
