"""Scoring and spaced repetition, ported unchanged from the desktop build.

Plain arithmetic with no database access, so it can be tested on its own --
the same reason it sits apart in merged_api.py. Kept byte-for-byte equivalent
to the desktop version on purpose: a web account and a desktop install that
graded the same answer differently would be a very confusing bug to chase.
"""

POINTS = {"power": 15, "ten": 10, "neg": -5, "pass": 0}


def points_for(outcome):
    return POINTS.get(outcome, 0)


def sm2_update(q, n, ef, interval):
    """SM-2, exactly as SuperMemo published it.

    Given a grade q (0-5) and the question's current state, return the state it
    should have next: n consecutive recalls, easiness factor ef, and the gap in
    days before it is asked again.
    """
    if q >= 3:
        if n == 0:
            interval = 1
        elif n == 1:
            interval = 6
        else:
            interval = round(interval * ef)
        n += 1
    else:
        n = 0
        interval = 1

    ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    # SuperMemo's floor. Below 1.3 a card comes back so often it crowds out
    # everything else, and the paper's advice is to rewrite it instead.
    if ef < 1.3:
        ef = 1.3

    return n, ef, interval


def sm2_grade(was_correct, celerity, user_answer=None):
    """Derive the 0-5 grade from how the tossup was actually answered.

    SuperMemo asks for a self-rating because a flashcard app cannot tell
    instant recall from a laboured one. This can: celerity is the fraction of
    the question still unread at the buzz, which measures the same thing the
    grades describe, and a measured number cannot be talked up.

        0  no answer at all
        1  wrong, but confident enough to buzz
        3  right, in the last third of the question
        4  right, in the middle third
        5  right, in the first third
    """
    if not was_correct:
        return 0 if not (user_answer or "").strip() else 1
    if celerity is None:
        return 4
    if celerity >= 2 / 3:
        return 5
    if celerity >= 1 / 3:
        return 4
    return 3


def outcome_for(was_correct, in_power, buzzed):
    """The four values user_stats.outcome is allowed to hold.

    'pass' is a tossup that read to the end with no buzz. It counts as a
    question you sat through -- so it belongs in the answer count -- while
    contributing no buzz point and no points, since there was no buzz.
    """
    if not buzzed:
        return "pass"
    if not was_correct:
        return "neg"
    return "power" if in_power else "ten"
