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

from flask import Blueprint, jsonify, request

import db
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
