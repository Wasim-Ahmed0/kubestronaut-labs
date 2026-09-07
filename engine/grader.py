"""Turn a lab attempt into an exam-confidence score out of 100.

The number answers one question: *if this single lab had been the whole
exam, how confident am I that you would have passed?*  So it is not just
"did the YAML end up correct" -- speed, hint reliance, how many wrong
verifies you burned and whether you had seen the lab before all move it.
"""
from __future__ import annotations

# Official-ish cut scores, used to phrase the verdict.
PASS_MARKS = {"KCNA": 75, "KCSA": 75, "CKA": 66, "CKAD": 66, "CKS": 67}

W_CORRECT = 70.0   # did you actually produce the required end state
W_TIME = 15.0      # exams are time-boxed; slow is a real risk
W_HINTS = 10.0     # you get no hints on exam day
W_FLOW = 5.0       # thrashing on verify signals shaky recall


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def time_factor(duration_s: float, par_minutes: float) -> float:
    """1.0 at or under par, decaying to 0 at three times par."""
    if not par_minutes or par_minutes <= 0:
        return 1.0
    mins = duration_s / 60.0
    if mins <= par_minutes:
        return 1.0
    over = (mins - par_minutes) / (2.0 * par_minutes)
    return _clamp(1.0 - over)


def grade(*, checks, duration_s, par_minutes, hint_cost, verify_runs,
          attempt_n=1, hint_cost_total=0):
    """``checks`` is a list of dicts with ``points`` and ``passed``."""
    total_pts = sum(c.get("points", 1) for c in checks) or 1
    earned = sum(c.get("points", 1) for c in checks if c.get("passed"))
    correctness = earned / total_pts

    tf = time_factor(duration_s, par_minutes)

    hint_scale = float(hint_cost_total or 100)
    hint_use = _clamp(hint_cost / hint_scale) if hint_scale else 0.0
    hf = 1.0 - hint_use

    # first verify is free; each further failed run chips away at flow
    wasted = max(0, verify_runs - 1)
    ff = _clamp(1.0 - 0.12 * wasted)

    raw = (W_CORRECT * correctness + W_TIME * tf +
           W_HINTS * hf + W_FLOW * ff)

    # Repeats are easier than the first sight of a problem; discount a little
    # so the number keeps meaning "could you do this cold".
    familiarity = 0.0
    if attempt_n == 2:
        familiarity = 3.0
    elif attempt_n >= 3:
        familiarity = min(8.0, 3.0 + 1.5 * (attempt_n - 2))
    score = raw - familiarity

    # An incomplete solution can never read as exam-ready.
    if correctness < 1.0:
        score = min(score, 65.0 * correctness + 10.0)

    score = int(round(_clamp(score, 0.0, 100.0)))

    breakdown = {
        "correctness": {
            "weight": W_CORRECT, "earned": round(W_CORRECT * correctness, 1),
            "detail": "%d/%d check points" % (earned, total_pts)},
        "time": {
            "weight": W_TIME, "earned": round(W_TIME * tf, 1),
            "detail": "%.1f min vs %.0f min par" % (duration_s / 60.0, par_minutes or 0)},
        "hints": {
            "weight": W_HINTS, "earned": round(W_HINTS * hf, 1),
            "detail": "%d hint points used" % hint_cost},
        "flow": {
            "weight": W_FLOW, "earned": round(W_FLOW * ff, 1),
            "detail": "%d failed verify run(s)" % wasted},
        "familiarity_penalty": round(-familiarity, 1),
        "attempt": attempt_n,
    }
    return score, breakdown


def verdict(score: int, exams) -> dict:
    """Human-readable confidence plus a per-exam pass/fail read."""
    if score >= 90:
        label, blurb = "Exam ready", "Clean, fast and unaided. You would pass this cold."
    elif score >= 80:
        label, blurb = "Likely pass", "Solid. Tighten speed or drop the hints and you're there."
    elif score >= 70:
        label, blurb = "Borderline", "You got there, but not with exam margin. Re-run this one."
    elif score >= 50:
        label, blurb = "Needs work", "Too slow or too assisted. Review the topic, then reattempt."
    else:
        label, blurb = "Not ready", "Study this topic properly before moving on."

    per_exam = {}
    for ex in exams or []:
        mark = PASS_MARKS.get(ex)
        if mark is None:
            continue
        per_exam[ex] = {
            "pass_mark": mark,
            "would_pass": score >= mark,
            "margin": score - mark,
        }
    return {"label": label, "blurb": blurb, "per_exam": per_exam}
