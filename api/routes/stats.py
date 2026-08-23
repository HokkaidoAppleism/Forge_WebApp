"""Lifetime numbers, the practice streak, and the five analysis panels.

Everything here aggregates in Postgres rather than pulling rows out and summing
them in Python. That is not premature: `select * from user_stats` is unbounded
in the number of games played, and the answer is nine numbers however many rows
back it.

The panels are the same principle taken seriously. On the desktop each one is a
matplotlib PNG rendered on the server and returned as base64 inside a JSON
body; here the routes return numbers and a written finding, and the browser
draws. The reasoning ports unchanged and lives in `panels.py`; only the picture
was thrown away.

Three of the five change query shape on the way over, and all three for the
same reason -- work that belongs in the database was being done in Python:

  * **Where You Buzz** ran one query per band, four round trips for four
    numbers. One grouped query now.
  * **The neg autopsy** selected every qualifying row of `user_stats` and
    bucketed them in a loop. It is a cross-tab; Postgres does cross-tabs. At
    most eleven difficulties by four bands comes back instead of the whole
    answer history.
  * **Ceiling's tier labels** grouped all 169,056 questions on every request.
    Cached per process now -- see `_tier_labels`.

Scoping matches either level of the category, `(category = %s or subcategory =
%s)`. On the desktop build the profile filtered on `category` alone while
adaptive sessions logged the subcategory, so 17 of 20 filters silently matched
zero rows and every panel came up empty -- and the three that worked were the
categories that happen to mirror their own name, which made the feature look
like it worked.
"""

from flask import Blueprint, g, jsonify, request

import db
import panels
from auth import require_user
from clock import local_day

bp = Blueprint("stats", __name__, url_prefix="/api/stats")

# Days with at least one answer count toward the streak. It would be easy to
# reuse the 5-answer gate the accuracy trend uses, but that gate answers a
# different question -- "is this day's accuracy readable" -- and one answer is
# a thin measurement while being a solid fact: you showed up. A streak that
# breaks on a day you did practise is the one thing a streak must never do.


def _scope():
    category = (request.args.get("category") or "").strip()
    if not category or category.lower() == "all":
        return "", []
    return "and (category = %s or subcategory = %s)", [category, category]


def _answer_scope():
    """`_scope` plus an optional single session, for user_stats only.

    `progress_daily` and `review_queue` have no session column -- a day and a
    queue entry outlive the sitting that produced them -- so the panels built
    on those tables take the category filter and nothing else.
    """
    clause, params = _scope()
    session_id = (request.args.get("session") or "").strip()
    if session_id:
        clause += " and session_id = %s"
        params.append(session_id)
    return clause, params


@bp.get("/summary")
@require_user
def summary():
    """Lifetime numbers, the review queue's shape, and the practice streak.

    `?session=` narrows the first of those to one Adaptive Learning sitting,
    which is what the records page opens the profile with. The other two are
    left out entirely in that case rather than answered from the whole
    account: a streak is a property of a calendar and a review queue is a
    property of an account, and neither becomes a fact about one sitting just
    because it was asked for alongside one. Returning the lifetime figures
    under a session filter would put a nine-day streak on the page next to
    six answers and invite exactly the wrong reading.
    """
    clause, params = _answer_scope()
    session_id = (request.args.get("session") or "").strip()

    with db.user_tx(g.user_id) as conn:
        totals = conn.execute(
            f"""select count(*)                                              as tossups,
                       count(*) filter (where outcome = 'power')             as powers,
                       count(*) filter (where outcome = 'ten')               as tens,
                       count(*) filter (where outcome = 'neg')               as negs,
                       count(*) filter (where outcome = 'pass')              as passes,
                       coalesce(sum(case outcome when 'power' then 15
                                                 when 'ten'   then 10
                                                 when 'neg'   then -5
                                                 else 0 end), 0)             as points,
                       avg(celerity) filter (
                           where outcome in ('power', 'ten', 'neg'))         as avg_celerity
                  from public.user_stats
                 where user_id = %s {clause}""",
            [g.user_id] + params).fetchone()

        if session_id:
            return jsonify({
                "lifetime": _lifetime(totals),
                "session": session_id,
                "review": None,
                "streak": None,
                "daysPlayed": None,
            })

        review = conn.execute(
            """select count(*)                                                as total,
                      count(*) filter (where learned_at is not null)          as learned,
                      count(*) filter (where learned_at is null
                                         and (sm2_due is null
                                              or sm2_due <= now()))           as due_now
                 from public.review_queue
                where user_id = %s""",
            (g.user_id,)).fetchone()

        # The whole play calendar, one row per day, most recent first. The
        # streak is walked in Python because it is a run-length over at most a
        # few hundred rows and expressing it as a window function makes it
        # unreadable for no measurable gain.
        # Category only, never the session -- progress_daily has no session
        # column, because a day outlives the sitting that filled it.
        day_clause, day_params = _scope()
        days = [r["day"] for r in conn.execute(
            f"""select day
                  from public.progress_daily
                 where user_id = %s and answers > 0 {day_clause}
              group by day
              order by day desc""",
            [g.user_id] + day_params).fetchall()]

    return jsonify({
        "lifetime": _lifetime(totals),
        "session": None,
        "review": dict(review),
        # The player's own calendar, not the server's -- see clock.py.
        "streak": _streak(days, local_day(request.args.get("timezone"))),
        "daysPlayed": len(days),
    })


def _lifetime(totals):
    return {
        "tossups": totals["tossups"],
        "powers": totals["powers"],
        "tens": totals["tens"],
        "negs": totals["negs"],
        "passes": totals["passes"],
        "points": totals["points"],
        "averageCelerity": totals["avg_celerity"],
    }


def _streak(days, today):
    """Consecutive days played, ending today or yesterday.

    Yesterday still counts as alive. A streak that reset at midnight would read
    0 every morning until the first answer of the day, telling someone who has
    not had breakfast that they broke it -- so the run stands while its last
    day is today or yesterday, and the "at risk" case is flagged rather than
    zeroed, because it is the only state with something to do about it.
    """
    if not days:
        return {"current": 0, "atRisk": False, "best": 0, "lastPlayed": None}

    gap = (today - days[0]).days
    if gap > 1:
        current = 0
    else:
        current = 1
        for earlier, later in zip(days[1:], days):
            if (later - earlier).days != 1:
                break
            current += 1

    best = run = 1
    for earlier, later in zip(days[1:], days):
        run = run + 1 if (later - earlier).days == 1 else 1
        best = max(best, run)

    return {
        "current": current,
        "atRisk": current > 0 and gap == 1,
        "best": best,
        "lastPlayed": days[0].isoformat(),
    }


# --------------------------------------------------------------- the panels ---

# difficulty -> the tournament series that actually sit at it, computed once
# per process. This is the one cache in the API, and it is safe for the reason
# nothing else is: it is derived from the shared, read-only question set, so it
# is identical for every user and only changes when the question database is
# replaced. Caching anything per-user in module state is what makes the desktop
# adaptive session unable to survive a second worker (see adaptive.py).
_tier_labels = None


def tier_labels(conn):
    """Every set carries exactly one difficulty, so difficulty *is* the tier.

    That means a separate "tournament readiness" panel would be this same data
    with different labels, so the names hang off the difficulty levels instead
    -- which is where the value was. "Difficulty 8" is abstract; "ACF Regionals
    level" is a tournament you can enter.

    Top three series per difficulty, ranked in SQL. The desktop pulls every
    (difficulty, set_name) pair back and counts them in Python.
    """
    global _tier_labels
    if _tier_labels is not None:
        return _tier_labels

    rows = conn.execute(
        r"""select difficulty, series
              from (select difficulty,
                           -- Strip the leading year so it reads as a tier
                           -- ("ACF Regionals") rather than as one specific
                           -- event ("2024 ACF Regionals").
                           regexp_replace(set_name, '^\s*(19|20)\d{2}\s+', '') as series,
                           row_number() over (partition by difficulty
                                                  order by count(*) desc) as rank
                      from public.questions
                     where set_name is not null and difficulty is not null
                  group by difficulty, series) ranked
             where rank <= 3
          order by difficulty, rank""").fetchall()

    labels = {}
    for row in rows:
        labels.setdefault(row["difficulty"], []).append(row["series"])
    _tier_labels = labels
    return _tier_labels


@bp.get("/buzzpoints")
@require_user
def buzzpoints():
    """Where You Buzz: points earned per buzz, by quarter of the tossup."""
    clause, params = _answer_scope()

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select {panels.BAND_CASE} as band,
                       count(*)                                    as buzzes,
                       count(*) filter (where outcome = 'power')   as powers,
                       count(*) filter (where outcome = 'ten')     as tens,
                       count(*) filter (where outcome = 'neg')     as negs
                  from public.user_stats
                 where user_id = %s
                   and outcome in ('power', 'ten', 'neg')
                   and {panels.BAND_BOUNDS} {clause}
              group by band""",
            [g.user_id] + params).fetchall()

    return jsonify(panels.buzzpoints(rows))


@bp.get("/ceiling")
@require_user
def ceiling():
    """Ceiling: accuracy at each difficulty, and where the wall is."""
    clause, params = _answer_scope()

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select difficulty,
                       count(*)                                           as attempts,
                       count(*) filter (where outcome in ('power','ten')) as correct,
                       sum(case outcome when 'power' then 15
                                        when 'ten'   then 10
                                        when 'neg'   then -5 end)         as points
                  from public.user_stats
                 where user_id = %s
                   and difficulty is not null
                   and outcome in ('power', 'ten', 'neg') {clause}
              group by difficulty
              order by difficulty""",
            [g.user_id] + params).fetchall()

        # Only pay for the labels when there is something to label.
        tiers = tier_labels(conn) if rows else {}

    return jsonify(panels.ceiling(rows, tiers))


@bp.get("/negs")
@require_user
def negs():
    """Neg Autopsy: neg rate as a difficulty by buzz-point cross-tab."""
    clause, params = _answer_scope()

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select difficulty,
                       {panels.BAND_CASE} as band,
                       count(*)                                as buzzes,
                       count(*) filter (where outcome = 'neg') as negs
                  from public.user_stats
                 where user_id = %s
                   and outcome in ('power', 'ten', 'neg')
                   and difficulty is not null
                   and {panels.BAND_BOUNDS} {clause}
              group by difficulty, band
              order by difficulty, band""",
            [g.user_id] + params).fetchall()

    return jsonify(panels.neg_autopsy(rows))


@bp.get("/retention")
@require_user
def retention():
    """Retention: SM-2 easiness per subject -- does what you learn stay learned?

    Scoped on the *question's* category rather than on a column of the queue
    table, so this filter reads `q.category` where the others read `category`.
    Worth spelling out rather than reaching for the shared helper and quietly
    filtering on the wrong table.
    """
    category = (request.args.get("category") or "").strip()
    clause, params = "", []
    if category and category.lower() != "all":
        clause = "and (q.category = %s or q.subcategory = %s)"
        params = [category, category]

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select q.category,
                       count(*)             as reviewed,
                       avg(r.sm2_ef)        as ef,
                       sum(r.attempts)      as attempts,
                       sum(r.total_correct) as correct,
                       avg(r.sm2_interval)  as interval_days
                  from public.review_queue r
                  join public.questions q on q.id = r.question_id
                 where r.user_id = %s and r.attempts > 0 {clause}
              group by q.category
              order by avg(r.sm2_ef) asc""",
            [g.user_id] + params).fetchall()

    return jsonify(panels.retention(rows))


@bp.get("/progress")
@require_user
def progress():
    """Progress Over Time: one calendar month of accuracy and buzz point.

    Reads `progress_daily`, which "Reset Stats" deliberately leaves alone -- a
    record of how someone has changed over months is the one thing a reset has
    no business erasing.
    """
    clause, params = _scope()
    month = (request.args.get("month") or "").strip() or None
    # A malformed month is not worth failing on: the panel falls back to the
    # most recent month with play in it, which is what the caller wanted.
    if month and (len(month) != 7 or month[4] != "-"
                  or not month[:4].isdigit() or not month[5:].isdigit()):
        month = None

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select day,
                       sum(answers)      as answers,
                       sum(correct)      as correct,
                       sum(negs)         as negs,
                       sum(points)       as points,
                       sum(celerity_sum) as celerity_sum,
                       sum(celerity_n)   as celerity_n
                  from public.progress_daily
                 where user_id = %s {clause}
              group by day
              order by day""",
            [g.user_id] + params).fetchall()

    return jsonify(panels.progress(rows, month))


@bp.post("/reset")
@require_user
def reset():
    """Reset All Stats: clear the lifetime record, keep everything else.

    Only `user_stats` is deleted -- matching `reset_all_stats()` in the
    desktop's `stats_manager.py` exactly, right down to what it refuses to
    touch:

    * **`progress_daily` survives.** Every panel on the profile except
      Progress Over Time is a snapshot rebuilt from `user_stats`, so wiping
      that table legitimately blanks them. Progress Over Time is a record of
      how the player changed over months, and "reset my numbers" has never
      meant "erase my history" -- it is the one thing on the page a reset has
      no business erasing.
    * **The review queue, the notebook, and Adaptive Learning's skill model
      all survive too.** A neg you are still relearning does not stop being
      unlearned because the number that counted it got reset, a saved note is
      not a stat, and `category_user_state` is a model of what you know, not
      a scoreboard -- resetting the scoreboard should not un-teach the
      recommender everything it has learned about you. The desktop reset
      leaves all three alone as well; nothing here is a new decision.

    One statement, inside the user's own RLS-scoped transaction, so this can
    only ever delete the caller's own rows -- there is no `where` clause to
    forget here, because there is nothing else in the statement to forget it
    on.
    """
    with db.user_tx(g.user_id) as conn:
        deleted = conn.execute(
            "delete from public.user_stats where user_id = %s returning 1",
            (g.user_id,)).fetchall()

    return jsonify({
        "reset": True,
        "rowsDeleted": len(deleted),
        "message": "Your statistics have been reset. Your progress history, "
                   "review queue and notes are kept.",
    })
