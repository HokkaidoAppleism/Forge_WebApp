"""What counts as "learned", and what counts as "stuck".

Three numbers, and all three are the player's to set -- what it means to know
something is a judgement call, not ours. They live in `user_settings` as text
key/value rows, which is the desktop's `app_settings` table with a `user_id`
in front of it.

This module exists because two places need the same three numbers and must not
disagree about them: `routes/answers.py` decides whether an answer moves a
question toward Learned, and `routes/review.py` decides whether a question has
been missed often enough to be called Stuck. On the desktop both read
`get_review_settings()`; here they both read `load()`. A second copy of
`DEFAULT_LEARNED_AFTER = 2` in another file is the version that eventually
says 3.
"""

# Matching REVIEW_DEFAULTS in merged_api.py. minCelerity is the fraction of the
# tossup still *unread* when the buzz came, so 0.25 means "before the final
# quarter" and 0 means "any time".
DEFAULTS = {
    "learnedAfterCorrect": 2,
    "minCelerity": 0.25,
    "stuckAfterMissed": 3,
}

CASTS = {"learnedAfterCorrect": int, "minCelerity": float, "stuckAfterMissed": int}


def load(conn, user_id):
    """The three settings for one user, defaults filling any gap.

    One query, not three. A row that will not cast -- a hand-edited value, a
    column written by an older build -- falls back to the default for that key
    rather than failing the request that only wanted to score an answer.
    """
    rows = conn.execute(
        "select key, value from public.user_settings "
        "where user_id = %s and key = any(%s)",
        (user_id, list(DEFAULTS))).fetchall()
    stored = {row["key"]: row["value"] for row in rows}

    settings = dict(DEFAULTS)
    for key, cast in CASTS.items():
        try:
            settings[key] = cast(stored[key])
        except (KeyError, TypeError, ValueError):
            pass
    return settings


def clamp(payload):
    """Validate a submitted settings body. Returns (settings, error).

    No practical ceiling on either count: if someone wants a question to need
    a thousand correct answers, or a thousand misses before it is called
    stuck, that is a legitimate choice and the desktop allows it too. Only the
    lower bound is enforced, and minCelerity stops short of 1.0 -- a buzz on
    the very first word is the only thing that would ever clear it.
    """
    try:
        learned = int(payload.get("learnedAfterCorrect",
                                  DEFAULTS["learnedAfterCorrect"]))
        celerity = float(payload.get("minCelerity", DEFAULTS["minCelerity"]))
        stuck = int(payload.get("stuckAfterMissed", DEFAULTS["stuckAfterMissed"]))
    except (TypeError, ValueError):
        return None, ("learnedAfterCorrect and stuckAfterMissed must be whole "
                      "numbers, and minCelerity a fraction.")
    if celerity != celerity:                      # NaN survives float("nan")
        return None, "minCelerity must be a fraction between 0 and 0.95."

    return {
        "learnedAfterCorrect": max(1, min(100000, learned)),
        "minCelerity": max(0.0, min(0.95, celerity)),
        "stuckAfterMissed": max(1, min(100000, stuck)),
    }, None
