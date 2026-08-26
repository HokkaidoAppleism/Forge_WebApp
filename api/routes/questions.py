"""Serving tossups.

One thing here is deliberately different from the desktop build, and it is the
difference a browser forces: **the answer is not sent with the question.**

The desktop app ships the whole row to the renderer and lets it score the buzz
locally, which is fine when the renderer, the backend and the database are all
one process on one person's machine. On the web the renderer is a page anyone
can open the network tab on, so an answer sent up front is an answer sitting in
devtools next to a question that has not been read yet. The answer comes back
from POST /api/answers, after a guess has been committed.

Same reasoning kills client-side scoring; see routes/answers.py.
"""

import random

from flask import Blueprint, g, jsonify, request

import db
import review_settings
from answerline import clean_answerline
from auth import require_user

bp = Blueprint("questions", __name__, url_prefix="/api/questions")

# Only these columns ever leave the server for an unfinished tossup.
_PUBLIC_COLUMNS = (
    "id, question, category, subcategory, set_name, set_year, "
    "packet_number, question_number, difficulty"
)


def _filters():
    """Parse repeated ?category= / ?subcategory= / ?difficulty= params.

    Returns (sql_fragments, params). Built as a list rather than as one
    `(%s is null or col = any(%s))` expression: that shape reads as tidier and
    stops the planner using the index, because a condition OR-ed with an
    unrelated null test is not a searchable predicate.
    """
    clauses, params = [], []

    for field in ("category", "subcategory"):
        values = [v for v in request.args.getlist(field) if v.strip()]
        if values:
            clauses.append(f"{field} = any(%s)")
            params.append(values)

    difficulties = []
    for raw in request.args.getlist("difficulty"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 10:
            difficulties.append(value)
    if difficulties:
        clauses.append("difficulty = any(%s)")
        params.append(difficulties)

    return clauses, params


def _describe_filters():
    parts = request.args.getlist("category") + request.args.getlist("subcategory")
    difficulties = request.args.getlist("difficulty")
    if difficulties:
        word = "difficulties" if len(difficulties) > 1 else "difficulty"
        parts.append(f"{word} {', '.join(difficulties)}")
    return " · ".join(p for p in parts if p)


@bp.get("/random")
@require_user
def random_question():
    clauses, params = _filters()
    where = (" and ".join(clauses)) or "true"

    # An index seek on rand_key rather than a sort of the whole table. The
    # first probe starts at a random point and takes the next row; if it fell
    # past the end of the matching set, the second wraps to the beginning.
    # Two seeks, worst case, instead of 169,099 rows read and discarded.
    sql = (f"select {_PUBLIC_COLUMNS} from public.questions "
           f"where {where} and rand_key >= %s order by rand_key limit 1")

    with db.content_tx() as conn:
        row = conn.execute(sql, params + [random.random()]).fetchone()
        if row is None:
            row = conn.execute(sql, params + [0.0]).fetchone()

    if row is None:
        # Not a failure: the query ran and the honest answer is that no
        # question is like that. The desktop build returns the same shape --
        # 404 with empty:true -- so a client can tell "nothing matched" apart
        # from a real error and stop advising a retry that cannot work.
        described = _describe_filters()
        return jsonify({
            "empty": True,
            "error": f"No questions match those filters{f' ({described})' if described else ''}. "
                     "Try a different difficulty or category.",
        }), 404

    return jsonify(row)


_BROWSE_PAGE = 25
_BROWSE_STATUSES = ("all", "unseen", "queued", "learned", "stuck")


@bp.get("/browse")
@require_user
def browse():
    """Page through the whole question set, labelled with its review status.

    The review list only ever shows what is already in the queue. This shows
    the other 169,000 -- what has never been asked, what was got right, what
    is queued -- so the set reads as a library rather than a list of failures.
    Ported from the desktop's `/browseQuestions`.

    **The answer IS included here, unlike every other route in this file.**
    That is not an oversight and it does not undo what the module docstring
    says: the rule there is that an answer must not travel with a tossup the
    player is *about to be scored on*. Nothing in this list is being scored --
    it is a reference view, and a browser that hides the answers is a list of
    question numbers. The reader's own path (`/random`) still withholds it.

    **One SQL statement, not two passes.** The desktop has to pull its whole
    review queue over HTTP and index it in Python, because `review_queue`
    lives in this database and its questions live in a local SQLite file (see
    that route's own note). Here both are tables in the same database, so the
    status comes from a LEFT JOIN and the paging is done by Postgres.

    Search is `answer ilike %term%`, served by the trigram index added in
    0006_question_search.sql -- ~60-130 ms against ~1.9 s without it.
    """
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip()
    if status not in _BROWSE_STATUSES:
        return jsonify({"error": f"status must be one of {_BROWSE_STATUSES}."}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        return jsonify({"error": "page must be a number."}), 400

    clauses, params = _filters()

    if q:
        # Capped: a 1-2 character fragment matches most of the set, and the
        # trigram index cannot help below 3 characters either (pg_trgm has
        # nothing to look up), so it would degrade to the scan this index
        # exists to avoid.
        if len(q) < 3:
            return jsonify({"error": "Type at least 3 characters to search."}), 400
        clauses.append("qs.answer ilike %s")
        params.append(f"%{q}%")

    with db.user_tx(g.user_id) as conn:
        stuck_at = review_settings.load(conn, g.user_id)["stuckAfterMissed"]

        # `missed` is attempts minus corrects, the same arithmetic the desktop
        # uses; `state` is derived once here so the filter below and the row
        # the client renders can never disagree about what "stuck" means.
        state_sql = """
            case
                when rq.question_id is null then 'unseen'
                when rq.learned_at is not null then 'learned'
                when greatest(rq.attempts - rq.total_correct, 0) >= %s then 'stuck'
                else 'queued'
            end
        """

        # **Which table leads decides whether this is fast.** A filter on one
        # of the in-queue states matches at most what is in the queue -- low
        # hundreds of rows -- while the LEFT JOIN shape below starts from all
        # 169,056 questions and throws nearly all of them away. Measured: 7.3 s
        # for status=queued that way, so the in-queue statuses drive from
        # `review_queue` and join questions by primary key instead. Same "lead
        # with the small side" reasoning the desktop's own /browseQuestions
        # documents, expressed as a join order rather than as Python.
        # Params are assembled in the order the placeholders appear in the
        # finished SQL, which is why they are built here alongside each branch
        # rather than concatenated afterwards: SELECT's copy of state_sql
        # leads, then the join/scope, then the WHERE in the order the clauses
        # were appended, then LIMIT/OFFSET. Getting this wrong is silent --
        # Postgres just reports a type mismatch from whichever pair collided.
        if status in ("queued", "learned", "stuck"):
            source = """from public.review_queue rq
                        join public.questions qs on qs.id = rq.question_id"""
            scope = "rq.user_id = %s and "
            # Appended last, so its params come after the filter params.
            clauses.append(f"({state_sql}) = %s")
            where = " and ".join(clauses)
            query_params = [stuck_at, g.user_id] + params + [stuck_at, status]
        else:
            source = """from public.questions qs
                        left join public.review_queue rq
                               on rq.question_id = qs.id and rq.user_id = %s"""
            scope = ""
            if status == "unseen":
                # An anti-join: questions with no queue row at all. `is null`
                # on the joined side is what makes it one, rather than a
                # filter applied after the rows are already assembled.
                clauses.append("rq.question_id is null")
            where = (" and ".join(clauses)) or "true"
            query_params = [stuck_at, g.user_id] + params

        rows = conn.execute(
            f"""select qs.id, qs.answer, qs.question, qs.category, qs.subcategory,
                       qs.difficulty, qs.set_name, qs.set_year,
                       ({state_sql}) as status,
                       coalesce(rq.attempts, 0) as attempts,
                       greatest(coalesce(rq.attempts, 0) - coalesce(rq.total_correct, 0), 0)
                           as times_missed,
                       rq.correct_streak, rq.sm2_due,
                       (rq.source = 'manual') as bookmarked
                  {source}
                 where {scope}{where}
              order by qs.id
                 limit %s offset %s""",
            query_params + [_BROWSE_PAGE + 1, (page - 1) * _BROWSE_PAGE]
        ).fetchall()

        # "What you answered" for each row, batched the same way the review
        # queue's own /queue route does -- one query keyed by `= any(%s)`
        # rather than one round trip per row. An unseen question was never
        # queued, so it never has anything here.
        histories = {}
        ids = [r["id"] for r in rows]
        if ids:
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

    # One row over the page size tells us whether there is a next page without
    # a second count(*) over 169k rows -- the count is what makes this kind of
    # screen slow, and nothing on it needs a total.
    has_more = len(rows) > _BROWSE_PAGE
    items = rows[:_BROWSE_PAGE]
    for row in items:
        row["answer"] = clean_answerline(row["answer"]) if row["answer"] else None
        row["history"] = histories.get(row["id"], [])

    return jsonify({"items": items, "page": page, "hasMore": has_more})


@bp.get("/<int:question_id>")
@require_user
def one_question(question_id):
    with db.content_tx() as conn:
        row = conn.execute(
            f"select {_PUBLIC_COLUMNS} from public.questions where id = %s",
            (question_id,)).fetchone()
    if row is None:
        return jsonify({"error": "No question with that id."}), 404
    return jsonify(row)


@bp.get("/filters")
@require_user
def filters():
    """Every category and subcategory the picker should offer, with counts.

    One grouped query, not a query per category. The obvious build -- list the
    categories, then loop and count each one's subcategories -- is 9 round
    trips instead of 1 and gets slower as the set grows. Same shape of mistake
    as the N+1 in the desktop cluster labeller (see web/README.md).

    The minimum count drops the handful of mislabelled rows in the question
    set, which otherwise file Ancient History under Fine Arts.
    """
    with db.content_tx() as conn:
        rows = conn.execute(
            """select category, subcategory, count(*) as questions
                 from public.questions
                where category is not null
             group by category, subcategory
               having count(*) >= 50
             order by category, subcategory"""
        ).fetchall()

    grouped = {}
    for row in rows:
        bucket = grouped.setdefault(
            row["category"], {"category": row["category"], "questions": 0,
                              "subcategories": []})
        bucket["questions"] += row["questions"]
        # A subcategory that merely repeats its category is not a shelf.
        if row["subcategory"] and row["subcategory"] != row["category"]:
            bucket["subcategories"].append(
                {"name": row["subcategory"], "questions": row["questions"]})

    return jsonify({"categories": list(grouped.values())})
