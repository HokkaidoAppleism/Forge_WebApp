"""Convergence sanity-check for the Adaptive Learning skill model.

NOT the real recommender path -- it drives the *actual* `rec_logic` classes
(`recommender.recommend_question` + `user.update_stats`) but stands in a
sigmoid "simulated player" for real humans answering real clues, and skips the
database question lookup by assuming a question always exists at the drawn
difficulty. It is a direction-and-rough-scale check, not a promise that any
particular K/S is right for live play. Tune K/S against a live account too.

Run:  python web/tools/adaptive_sim.py
"""

import sys
import os

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from rec_logic.recommender import recommender  # noqa: E402
from rec_logic.user import user as UserModel   # noqa: E402

NUM_CLUSTERS = 46          # American History, the worked example in the prompt
QUESTIONS = 150
TAIL = 40                  # average served difficulty over the last N questions
SEEDS = range(5)
TRUE_SKILLS = (2.0, 5.0, 8.0)
CATEGORY = "American History"


def simulated_answer(true_skill, difficulty, rng):
    """(correct, celerity) for a player of fixed true skill on one question.

    P(correct) rises with (true_skill - difficulty). When correct, the buzz
    point (celerity: 1.0 = first word, 0.0 = last line) also rises with the
    same gap, plus noise -- a stronger player relative to the question buzzes
    earlier.
    """
    p_correct = 1 / (1 + np.exp(-(true_skill - difficulty) / 1.5))
    correct = rng.random() < p_correct
    if not correct:
        return False, 0.0
    center = 1 / (1 + np.exp(-(true_skill - difficulty) / 2.0))
    celerity = float(np.clip(center + rng.normal(0, 0.15), 0.0, 1.0))
    return True, celerity


def run(true_skill, seed, update_kwargs):
    rng = np.random.default_rng(seed)
    model = UserModel(5.0)
    served = []
    for _ in range(QUESTIONS):
        cluster, diff_range = recommender.recommend_question(
            model, CATEGORY, NUM_CLUSTERS)
        low, high = int(diff_range[0]), int(diff_range[1])
        difficulty = int(rng.integers(low, high + 1))
        served.append(difficulty)

        correct, celerity = simulated_answer(true_skill, difficulty, rng)
        cid = -1 if cluster == -1 else int(cluster)
        # Patch update_skill's tuning knobs for this run without touching source.
        _orig = UserModel.update_skill
        UserModel.update_skill = lambda self, us, qd, c, __o=_orig, **_k: __o(
            self, us, qd, c, **{**update_kwargs, **{k: v for k, v in _k.items()
                                                    if k == "celerity"}})
        try:
            model.update_stats(len(served), CATEGORY, cid, correct,
                               difficulty, celerity)
        finally:
            UserModel.update_skill = _orig
    return float(np.mean(served[-TAIL:]))


def _old_update_skill(self, user_skill, question_difficulty, correct,
                      K=0.5, celerity=0):
    """The pre-fix formula, for a real before/after baseline."""
    expected = 1 / (1 + np.exp(-(user_skill - question_difficulty)))
    actual = 1 if correct else 0
    new_skill = user_skill + K * (actual - expected) * (1 + celerity)
    return max(0.0, min(10.0, new_skill))


def sweep_baseline():
    """Today's code: old formula AND old get_skill (frozen reported_skill)."""
    _new_us, _new_gs = UserModel.update_skill, UserModel.get_skill
    UserModel.update_skill = _old_update_skill
    UserModel.get_skill = lambda self, cat, cid: (
        self.user_data.get(cat, {}).get(cid, self.reported_skill))
    try:
        row = []
        for t in TRUE_SKILLS:
            row.append(np.mean([run(t, s, dict(K=0.5)) for s in SEEDS]))
    finally:
        UserModel.update_skill, UserModel.get_skill = _new_us, _new_gs
    print(f"  {'today (both bugs present)':<28} true2.0={row[0]:.2f}  "
          f"true5.0={row[1]:.2f}  true8.0={row[2]:.2f}  "
          f"spread={row[-1] - row[0]:.2f}")


def sweep(label, update_kwargs):
    row = []
    for t in TRUE_SKILLS:
        vals = [run(t, s, update_kwargs) for s in SEEDS]
        row.append(np.mean(vals))
    spread = row[-1] - row[0]
    print(f"  {label:<28} true2.0={row[0]:.2f}  true5.0={row[1]:.2f}  "
          f"true8.0={row[2]:.2f}  spread={spread:.2f}")


if __name__ == "__main__":
    print(f"{QUESTIONS} questions/player, {len(list(SEEDS))} seeds, "
          f"mean served difficulty over last {TAIL}\n")
    sweep_baseline()
    print()
    # Current source default is K=1.5, S=2.0 (post-fix). Try a few.
    sweep("both fixes, K=1.0 S=2.0", dict(K=1.0, S=2.0))
    sweep("both fixes, K=1.5 S=2.0", dict(K=1.5, S=2.0))
    sweep("both fixes, K=2.0 S=2.0", dict(K=2.0, S=2.0))
    sweep("both fixes, K=1.5 S=1.5", dict(K=1.5, S=1.5))
    sweep("both fixes, K=1.5 S=3.0", dict(K=1.5, S=3.0))
