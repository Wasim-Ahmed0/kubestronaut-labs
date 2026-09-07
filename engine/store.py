"""Persistent state for the lab platform.

Everything lives under ~/.kubestronaut so that reinstalling or moving the
repo never loses progress.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("KLAB_HOME", "~/.kubestronaut"))
STATE = os.path.join(HOME, "state.json")
ATTEMPTS = os.path.join(HOME, "attempts")
RUNTIME = os.path.join(HOME, "runtime")
LOGS = os.path.join(HOME, "logs")

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for d in (HOME, ATTEMPTS, RUNTIME, LOGS):
        os.makedirs(d, exist_ok=True)


def _blank():
    return {"version": 1, "created_at": now_iso(), "labs": {}, "session": None,
            "pending_start": None, "settings": {}}


def load() -> dict:
    ensure_dirs()
    with _lock:
        if not os.path.exists(STATE):
            return _blank()
        try:
            with open(STATE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # never let a corrupt file block a study session
            if os.path.exists(STATE):
                shutil.copy(STATE, STATE + ".corrupt-%d" % int(time.time()))
            return _blank()
        for key, default in _blank().items():
            data.setdefault(key, default)
        return data


def save(data: dict) -> None:
    ensure_dirs()
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=HOME, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=False)
            os.replace(tmp, STATE)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class Store:
    """Thin transactional wrapper so callers never race on the JSON file."""

    def __init__(self):
        ensure_dirs()
        self._data = load()

    @property
    def data(self) -> dict:
        return self._data

    def reload(self) -> dict:
        self._data = load()
        return self._data

    def commit(self) -> None:
        save(self._data)

    def lab(self, lab_id: str) -> dict:
        return self._data["labs"].setdefault(
            lab_id,
            {"status": "not_started", "best_score": None, "attempts": [],
             "first_started_at": None, "last_activity_at": None,
             "total_time_s": 0, "bookmarked": False},
        )

    # ---------------- attempts ----------------

    def start_attempt(self, lab_id: str, total_checks: int) -> dict:
        rec = self.lab(lab_id)
        n = len(rec["attempts"]) + 1
        attempt = {
            "n": n,
            "started_at": now_iso(),
            "started_ts": time.time(),
            "ended_at": None,
            "duration_s": 0,
            "outcome": "in_progress",
            "checks": [],
            "checks_total": total_checks,
            "checks_passed": 0,
            "verify_runs": 0,
            "hints_used": [],
            "hint_cost": 0,
            "score": None,
            "breakdown": None,
            "notes": "",
            "commands": [],
            "solution": "",
        }
        rec["attempts"].append(attempt)
        rec["status"] = "in_progress"
        rec["first_started_at"] = rec["first_started_at"] or attempt["started_at"]
        rec["last_activity_at"] = attempt["started_at"]
        self._data["session"] = {
            "lab_id": lab_id,
            "attempt": n,
            "started_ts": attempt["started_ts"],
            "state": "running",
            "progress": 0.0,
            "checks": [],
            "hints_used": [],
        }
        self.commit()
        return attempt

    def current_attempt(self, lab_id: str):
        rec = self.lab(lab_id)
        return rec["attempts"][-1] if rec["attempts"] else None

    def attempt_dir(self, lab_id: str, n: int) -> str:
        d = os.path.join(ATTEMPTS, lab_id, "attempt-%02d" % n)
        os.makedirs(d, exist_ok=True)
        return d
