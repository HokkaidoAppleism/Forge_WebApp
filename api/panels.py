"""The stats panels: shaping and wording, with no database and no drawing.

Each panel is two halves. The half that ports is the reasoning -- which bands
are worth comparing, where the ceiling is, which axis the negs actually track,
and the sentence that says so. The half that does not port is the picture:
`stats_manager.py` renders every one of these as a matplotlib PNG and hands
back base64 in a JSON body, which is the wrong shape for a browser that can
draw. So the routes return the numbers and the finding, and the page draws.

Everything here is a pure function over rows the caller has already
aggregated. That split is deliberate -- the desktop build interleaves SQL,
bucketing and prose in one method per panel, and the consequence is that the
neg autopsy pulls *every buzz you have ever made* into Python to bucket it by
hand. Grouping belongs in Postgres; judgement belongs here.

The minimum-sample gates are the load-bearing part of all five. A band, level,
subject or day below its gate is shown but never compared, never called your
best or worst, and never used to reach a verdict. Two buzzes at 100% is not a
strength, and a panel that says it is teaches the wrong lesson confidently.
"""

import calendar

# Where in the tossup a buzz landed, by fraction of the question left unread.
# Upper bound 1.01 rather than 1.0 because celerity is computed against a word
# count and can land a hair over on a first-word buzz.
BUZZ_BANDS = [
    (0.75, 1.01, "First quarter"),
    (0.50, 0.75, "Second quarter"),
    (0.25, 0.50, "Third quarter"),
    (0.00, 0.25, "Last quarter"),
]
BAND_LABELS = [label for _, _, label in BUZZ_BANDS]

# The SQL that assigns a row to a band. One grouped query rather than the
# desktop's one query per band -- four round trips for four numbers, and the
# band bounds have to be bound ahead of the scope parameters in that shape,
# which is a footgun the comment there is entirely about.
BAND_CASE = """
    case when celerity >= 0.75 then 'First quarter'
         when celerity >= 0.50 then 'Second quarter'
         when celerity >= 0.25 then 'Third quarter'
         else 'Last quarter' end
"""

# Rows outside [0, 1.01) belong to no band, so they are excluded rather than
# swept into the nearest one.
BAND_BOUNDS = "celerity is not null and celerity >= 0 and celerity < 1.01"

BUZZ_MIN_SAMPLE = 5
CEILING_MIN_SAMPLE = 3
NEG_MIN_SAMPLE = 5
RETENTION_MIN_SAMPLE = 3
PROGRESS_MIN_SAMPLE = 5

# How many percentage points one axis has to beat the other by before it is
# called the dominant one. Below this the two are treated as tied, because
# picking a winner off a couple of points is noise dressed as a diagnosis.
NEG_LEVER_MARGIN = 10


# ----------------------------------------------------------- where you buzz ---

def buzzpoints(rows):
    """Points actually earned per buzz, split by where in the tossup it came.

    This is the one number that speaks to a decision made on every single
    question -- buzz now, or wait for one more clue -- and nothing else in the
    app can show it. Average celerity is computed over correct answers only, so
    buzzes that came early *and missed* contribute nothing to it: the habit is
    invisible in that figure by construction.

    Scored in real points (power +15, ten +10, neg -5) rather than as a
    conversion percentage, because the tradeoff being measured is points. A
    band converting 30% can still be worth buzzing in if the hits are powers,
    and one converting 50% can still be a net loss if they are not.
    """
    counted = {r["band"]: r for r in rows}
    bands = []
    for label in BAND_LABELS:
        row = counted.get(label)
        if row is None or not row["buzzes"]:
            bands.append({"label": label, "buzzes": 0, "powers": 0, "tens": 0,
                          "negs": 0, "points": 0, "perBuzz": None,
                          "conversion": None, "reliable": False})
            continue
        buzzes, powers, tens, negs = (row["buzzes"], row["powers"],
                                      row["tens"], row["negs"])
        points = powers * 15 + tens * 10 + negs * -5
        bands.append({
            "label": label,
            "buzzes": buzzes,
            "powers": powers,
            "tens": tens,
            "negs": negs,
            "points": points,
            "perBuzz": round(points / buzzes, 1),
            "conversion": round((powers + tens) / buzzes * 100),
            "reliable": buzzes >= BUZZ_MIN_SAMPLE,
        })

    played = [b for b in bands if b["buzzes"]]
    total_buzzes = sum(b["buzzes"] for b in played)
    total_points = sum(b["points"] for b in played)
    if not total_buzzes:
        return {"bands": bands, "totalBuzzes": 0, "totalPoints": 0,
                "perBuzz": None, "minSample": BUZZ_MIN_SAMPLE, "hasData": False,
                "evaluation": "No buzzes recorded yet. "
                              "Answer some tossups to see this."}

    # Only bands with enough buzzes to mean anything get compared -- otherwise
    # a single lucky buzz becomes "your best buzz point".
    solid = [b for b in played if b["reliable"]]
    evaluation = (f"Only {total_buzzes} buzz{'' if total_buzzes == 1 else 'es'} "
                  f"so far - keep playing and this will sharpen up.")
    if len(solid) >= 2:
        best = max(solid, key=lambda b: b["perBuzz"])
        worst = min(solid, key=lambda b: b["perBuzz"])
        most = max(solid, key=lambda b: b["buzzes"])
        share = round(most["buzzes"] / total_buzzes * 100)
        if worst["perBuzz"] < 0 and most is worst:
            evaluation = (
                f"{share}% of your buzzes come in the {most['label'].lower()}, and they "
                f"cost you {abs(worst['perBuzz'])} points each. Buzzing in the "
                f"{best['label'].lower()} is worth {best['perBuzz']:+.1f} - waiting one more "
                f"clue is the single biggest gain available to you.")
        elif worst["perBuzz"] < 0:
            evaluation = (
                f"Your {worst['label'].lower()} buzzes lose {abs(worst['perBuzz'])} points each; "
                f"the {best['label'].lower()} is your strongest at {best['perBuzz']:+.1f}. "
                f"Hold off when you're not sure.")
        else:
            evaluation = (
                f"Every buzz point is net positive for you - the {best['label'].lower()} is "
                f"strongest at {best['perBuzz']:+.1f} per buzz. You can afford to push earlier.")
    elif len(solid) == 1:
        only = solid[0]
        evaluation = (
            f"Almost all your buzzes are in the {only['label'].lower()} "
            f"({only['perBuzz']:+.1f} per buzz). Spread them out to see where "
            f"you're strongest.")

    return {"bands": bands, "totalBuzzes": total_buzzes,
            "totalPoints": total_points,
            "perBuzz": round(total_points / total_buzzes, 1),
            "evaluation": evaluation, "minSample": BUZZ_MIN_SAMPLE,
            "hasData": True}


# ------------------------------------------------------------------ ceiling ---

def ceiling(rows, tiers):
    """Accuracy at each question difficulty -- where your wall is.

    Tossups carry a 0-10 difficulty and it is recorded on every answer, and
    nothing in the desktop UI ever read it back. The result is that a player
    can spend a large share of their reps on questions they cannot convert at
    all and have no way to see it, because the profile reports one accuracy
    number averaged across every difficulty at once.

    `tiers` maps a difficulty to the tournament series that actually sit at it,
    which is the difference between "difficulty 8" and "ACF Regionals level" --
    one is abstract and the other is an event you can enter.
    """
    if not rows:
        return {"levels": [], "hasData": False, "ceiling": None, "wall": None,
                "strongest": None, "totalAnswers": 0,
                "minSample": CEILING_MIN_SAMPLE,
                "evaluation": "No answers recorded yet."}

    levels = []
    for row in rows:
        attempts, correct = row["attempts"], row["correct"] or 0
        examples = tiers.get(row["difficulty"], [])
        levels.append({
            "difficulty": row["difficulty"],
            "attempts": attempts,
            "correct": correct,
            "accuracy": round(correct / attempts * 100),
            "points": row["points"] or 0,
            "perQuestion": round((row["points"] or 0) / attempts, 1),
            "reliable": attempts >= CEILING_MIN_SAMPLE,
            "tier": examples[0] if examples else None,
            "exampleSets": examples,
        })

    solid = [l for l in levels if l["reliable"]]
    # Ceiling: the hardest level you still convert at least half the time.
    converting = [l for l in solid if l["accuracy"] >= 50]
    ceiling_at = max((l["difficulty"] for l in converting), default=None)

    # Wall: the easiest level at or above which you convert nothing at all,
    # provided everything harder is also zero -- a lone bad level in the middle
    # is not a wall, it is noise.
    wall = None
    for level in solid:
        if level["accuracy"] == 0 and all(
                h["accuracy"] == 0 for h in solid
                if h["difficulty"] >= level["difficulty"]):
            wall = level["difficulty"]
            break

    # The level you are actually best at, which is not the same as the 50%
    # ceiling: a player converting 33% at their best has no level above 50% at
    # all, and telling them to drop to easier questions "until something
    # converts" would be plainly wrong when they are converting.
    strongest = max(solid, key=lambda l: (l["accuracy"], l["difficulty"]),
                    default=None)

    total = sum(l["attempts"] for l in levels)
    if not solid:
        evaluation = ("Not enough answers at any one difficulty yet - "
                      "keep playing and your ceiling will show up here.")
    elif wall is not None:
        wasted = sum(l["attempts"] for l in levels if l["difficulty"] >= wall)
        wall_tier = next((l["tier"] for l in levels if l["difficulty"] == wall), None)
        named = f" ({wall_tier} level)" if wall_tier else ""
        evaluation = (
            f"You convert nothing at difficulty {wall}{named} and above - "
            f"{wasted} of your {total} questions ({round(100 * wasted / total)}%) "
            f"went there for no points. ")
        if ceiling_at is not None:
            evaluation += (f"You hold above half up to difficulty {ceiling_at}; "
                           f"work at {ceiling_at} and {ceiling_at + 1} to move the wall.")
        elif strongest and strongest["accuracy"] > 0:
            evaluation += (f"You're strongest at difficulty {strongest['difficulty']} "
                           f"({strongest['accuracy']}%) - stay around there until it climbs.")
        else:
            evaluation += "Try easier questions until something converts."
    elif ceiling_at is not None:
        evaluation = (f"You hold above half up to difficulty {ceiling_at}. "
                      f"Push into {ceiling_at + 1} to find your ceiling.")
    elif strongest and strongest["accuracy"] > 0:
        evaluation = (f"You're strongest at difficulty {strongest['difficulty']} "
                      f"({strongest['accuracy']}%). Nothing has hit half yet - "
                      f"consolidate there before pushing harder.")
    else:
        evaluation = "Nothing converting yet. Try easier questions."

    return {"levels": levels, "ceiling": ceiling_at, "wall": wall,
            "strongest": strongest["difficulty"] if strongest else None,
            "totalAnswers": total, "minSample": CEILING_MIN_SAMPLE,
            "evaluation": evaluation, "hasData": True}


# --------------------------------------------------------------- neg autopsy ---

def neg_autopsy(cells):
    """Why the negs happen: buzz point against question difficulty.

    The outcome pie counts negs and stops. "Where You Buzz" prices every buzz
    by quarter but pools all difficulties, so it can say *when* the points leak
    and never *whether the questions were too hard anyway*. Those two failures
    want opposite fixes -- wait one more clue, versus drop to a level you can
    actually convert -- and nothing in the app could tell them apart.

    The split that matters is **rates, not counts**. Raw neg counts follow
    wherever you happen to buzz most: a cell with 12 negs out of 12 buzzes and
    one with 12 out of 40 look identical in a tally and are nothing alike.
    Every figure here is negs over buzzes in that same cell.

    `cells` is one row per (difficulty, band) with buzzes and negs, grouped in
    Postgres. The desktop reads every qualifying row of user_stats and buckets
    them in a Python loop, which is the whole answer table over the wire for
    forty-four numbers.
    """
    total_buzzes = sum(c["buzzes"] for c in cells)
    total_negs = sum(c["negs"] for c in cells)
    if not total_buzzes:
        return {"hasData": False, "totalNegs": 0, "totalBuzzes": 0,
                "negRate": None, "byQuarter": [], "byDifficulty": [], "grid": [],
                "dominant": None, "lever": None, "minSample": NEG_MIN_SAMPLE,
                "evaluation": "No buzzes recorded yet."}

    def bucket(buzzes, negs):
        return {"buzzes": buzzes, "negs": negs,
                "negRate": round(100 * negs / buzzes) if buzzes else None,
                "reliable": buzzes >= NEG_MIN_SAMPLE}

    # The two marginals, each with its own denominator.
    quarters = {label: [0, 0] for label in BAND_LABELS}
    difficulties = {}
    grid = {}
    for cell in cells:
        label, difficulty = cell["band"], cell["difficulty"]
        quarters[label][0] += cell["buzzes"]
        quarters[label][1] += cell["negs"]
        d = difficulties.setdefault(difficulty, [0, 0])
        d[0] += cell["buzzes"]
        d[1] += cell["negs"]
        grid[(difficulty, label)] = [cell["buzzes"], cell["negs"]]

    by_quarter = [dict(label=label, **bucket(*quarters[label]))
                  for label in BAND_LABELS]

    # No tournament names on this panel. Ceiling names its levels because "can
    # you convert at ACF Fall difficulty" is the question there. Here the
    # difficulty is one axis of a cross-tab and the reader is already holding
    # two variables at once -- a third label per row turns the axis into
    # something to decode rather than read.
    by_difficulty = [dict(difficulty=d, **bucket(*difficulties[d]))
                     for d in sorted(difficulties)]

    grid_out = [dict(difficulty=difficulty, quarter=label, **bucket(b, n))
                for (difficulty, label), (b, n) in sorted(grid.items())]

    # Which axis is actually the lever: compare how far the neg rate moves
    # along each, over buckets big enough to mean anything. The axis with the
    # wider spread is the one worth acting on; a near-tie is reported as a tie.
    solid_q = [q for q in by_quarter if q["reliable"]]
    solid_d = [d for d in by_difficulty if d["reliable"]]

    def spread(buckets):
        if len(buckets) < 2:
            return None
        return (max(b["negRate"] for b in buckets)
                - min(b["negRate"] for b in buckets))

    q_spread, d_spread = spread(solid_q), spread(solid_d)
    lever = None
    if q_spread is not None and d_spread is not None:
        if q_spread - d_spread >= NEG_LEVER_MARGIN:
            lever = "timing"
        elif d_spread - q_spread >= NEG_LEVER_MARGIN:
            lever = "difficulty"
        else:
            lever = "both"
    elif q_spread is not None:
        lever = "timing"
    elif d_spread is not None:
        lever = "difficulty"

    # The single cell losing the most questions, for the headline.
    dominant = None
    scored = [g for g in grid_out if g["negs"]]
    if scored:
        dominant = dict(max(scored, key=lambda g: (g["negs"], g["negRate"] or 0)))

    return {"hasData": True, "totalNegs": total_negs, "totalBuzzes": total_buzzes,
            "negRate": round(100 * total_negs / total_buzzes),
            "byQuarter": by_quarter, "byDifficulty": by_difficulty,
            "grid": grid_out, "dominant": dominant, "lever": lever,
            "minSample": NEG_MIN_SAMPLE,
            "evaluation": _neg_evaluation(total_negs, total_buzzes, solid_q,
                                          solid_d, grid, lever, dominant)}


def _neg_evaluation(total_negs, total_buzzes, solid_q, solid_d, grid, lever,
                    dominant):
    """Turn the two marginals into one sentence a player can act on."""
    plural = "buzz" if total_buzzes == 1 else "buzzes"
    if not total_negs:
        return (f"No negs in {total_buzzes} {plural} - nothing to autopsy. "
                f"Whatever you're doing on the buzzer is working.")

    # Below the gate there is no verdict to give: state the count and stop,
    # rather than reading a habit into a handful of questions.
    if total_buzzes < NEG_MIN_SAMPLE * 2 or not (solid_q or solid_d):
        return (f"{total_negs} neg{'' if total_negs == 1 else 's'} in "
                f"{total_buzzes} {plural}. Too few to say whether that's a "
                f"timing habit or a difficulty problem yet.")

    worst_q = max(solid_q, key=lambda q: q["negRate"]) if solid_q else None
    worst_d = max(solid_d, key=lambda d: d["negRate"]) if solid_d else None
    lead = f"{total_negs} of your {total_buzzes} buzzes are negs."

    if lever == "timing" and worst_q:
        others = [q for q in solid_q if q["label"] != worst_q["label"]]
        rest = ""
        if others:
            on = sum(q["negs"] for q in others)
            ob = sum(q["buzzes"] for q in others)
            rest = (f" Buzz later and it roughly halves - "
                    f"{round(100 * on / ob)}% across your other buzz points.")
        # The strong version of the finding: if difficulty does not rescue you
        # inside the worst quarter, timing is the whole story.
        inside = [(d, b, n) for (d, q), (b, n) in grid.items()
                  if q == worst_q["label"] and b >= 3]
        control = ""
        if len(inside) >= 2:
            rates = [round(100 * n / b) for _, b, n in inside]
            if min(rates) >= 70:
                easiest = min(inside, key=lambda t: t[0])
                control = (f" It isn't the questions being hard: buzzing there, "
                           f"you neg {round(100 * easiest[2] / easiest[1])}% even at "
                           f"difficulty {easiest[0]}, your easiest well-played level.")
        return (f"{lead} {worst_q['negRate']}% of your {worst_q['label'].lower()} "
                f"buzzes are negs.{rest}{control} That points at timing rather than "
                f"a knowledge gap - try one more clue before committing.")

    if lever == "difficulty" and worst_d:
        best_d = min(solid_d, key=lambda d: d["negRate"])
        return (f"{lead} They track difficulty rather than when you buzz: "
                f"{worst_d['negRate']}% at difficulty {worst_d['difficulty']}"
                f" against {best_d['negRate']}% at difficulty "
                f"{best_d['difficulty']}. You're playing above your level - "
                f"drop a tier and the negs go with it.")

    if lever == "both" and worst_q and worst_d:
        return (f"{lead} Both axes matter about equally: "
                f"{worst_q['negRate']}% on {worst_q['label'].lower()} buzzes and "
                f"{worst_d['negRate']}% at difficulty {worst_d['difficulty']}. "
                f"No single lever fixes this - waiting one more clue at a level "
                f"you already convert is the cheapest place to start.")

    if dominant:
        return (f"{lead} Most of them - {dominant['negs']} - come at difficulty "
                f"{dominant['difficulty']} on {dominant['quarter'].lower()} buzzes. "
                f"Not enough spread yet to say which of the two is driving it.")
    return f"{lead} Not enough spread yet to say what's driving them."


# ---------------------------------------------------------------- retention ---

def retention(rows):
    """How well what you have learned *sticks*, per subject.

    Every other panel measures whether you get a question right. This one
    measures whether it stays right. `review_queue.sm2_ef` is SM-2's easiness
    factor -- it rises each time a question is recalled well and falls when it
    is not -- so averaged over a subject it says how fast that subject decays
    for this player. That is a different axis from accuracy: "Mythology you
    learn fast and forget fast" is a sentence no other stat here can produce.

    Only questions that have actually been reviewed count. A queued question
    nobody has come back to still sits at the 2.5 default, and letting those in
    would drag every subject toward 2.5 and make the whole chart say nothing.
    """
    subjects = []
    for row in rows:
        subjects.append({
            "category": row["category"] or "Uncategorised",
            "reviewed": row["reviewed"],
            "ef": round(float(row["ef"] or 2.5), 2),
            "attempts": row["attempts"] or 0,
            "correct": row["correct"] or 0,
            "intervalDays": round(float(row["interval_days"] or 0), 1),
            # Below the gate the number is shown but never compared, the same
            # rule the buzz bands and ceiling levels follow.
            "reliable": row["reviewed"] >= RETENTION_MIN_SAMPLE,
        })

    solid = [s for s in subjects if s["reliable"]]
    total_reviewed = sum(s["reviewed"] for s in subjects)

    if not subjects:
        summary = ("Nothing has been reviewed yet, so there is nothing to say "
                   "about how well it sticks. Drill the missed list and this "
                   "fills in.")
    elif not solid:
        summary = (f"{total_reviewed} question{'' if total_reviewed == 1 else 's'} "
                   f"reviewed so far - not enough in any one subject to say how "
                   f"well it sticks yet. A subject needs {RETENTION_MIN_SAMPLE} "
                   f"before it is worth comparing.")
    elif len(solid) == 1:
        s = solid[0]
        holding = (" — below the 2.5 starting point, so it is taking more "
                   "repetitions than average to stick" if s["ef"] < 2.5 else
                   " — at or above the 2.5 starting point, so it is holding well")
        summary = (f"{s['category']} sits at an easiness of {s['ef']:.2f}{holding}. "
                   f"It is the only subject with {RETENTION_MIN_SAMPLE} or more "
                   f"reviews; the rest need more before they can be compared.")
    else:
        worst, best = solid[0], solid[-1]
        gap = best["ef"] - worst["ef"]
        if gap < 0.15:
            summary = (f"Everything you have reviewed enough of sticks at about the same rate "
                       f"({worst['ef']:.2f}-{best['ef']:.2f}). No subject is decaying faster than "
                       f"the others - the difference between them is accuracy, not retention.")
        else:
            summary = (f"{worst['category']} is the one that slips: easiness {worst['ef']:.2f} against "
                       f"{best['ef']:.2f} for {best['category']}. Getting a {worst['category']} question "
                       f"right once is not making it stay right, so it wants shorter gaps and more "
                       f"repetitions rather than more new questions.")

    return {"subjects": subjects, "minSample": RETENTION_MIN_SAMPLE,
            "totalReviewed": total_reviewed, "summary": summary,
            "hasData": bool(subjects)}


# ---------------------------------------------------------------- progress ---

def progress(rows, month=None):
    """Accuracy and buzz point over time -- is any of this working?

    Every other panel is a snapshot: it can say you neg 79% and never whether
    that is better or worse than last week. For a practice tool that is the
    question, and nothing in the app could answer it.

    It pairs with the neg autopsy deliberately. That one says *what* the
    problem is; this is the only thing that can say whether changing it helped.
    Celerity travels next to accuracy for exactly that reason -- buzzing
    earlier and converting less is a shape visible here and nowhere else.

    Built from `progress_daily`, **not** user_stats: a reset clears user_stats,
    and a trend rebuilt from that table would be destroyed by the one thing a
    reset has no business erasing.

    **Paged one calendar month at a time.** A single axis covering every day
    ever played is readable for a week and unreadable after a year. Days with
    no play are still returned, with `played: false`, so a fortnight away reads
    as a fortnight rather than being silently closed up -- but they carry a
    null accuracy, not a zero, because not playing is not the same as getting
    everything wrong.

    The trend and the written finding are computed over **all** history, not
    the month on screen, so paging back through old months does not change the
    verdict.
    """
    all_days = []
    for row in rows:
        answers = row["answers"] or 0
        if not answers:
            continue
        correct = row["correct"] or 0
        celerity_n = row["celerity_n"] or 0
        all_days.append({
            "date": row["day"].isoformat(),
            "answers": answers,
            "correct": correct,
            "negs": row["negs"] or 0,
            "accuracy": round(correct / answers * 100),
            "celerity": (round(float(row["celerity_sum"]) / celerity_n, 3)
                         if celerity_n else None),
            "points": row["points"] or 0,
            "reliable": answers >= PROGRESS_MIN_SAMPLE,
            "played": True,
        })

    if not all_days:
        return {"hasData": False, "days": [], "months": [], "month": None,
                "monthLabel": None, "trend": None, "daysPlayed": 0,
                "totalAnswers": 0, "reliableDays": 0,
                "minSample": PROGRESS_MIN_SAMPLE,
                "evaluation": "No answers recorded yet."}

    # Which months have anything in them, oldest first.
    months = {}
    for day in all_days:
        key = day["date"][:7]
        entry = months.setdefault(key, {"key": key, "answers": 0, "daysPlayed": 0})
        entry["answers"] += day["answers"]
        entry["daysPlayed"] += 1
    for key, entry in months.items():
        year, mon = int(key[:4]), int(key[5:7])
        entry["label"] = f"{calendar.month_name[mon]} {year}"
    month_list = [months[k] for k in sorted(months)]

    # Default to the most recent month with play in it.
    if month not in months:
        month = month_list[-1]["key"]
    year, mon = int(month[:4]), int(month[5:7])

    by_date = {d["date"]: d for d in all_days}
    days = []
    for dom in range(1, calendar.monthrange(year, mon)[1] + 1):
        date = f"{year:04d}-{mon:02d}-{dom:02d}"
        day = dict(by_date[date]) if date in by_date else {
            "date": date, "answers": 0, "correct": 0, "negs": 0,
            "accuracy": None, "celerity": None, "points": 0,
            "reliable": False, "played": False}
        day["dayOfMonth"] = dom
        days.append(day)

    solid_all = [d for d in all_days if d["reliable"]]
    total = sum(d["answers"] for d in all_days)

    # Direction comes from the earlier half against the later half of the days
    # big enough to read. A least-squares line over two points is a straight
    # line through two points -- it would look like a finding and carry none --
    # so this stays an explicit before/after comparison.
    trend = None
    if len(solid_all) >= 2:
        mid = len(solid_all) // 2
        early = solid_all[:mid] or solid_all[:1]
        late = solid_all[mid:] or solid_all[-1:]

        def avg(group, key):
            values = [d[key] for d in group if d[key] is not None]
            return sum(values) / len(values) if values else None

        acc_from, acc_to = avg(early, "accuracy"), avg(late, "accuracy")
        cel_from, cel_to = avg(early, "celerity"), avg(late, "celerity")
        trend = {
            "accuracyFrom": round(acc_from) if acc_from is not None else None,
            "accuracyTo": round(acc_to) if acc_to is not None else None,
            "celerityFrom": round(cel_from, 3) if cel_from is not None else None,
            "celerityTo": round(cel_to, 3) if cel_to is not None else None,
            "daysCompared": len(solid_all),
            "firstDay": solid_all[0]["date"],
            "lastDay": solid_all[-1]["date"],
        }

    played_this_month = [d for d in days if d["played"]]
    return {"hasData": True, "days": days, "months": month_list, "month": month,
            "monthLabel": months[month]["label"],
            "monthDaysPlayed": len(played_this_month),
            "monthAnswers": sum(d["answers"] for d in played_this_month),
            "daysPlayed": len(all_days), "totalAnswers": total,
            "reliableDays": len(solid_all), "trend": trend,
            "minSample": PROGRESS_MIN_SAMPLE,
            "evaluation": _progress_evaluation(all_days, solid_all, trend, total)}


def _progress_evaluation(days, solid, trend, total):
    """Say which way it is going, and refuse to when it cannot be known."""
    if len(days) < 2:
        return (f"Only one day of play so far ({total} answers). "
                f"Come back after another session and this will show a direction.")
    if trend is None or len(solid) < 2:
        return (f"{len(days)} days played, {total} answers, but only "
                f"{len(solid)} day{'' if len(solid) == 1 else 's'} with at least "
                f"{PROGRESS_MIN_SAMPLE} answers. Not enough to call a "
                f"direction yet - short days swing too hard to compare.")

    acc_delta = (trend["accuracyTo"] or 0) - (trend["accuracyFrom"] or 0)
    parts = []
    if abs(acc_delta) < 5:
        parts.append(f"Accuracy is holding around {trend['accuracyTo']}%")
    else:
        parts.append(f"Accuracy has gone {trend['accuracyFrom']}% → "
                     f"{trend['accuracyTo']}%")

    # Celerity is the interesting half: it is the thing the player can change
    # on purpose, and which way it moved explains most accuracy swings on a
    # profile this size.
    cel_from, cel_to = trend["celerityFrom"], trend["celerityTo"]
    if cel_from is not None and cel_to is not None:
        cel_delta = cel_to - cel_from
        if abs(cel_delta) >= 0.05:
            earlier = cel_delta > 0        # higher celerity = more left unread
            moved = "earlier" if earlier else "later"
            parts.append(f"and you're buzzing {moved} "
                         f"({cel_from:.2f} → {cel_to:.2f} of the question unread)")
            if earlier and acc_delta <= -5:
                parts.append("— those move together, which is the early-buzz "
                             "habit costing you questions, not bad luck")
            elif not earlier and acc_delta >= 5:
                parts.append("— waiting longer is converting more, so keep doing that")
            elif earlier and acc_delta >= 5:
                parts.append("— buzzing earlier and still converting, which is "
                             "the direction you want")
        else:
            parts.append(f"with your buzz point steady around {cel_to:.2f}")

    gate = (f" Across all {trend['daysCompared']} days with at least "
            f"{PROGRESS_MIN_SAMPLE} answers ({trend['firstDay']} to "
            f"{trend['lastDay']}); shorter days are shown but not compared.")
    return " ".join(parts) + "." + gate
