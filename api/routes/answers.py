"""Recording an answer. The core write path, and the one worth getting right.

**The server scores the buzz.** The desktop build lets the renderer decide
whether the answer was right, whether it was in power, and how many points that
is worth, then POSTs the verdict. That is fine for a process talking to itself
and it is a scoreboard anyone can edit as soon as the renderer is a web page --
`POST {"outcome": "power"}` in a console is +15 points for a question nobody
read. So the client sends what it *observed* (which question, how many words
had been shown, what was typed) and the server derives everything that counts.

**No network call happens inside a database transaction.** Checking a guess
means asking qbreader, which is allowed six seconds to answer. Holding a
Postgres transaction open across that would pin a pooled connection -- and its
row locks -- for the whole wait, and eight of those in a row is the entire
pool. So the shape is: read (short transaction), check (no transaction), write
(short transaction).
"""

import requests
from flask import Blueprint, g, jsonify, request

import adaptive
import db
import review_settings
import scoring
from answerline import clean_answerline, simple_answer_match
from auth import require_user
from clock import local_day

bp = Blueprint("answers", __name__, url_prefix="/api")

QBREADER_CHECK = "https://www.qbreader.org/api/check-answer"
QBREADER_TIMEOUT = 6


def _power_index(question_text):
    """Which word index the (*) sits at, or -1 if the tossup has no powermark.

    41% of the set has none. The mark can be fused to punctuation ("beta,(*)"),
    so this looks for it inside a token rather than as a token.
    """
    for i, word in enumerate(str(question_text or "").split()):
        if "(*)" in word:
            return i
    return -1


def _check_guess(guess, answerline):
    """(was_correct, scored_offline).

    qbreader's checker understands answerline directives properly. The local
    matcher is the fallback for when it is unreachable, and which one judged a
    given answer is recorded, so a run scored by the weaker checker is not
    indistinguishable from a clean one once it is in the table.
    """
    if not (guess or "").strip():
        return False, None          # nothing to check; a blank guess is a miss

    try:
        response = requests.get(
            QBREADER_CHECK,
            params={"answerline": answerline, "givenAnswer": guess},
            timeout=QBREADER_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("directive") == "accept", False
    except Exception:
        return simple_answer_match(guess, answerline), True


@bp.post("/answers")
@require_user
def log_answer():
    payload = request.get_json(silent=True) or {}

    question_id = payload.get("questionId")
    session_id = (payload.get("sessionId") or "").strip()
    if not isinstance(question_id, int) or not session_id:
        return jsonify({"error": "questionId and sessionId are required."}), 400

    guess = payload.get("guess") or ""
    buzzed = bool(payload.get("buzzed"))
    words_read = payload.get("wordsRead")
    submission_ms = payload.get("submissionTimeMs")

    # ------------------------------------------------------- read the row ---
    with db.user_tx(g.user_id) as conn:
        question = conn.execute(
            "select id, question, answer, category, subcategory, difficulty, "
            "cluster_label from public.questions where id = %s",
            (question_id,)).fetchone()
    if question is None:
        return jsonify({"error": "No question with that id."}), 404

    # ------------------------------------------------ score it, off-clock ---
    was_correct, scored_offline = (False, None)
    if buzzed:
        was_correct, scored_offline = _check_guess(guess, question["answer"])

    total_words = len(str(question["question"]).split()) or 1
    if buzzed and isinstance(words_read, int) and 0 < words_read <= total_words:
        # Fraction of the tossup still unread at the buzz. A buzz on the first
        # word reads 1.0, one on the last reads ~0.0.
        celerity = (total_words - words_read) / total_words
        power_index = _power_index(question["question"])
        in_power = power_index >= 0 and (words_read - 1) <= power_index
    else:
        celerity = None
        in_power = False

    outcome = scoring.outcome_for(was_correct, in_power, buzzed)
    points = scoring.points_for(outcome)

    # ------------------------------------------------------------- write ---
    day = local_day(payload.get("timezone"))

    with db.user_tx(g.user_id) as conn:
        conn.execute(
            """insert into public.user_stats
                   (user_id, session_id, question_id, category, subcategory,
                    difficulty, outcome, celerity, submission_time_ms,
                    scored_offline, user_answer)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (g.user_id, session_id, question["id"],
             question["category"], question["subcategory"], question["difficulty"],
             outcome, celerity,
             submission_ms if isinstance(submission_ms, int) else None,
             scored_offline, (guess or "").strip() or None))

        # The permanent day-by-day record, which Reset Stats does not touch.
        # A 'pass' counts toward the day's answers -- it is a question you sat
        # through -- but contributes no buzz point, since there was no buzz.
        has_celerity = 1 if (celerity is not None and outcome != "pass") else 0
        conn.execute(
            """insert into public.progress_daily
                   (user_id, day, category, subcategory, answers, correct,
                    negs, points, celerity_sum, celerity_n)
               values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
               on conflict (user_id, day, category, subcategory) do update set
                   answers      = progress_daily.answers + 1,
                   correct      = progress_daily.correct + excluded.correct,
                   negs         = progress_daily.negs + excluded.negs,
                   points       = progress_daily.points + excluded.points,
                   celerity_sum = progress_daily.celerity_sum + excluded.celerity_sum,
                   celerity_n   = progress_daily.celerity_n + excluded.celerity_n""",
            (g.user_id, day, question["category"] or "", question["subcategory"] or "",
             1 if outcome in ("power", "ten") else 0,
             1 if outcome == "neg" else 0,
             points,
             float(celerity) if has_celerity else 0.0,
             has_celerity))

        # Every neg files the question into the review list on its own. `do
        # nothing` on conflict rather than an update: a question already in the
        # queue keeps the schedule it has earned, and `source` records how it
        # first got here, which a re-neg should not rewrite.
        if outcome == "neg":
            conn.execute(
                "insert into public.review_queue (user_id, question_id, source) "
                "values (%s, %s, 'missed') on conflict do nothing",
                (g.user_id, question["id"]))

        review = _apply_to_review_queue(
            conn, g.user_id, question["id"], outcome, was_correct, celerity, guess)

        adaptive_result = _apply_to_skill_model(
            conn, g.user_id, payload.get("adaptive"), question,
            outcome, was_correct, celerity)

    return jsonify({
        "correct": was_correct,
        "outcome": outcome,
        "points": points,
        "inPower": in_power,
        "celerity": celerity,
        "scoredOffline": scored_offline,
        "answer": clean_answerline(question["answer"]),
        "review": review,
        "adaptive": adaptive_result,
    })


def _apply_to_skill_model(conn, user_id, adaptive_payload, question,
                          outcome, was_correct, celerity):
    """Advance the recommender's per-cluster skill, if this was an adaptive question.

    The desktop build does this in `/updateUser`, from an `isCorrect` the client
    supplied. Here it rides along with the answer that has just been scored, so
    the model is fed the server's own verdict and there is no separate endpoint
    that will take the player's word for it.

    **The cluster is read off the question row, never off the request.** The
    client echoes back what it was served, and the only part of that trusted is
    `restoreKey` -- which selection's model to write -- because that is the
    user's own pick and the worst a wrong one does is update their own state
    under the wrong name. The cluster id and subcategory decide which skill
    number moves, so those come from the question itself.
    """
    if not isinstance(adaptive_payload, dict):
        return None
    key = (adaptive_payload.get("restoreKey") or "").strip()
    if not key:
        return None

    # A dead tossup is not an attempt. Grading one would move the skill model
    # for a question nobody tried, and `update_stats` multiplies by
    # `(1 + celerity)` -- which is not arithmetic that survives a null buzz
    # point anyway.
    if outcome not in ("power", "ten", "neg"):
        return {"graded": False, "reason": "no buzz"}

    cluster = question["cluster_label"]
    subcategory = question["subcategory"]
    if cluster is None or cluster < 0 or not subcategory:
        # An unclustered question -- one outside the 2016+ embedded set, or in
        # HDBSCAN's noise bucket. It was still answered and is still recorded
        # in user_stats; there is simply no cluster whose skill it describes.
        return {"graded": False, "reason": "question has no cluster"}

    model, served, _ = adaptive.load_state(conn, user_id, key)
    model.update_stats(question["id"], subcategory, int(cluster), was_correct,
                       question["difficulty"] or 0, celerity or 0)
    adaptive.save_state(conn, user_id, key, model, served)

    return {
        "graded": True,
        "subcategory": subcategory,
        "clusterId": int(cluster),
        "skill": round(model.get_skill(subcategory, int(cluster)), 2),
    }


def _apply_to_review_queue(conn, user_id, question_id, outcome,
                           was_correct, celerity, guess):
    """Advance the question's review schedule, if it is in the queue at all.

    Three behaviours, not two. A real buzz is graded. A 'pass' -- a tossup that
    read out with nobody buzzing -- must NOT be graded as a miss, because that
    would reset the streak and reschedule at SM-2's failure grade for a
    question nobody attempted; but it cannot be ignored either, or the most
    overdue question would be served, time out, and be served again forever.
    So it moves sm2_due to now and leaves every counter alone.
    """
    row = conn.execute(
        "select attempts, correct_streak, total_correct, sm2_reps, sm2_ef, "
        "sm2_interval from public.review_queue "
        "where user_id = %s and question_id = %s for update",
        (user_id, question_id)).fetchone()
    if row is None:
        return None                       # not in the queue; nothing to do

    if outcome == "pass":
        conn.execute(
            "update public.review_queue set sm2_due = now() "
            "where user_id = %s and question_id = %s", (user_id, question_id))
        return {"rescheduled": True, "graded": False}

    conn.execute(
        """insert into public.review_answers
               (user_id, question_id, user_answer, was_correct, celerity)
           values (%s, %s, %s, %s, %s)""",
        (user_id, question_id, (guess or "").strip() or None, was_correct, celerity))

    # The player's own thresholds, editable from the Review Settings panel.
    # Read here rather than kept in a constant, because "learned" is a
    # judgement call and the desktop has always let it be set.
    settings = review_settings.load(conn, user_id)
    learned_after = settings["learnedAfterCorrect"]
    min_celerity = settings["minCelerity"]

    # A correct answer on the giveaway clue does not count toward Learned
    # unless the user's own threshold says it should.
    counts = was_correct and (celerity is None or celerity >= min_celerity)
    streak = (row["correct_streak"] or 0) + 1 if counts else 0
    learned = streak >= learned_after

    reps, ef, interval = scoring.sm2_update(
        scoring.sm2_grade(was_correct, celerity, guess),
        row["sm2_reps"] or 0,
        row["sm2_ef"] if row["sm2_ef"] is not None else 2.5,
        row["sm2_interval"] or 0)

    conn.execute(
        """update public.review_queue
              set attempts       = %s,
                  correct_streak = %s,
                  total_correct  = %s,
                  last_seen      = now(),
                  sm2_reps       = %s,
                  sm2_ef         = %s,
                  sm2_interval   = %s,
                  sm2_due        = now() + make_interval(days => %s),
                  learned_at     = case when %s then now() else learned_at end
            where user_id = %s and question_id = %s""",
        ((row["attempts"] or 0) + 1, streak,
         (row["total_correct"] or 0) + (1 if was_correct else 0),
         reps, ef, interval, interval, learned, user_id, question_id))

    return {"rescheduled": True, "graded": True, "streak": streak,
            "learned": learned, "nextInDays": interval}
