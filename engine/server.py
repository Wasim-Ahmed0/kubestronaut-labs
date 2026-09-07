"""The dashboard server.

Stdlib only: it serves the single-page dashboard and a small JSON API
that both the browser and the terminal-side runner talk to.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import grader
import labs as labmod
import store

WEB = os.path.join(labmod.ROOT, "web")
TOKEN_FILE = os.path.join(store.RUNTIME, "token")

_state_lock = threading.RLock()
_version = {"n": 0}


def bump():
    with _state_lock:
        _version["n"] += 1


def get_token():
    store.ensure_dirs()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as fh:
            tok = fh.read().strip()
            if tok:
                return tok
    tok = secrets.token_urlsafe(18)
    with open(TOKEN_FILE, "w") as fh:
        fh.write(tok)
    os.chmod(TOKEN_FILE, 0o600)
    return tok


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Catalogue:
    """Loads the labs once and refreshes them if the files change."""

    def __init__(self):
        self.labs = []
        self.by_id = {}
        self.error = None
        self.reload()

    def reload(self):
        try:
            self.labs = labmod.load_catalogue()
            self.by_id = {l["id"]: l for l in self.labs}
            self.error = None
        except Exception as e:  # a broken lab file should not kill the server
            self.error = str(e)
        return self.error


CAT = Catalogue()


# --------------------------------------------------------------- analytics

def _attempt_view(a):
    keep = ("n", "started_at", "ended_at", "duration_s", "outcome", "checks",
            "checks_total", "checks_passed", "verify_runs", "hints_used",
            "hint_cost", "score", "breakdown", "notes", "solution",
            "commands", "verdict", "solution_stats")
    return {k: a.get(k) for k in keep}


def lab_state(data, lab_id):
    rec = data["labs"].get(lab_id)
    if not rec:
        return {"status": "not_started", "best_score": None, "attempts": 0,
                "last_score": None, "progress": 0.0, "bookmarked": False,
                "total_time_s": 0}
    attempts = rec.get("attempts") or []
    last = attempts[-1] if attempts else None
    progress = 0.0
    if last and last.get("checks_total"):
        progress = (last.get("checks_passed", 0) or 0) / last["checks_total"]
    return {
        "status": rec.get("status", "not_started"),
        "best_score": rec.get("best_score"),
        "last_score": last.get("score") if last else None,
        "attempts": len(attempts),
        "progress": round(progress, 3),
        "bookmarked": rec.get("bookmarked", False),
        "total_time_s": rec.get("total_time_s", 0),
        "last_activity_at": rec.get("last_activity_at"),
    }


def build_progress(data):
    """Everything the Progress page renders."""
    labs = CAT.labs
    per_cat, per_exam, per_diff, per_concept = {}, {}, {}, {}
    completed = in_progress = 0
    total_time = 0
    scores = []
    timeline = []

    for lab in labs:
        st = lab_state(data, lab["id"])
        done = st["status"] == "completed"
        if done:
            completed += 1
        elif st["status"] == "in_progress":
            in_progress += 1
        total_time += st["total_time_s"]
        if st["best_score"] is not None:
            scores.append(st["best_score"])

        buckets = [(per_cat, lab["category"])]
        buckets += [(per_exam, e) for e in lab["exams"]]
        buckets += [(per_diff, lab["difficulty"])]
        buckets += [(per_concept, c) for c in lab["concepts"]]
        for target, key in buckets:
            b = target.setdefault(key, {"total": 0, "completed": 0, "scores": []})
            b["total"] += 1
            if done:
                b["completed"] += 1
            if st["best_score"] is not None:
                b["scores"].append(st["best_score"])

    for target in (per_cat, per_exam, per_diff, per_concept):
        for b in target.values():
            b["avg_score"] = round(sum(b["scores"]) / len(b["scores"])) if b["scores"] else None
            b["pct"] = round(100.0 * b["completed"] / b["total"]) if b["total"] else 0
            b.pop("scores")

    for lab_id, rec in data["labs"].items():
        lab = CAT.by_id.get(lab_id)
        for a in rec.get("attempts") or []:
            if a.get("ended_at"):
                timeline.append({
                    "lab_id": lab_id,
                    "title": lab["title"] if lab else lab_id,
                    "category": lab["category"] if lab else "",
                    "exams": lab["exams"] if lab else [],
                    "at": a["ended_at"], "score": a.get("score"),
                    "duration_s": a.get("duration_s"), "outcome": a.get("outcome"),
                    "attempt": a.get("n"),
                })
    timeline.sort(key=lambda x: x["at"] or "", reverse=True)

    # exam readiness: coverage x quality, so a high score on 3 of 40 labs
    # does not read as ready
    readiness = {}
    for exam, b in per_exam.items():
        coverage = b["completed"] / b["total"] if b["total"] else 0
        quality = (b["avg_score"] or 0) / 100.0
        readiness[exam] = {
            "coverage": round(coverage * 100),
            "avg_score": b["avg_score"],
            "pass_mark": grader.PASS_MARKS.get(exam),
            "readiness": round(100 * coverage * quality),
            "total": b["total"], "completed": b["completed"],
        }

    weak = sorted(
        [{"concept": k, **v} for k, v in per_concept.items()
         if v["avg_score"] is not None],
        key=lambda x: x["avg_score"])[:12]
    untouched = sorted(
        [{"concept": k, **v} for k, v in per_concept.items() if v["completed"] == 0],
        key=lambda x: -x["total"])[:12]

    days = {}
    for t in timeline:
        days[(t["at"] or "")[:10]] = days.get((t["at"] or "")[:10], 0) + 1

    return {
        "totals": {
            "labs": len(labs), "completed": completed, "in_progress": in_progress,
            "not_started": len(labs) - completed - in_progress,
            "pct": round(100.0 * completed / len(labs)) if labs else 0,
            "total_time_s": total_time,
            "avg_score": round(sum(scores) / len(scores)) if scores else None,
            "attempts": sum(len(r.get("attempts") or []) for r in data["labs"].values()),
        },
        "by_category": per_cat, "by_exam": per_exam, "by_difficulty": per_diff,
        "by_concept": per_concept, "readiness": readiness,
        "weak_concepts": weak, "untouched_concepts": untouched,
        "timeline": timeline[:60], "activity_days": days,
    }


# --------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "klab"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("KLAB_DEBUG"):
            super().log_message(fmt, *args)

    # ---- helpers
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str), "application/json")

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _authorised(self, qs):
        host = self.client_address[0]
        if host in ("127.0.0.1", "::1", "localhost"):
            return True
        tok = (self.headers.get("X-Klab-Token")
               or (qs.get("t", [None])[0] if qs else None))
        return tok == self.server.token

    # ---- routing
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path
        if path.startswith("/api/"):
            if not self._authorised(qs):
                return self._err(403, "bad or missing access token")
            return self._api_get(path, qs)
        return self._static(path)

    do_HEAD = do_GET

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not u.path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._authorised(qs):
            return self._err(403, "bad or missing access token")
        return self._api_post(u.path, self._body())

    # ---- static
    def _static(self, path):
        if path in ("/", "/index.html"):
            rel = "index.html"
        else:
            rel = path.lstrip("/")
        target = os.path.normpath(os.path.join(WEB, rel))
        if not target.startswith(WEB) or not os.path.isfile(target):
            target = os.path.join(WEB, "index.html")  # SPA fallback
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype)

    # ---- API GET
    def _api_get(self, path, qs):
        data = store.load()
        if path == "/api/pulse":
            sess = data.get("session")
            return self._json({
                "v": _version["n"],
                "session": sess,
                "pending": bool(data.get("pending_start")),
                "runner": self.server.runner_seen_recently(),
            })
        if path == "/api/catalogue":
            if CAT.error:
                return self._json({"error": CAT.error, "labs": []}, 500)
            return self._json({
                "labs": [dict(labmod.summary(l), state=lab_state(data, l["id"]))
                         for l in CAT.labs],
                "categories": labmod.categories(CAT.labs),
                "exams": labmod.EXAMS,
                "difficulties": labmod.DIFFICULTIES,
                "pass_marks": grader.PASS_MARKS,
                "session": data.get("session"),
                "runner": self.server.runner_seen_recently(),
            })
        if path.startswith("/api/lab/"):
            lab_id = path[len("/api/lab/"):]
            lab = CAT.by_id.get(lab_id)
            if not lab:
                return self._err(404, "no such lab")
            rec = data["labs"].get(lab_id, {})
            public = {k: v for k, v in lab.items() if k not in ("verify",)}
            public["checks"] = [{"id": c["id"], "desc": c["desc"], "points": c["points"]}
                                for c in lab["verify"]]
            return self._json({
                "lab": public,
                "state": lab_state(data, lab_id),
                "attempts": [_attempt_view(a) for a in (rec.get("attempts") or [])],
                "session": data.get("session"),
            })
        if path == "/api/progress":
            return self._json(build_progress(data))
        if path == "/api/session":
            return self._json({"session": data.get("session"),
                               "runner": self.server.runner_seen_recently()})
        if path == "/api/pending":
            self.server.mark_runner()
            return self._json({"pending": data.get("pending_start")})
        if path == "/api/reload":
            err = CAT.reload()
            bump()
            return self._json({"ok": not err, "error": err, "labs": len(CAT.labs)})
        return self._err(404, "unknown endpoint")

    # ---- API POST
    def _api_post(self, path, body):
        data = store.load()

        if path == "/api/start":
            lab_id = body.get("lab_id")
            if lab_id not in CAT.by_id:
                return self._err(404, "no such lab")
            if data.get("session") and data["session"].get("state") == "running":
                return self._err(409, "a lab is already running in your terminal")
            data["pending_start"] = {
                "lab_id": lab_id, "requested_at": store.now_iso(),
                "fresh": bool(body.get("fresh")),
                "reattempt": bool(body.get("reattempt")),
            }
            store.save(data)
            bump()
            return self._json({"ok": True, "pending": data["pending_start"]})

        if path == "/api/cancel-pending":
            data["pending_start"] = None
            store.save(data)
            bump()
            return self._json({"ok": True})

        if path == "/api/notes":
            lab_id, n = body.get("lab_id"), body.get("attempt")
            rec = data["labs"].get(lab_id)
            if not rec:
                return self._err(404, "no attempts for that lab yet")
            for a in rec["attempts"]:
                if a["n"] == n:
                    a["notes"] = body.get("notes", "")[:20000]
                    store.save(data)
                    bump()
                    return self._json({"ok": True})
            return self._err(404, "no such attempt")

        if path == "/api/bookmark":
            lab_id = body.get("lab_id")
            if lab_id not in CAT.by_id:
                return self._err(404, "no such lab")
            rec = data["labs"].setdefault(lab_id, {
                "status": "not_started", "best_score": None, "attempts": [],
                "first_started_at": None, "last_activity_at": None,
                "total_time_s": 0, "bookmarked": False})
            rec["bookmarked"] = bool(body.get("bookmarked"))
            store.save(data)
            bump()
            return self._json({"ok": True})


        if path == "/api/session/verify":
            lab_id, n = body.get("lab_id"), body.get("attempt")
            lab = CAT.by_id.get(lab_id)
            rec = data["labs"].get(lab_id)
            if not lab or not rec:
                return self._err(404, "no such lab attempt")
            att = next((a for a in rec["attempts"] if a["n"] == n), None)
            if att is None:
                return self._err(404, "no such attempt")
            checks = body.get("checks") or []
            att["checks"] = checks
            att["checks_passed"] = sum(1 for c in checks if c.get("passed"))
            att["checks_total"] = len(checks) or lab and len(lab["verify"])
            if body.get("count_run"):
                att["verify_runs"] = att.get("verify_runs", 0) + 1
            att["duration_s"] = int(time.time() - att["started_ts"])
            rec["last_activity_at"] = store.now_iso()
            sess = data.get("session") or {}
            if sess.get("lab_id") == lab_id:
                sess["checks"] = checks
                sess["progress"] = round(
                    att["checks_passed"] / max(1, att["checks_total"]), 3)
                sess["duration_s"] = att["duration_s"]
                data["session"] = sess
            store.save(data)
            bump()
            return self._json({"ok": True, "passed": att["checks_passed"],
                               "total": att["checks_total"]})

        if path == "/api/session/hint":
            lab_id, n, idx = body.get("lab_id"), body.get("attempt"), body.get("index")
            lab = CAT.by_id.get(lab_id)
            rec = data["labs"].get(lab_id)
            if not lab or not rec:
                return self._err(404, "no such lab attempt")
            att = next((a for a in rec["attempts"] if a["n"] == n), None)
            if att is None or idx is None or idx >= len(lab["hints"]):
                return self._err(404, "no such hint")
            if idx not in att["hints_used"]:
                att["hints_used"].append(idx)
                att["hint_cost"] = sum(lab["hints"][i]["cost"] for i in att["hints_used"])
            sess = data.get("session") or {}
            if sess.get("lab_id") == lab_id:
                sess["hints_used"] = att["hints_used"]
                data["session"] = sess
            store.save(data)
            bump()
            return self._json({"ok": True, "hint": lab["hints"][idx],
                               "used": att["hints_used"], "cost": att["hint_cost"]})

        if path == "/api/session/finish":
            lab_id, n = body.get("lab_id"), body.get("attempt")
            lab = CAT.by_id.get(lab_id)
            rec = data["labs"].get(lab_id)
            if not lab or not rec:
                return self._err(404, "no such lab attempt")
            att = next((a for a in rec["attempts"] if a["n"] == n), None)
            if att is None:
                return self._err(404, "no such attempt")
            checks = body.get("checks") or att.get("checks") or []
            outcome = body.get("outcome", "passed")
            att["checks"] = checks
            att["checks_passed"] = sum(1 for c in checks if c.get("passed"))
            att["checks_total"] = len(checks)
            att["duration_s"] = int(body.get("duration_s")
                                    or (time.time() - att["started_ts"]))
            att["ended_at"] = store.now_iso()
            att["outcome"] = outcome
            att["solution"] = body.get("solution", "")
            att["commands"] = body.get("commands", [])
            att["solution_stats"] = body.get("solution_stats", {})
            score, breakdown = grader.grade(
                checks=checks, duration_s=att["duration_s"],
                par_minutes=lab["par_minutes"], hint_cost=att.get("hint_cost", 0),
                verify_runs=att.get("verify_runs", 0), attempt_n=att["n"],
                hint_cost_total=lab["hint_cost_total"])
            att["score"] = score
            att["breakdown"] = breakdown
            att["verdict"] = grader.verdict(score, lab["exams"])
            rec["total_time_s"] = rec.get("total_time_s", 0) + att["duration_s"]
            rec["last_activity_at"] = att["ended_at"]
            if outcome == "passed":
                rec["status"] = "completed"
                rec["best_score"] = max(rec.get("best_score") or 0, score)
            else:
                rec["status"] = "completed" if rec.get("status") == "completed" \
                    else "in_progress"
            data["session"] = None
            store.save(data)
            bump()
            return self._json({"ok": True, "score": score, "breakdown": breakdown,
                               "verdict": att["verdict"]})

        # ---- called by the terminal runner
        if path == "/api/session/claim":
            pending = data.get("pending_start")
            data["pending_start"] = None
            store.save(data)
            bump()
            self.server.mark_runner()
            return self._json({"claimed": pending})

        if path == "/api/session/update":
            data["session"] = body.get("session")
            if body.get("labs"):
                data["labs"].update(body["labs"])
            store.save(data)
            bump()
            self.server.mark_runner()
            return self._json({"ok": True})

        if path == "/api/session/heartbeat":
            self.server.mark_runner()
            return self._json({"ok": True, "v": _version["n"]})

        if path == "/api/reload":
            err = CAT.reload()
            bump()
            return self._json({"ok": not err, "error": err})

        return self._err(404, "unknown endpoint")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, token):
        super().__init__(addr, Handler)
        self.token = token
        self._runner_ts = 0.0

    def mark_runner(self):
        self._runner_ts = time.time()

    def runner_seen_recently(self):
        return (time.time() - self._runner_ts) < 15


def serve(host="127.0.0.1", port=9000):
    token = get_token()
    last_err = None
    for p in range(port, port + 25):
        try:
            httpd = Server((host, p), token)
            return httpd, p, token
        except OSError as e:
            last_err = e
            continue
    raise SystemExit("could not bind a port in %d-%d: %s" % (port, port + 25, last_err))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()
    httpd, port, token = serve(args.host, args.port)
    print("http://%s:%d/?t=%s" % (args.host, port, token), flush=True)
    httpd.serve_forever()
