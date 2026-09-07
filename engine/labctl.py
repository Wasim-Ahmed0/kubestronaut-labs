#!/usr/bin/env python3
"""klab -- the terminal side of the lab platform.

`labctl.py run` starts the dashboard and then waits.  When you press
Start Lab in the browser it builds the cluster, seeds the scenario and
drops you into a lab shell.  The other subcommands are the helpers that
shell exposes (verify / hint / submit / ...).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cluster  # noqa: E402
import labs as labmod  # noqa: E402
import recorder  # noqa: E402
import server as srv  # noqa: E402
import store  # noqa: E402
import ui  # noqa: E402

ACTIVE = os.path.join(store.RUNTIME, "active.json")
ENDPOINT = os.path.join(store.RUNTIME, "endpoint.json")


# ----------------------------------------------------------------- API client

def endpoint():
    try:
        with open(ENDPOINT) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def api(method, path, payload=None, timeout=120):
    ep = endpoint()
    if not ep:
        raise RuntimeError("the klab dashboard is not running -- start it with ./klab")
    url = "http://127.0.0.1:%d%s" % (ep["port"], path)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Klab-Token": ep["token"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": body or str(e)}


def read_active():
    try:
        with open(ACTIVE) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def write_active(obj):
    os.makedirs(store.RUNTIME, exist_ok=True)
    if obj is None:
        if os.path.exists(ACTIVE):
            os.unlink(ACTIVE)
        return
    with open(ACTIVE, "w") as fh:
        json.dump(obj, fh, indent=1)


def load_lab(lab_id):
    for lab in labmod.load_catalogue():
        if lab["id"] == lab_id:
            return lab
    raise SystemExit("unknown lab: %s" % lab_id)


# ----------------------------------------------------------------- lab shell

RC_TEMPLATE = r"""
# klab lab shell -- generated, do not edit
export KUBECONFIG="__KUBECONFIG__"
export KLAB_REAL_PATH="__REALPATH__"
export KLAB_LOG="__LOG__"
export PATH="__SHIM__:$PATH"
export KLAB_LAB_ID="__LABID__"

__klab() { "__PYTHON__" "__LABCTL__" "$@"; }

verify()   { __klab verify "$@"; }
check()    { __klab verify "$@"; }
hint()     { __klab hint "$@"; }
tasks()    { __klab tasks "$@"; }
brief()    { __klab tasks "$@"; }
progress() { __klab status "$@"; }
answer()   { __klab solution "$@"; }
helpme()   { __klab shellhelp "$@"; }

submit() {
  __klab finish "$@"
  if [ $? -eq 0 ]; then
    exit 0
  fi
  return 1
}
finish() { submit "$@"; }

giveup() {
  __klab giveup "$@"
  if [ $? -eq 0 ]; then exit 0; fi
  return 1
}

knode() {
  local n="$1"; shift
  if [ -z "$n" ]; then
    docker ps --filter "label=io.x-k8s.kind.cluster=__CLUSTER__" --format '{{.Names}}'
    return 0
  fi
  if [ $# -eq 0 ]; then docker exec -it "$n" bash; else docker exec -it "$n" "$@"; fi
}

alias k=kubectl
alias kn='kubectl config set-context --current --namespace'
export do='--dry-run=client -o yaml'
export now='--grace-period=0 --force'

if [ -n "$BASH_VERSION" ]; then
  source <(kubectl completion bash 2>/dev/null) 2>/dev/null
  complete -o default -F __start_kubectl k 2>/dev/null
fi

PS1='\[\033[36m\]__LABID__\[\033[0m\] \[\033[90m\]\w\[\033[0m\] \[\033[35m\]❯\[\033[0m\] '
cd "__WORKDIR__" 2>/dev/null || true
__klab shellhelp --compact
"""


def build_rc(lab, workdir, log_path):
    real_path = os.environ.get("KLAB_REAL_PATH") or os.environ.get("PATH", "")
    real_path = os.pathsep.join(
        p for p in real_path.split(os.pathsep) if p and p != recorder.SHIM_DIR)
    if cluster.BINDIR not in real_path.split(os.pathsep):
        real_path = cluster.BINDIR + os.pathsep + real_path
    shim = recorder.install_shims(real_path)
    rc = (RC_TEMPLATE
          .replace("__KUBECONFIG__", cluster.KUBECONFIG)
          .replace("__REALPATH__", real_path)
          .replace("__LOG__", log_path)
          .replace("__SHIM__", shim)
          .replace("__LABID__", lab["id"])
          .replace("__PYTHON__", sys.executable)
          .replace("__LABCTL__", os.path.join(HERE, "labctl.py"))
          .replace("__WORKDIR__", workdir)
          .replace("__CLUSTER__", cluster.CLUSTER_NAME))
    path = os.path.join(store.RUNTIME, "labrc.sh")
    with open(path, "w") as fh:
        fh.write(rc)
    return path


def print_brief(lab, attempt_n):
    ui.banner("%s  ·  %s" % (lab["id"], lab["title"]),
              "%s · %s · %s · par %d min"
              % (lab["category"], lab["difficulty"],
                 "/".join(lab["exams"]), lab["par_minutes"]))
    print(ui.wrap(lab["scenario"].strip()))
    if lab.get("tasks"):
        ui.head("  Tasks")
        for i, t in enumerate(lab["tasks"], 1):
            print(ui.wrap("%d. %s" % (i, t["text"]), indent="  "))
    if lab.get("docs"):
        ui.head("  Docs you are allowed to use in the real exam")
        for d in lab["docs"]:
            ui.dim(d)
    print()
    ui.kv("attempt", "#%d" % attempt_n)
    ui.kv("checks", "%d worth %d points" % (len(lab["verify"]), lab["points_total"]))
    ui.kv("hints", "%d available" % len(lab["hints"]))
    print(ui.rule())


def shell_help(compact=False):
    rows = [
        ("verify", "run the checks and see what still fails        (alias: check)"),
        ("tasks", "reprint the scenario and the task list         (alias: brief)"),
        ("hint", "list hints · hint 1  reveals one (costs points)"),
        ("progress", "timer, checks passed, hints used, your workdir"),
        ("submit", "verify, then finish and be graded              (alias: finish)"),
        ("giveup", "stop here and record a partial attempt"),
        ("answer", "show the reference solution (marks it assisted)"),
        ("knode", "list nodes · knode <name>  shells into one"),
        ("helpme", "print this list again"),
        ("", ""),
        ("k", "kubectl · kn <ns> switches namespace"),
        ("$do", "--dry-run=client -o yaml · $now = --grace-period=0 --force"),
    ]
    if compact:
        print()
        parts = ui.CYAN + "verify" + ui.RESET + ui.GREY + " · " + ui.RESET
        parts += ui.CYAN + "hint" + ui.RESET + ui.GREY + " · " + ui.RESET
        parts += ui.CYAN + "tasks" + ui.RESET + ui.GREY + " · " + ui.RESET
        parts += ui.CYAN + "progress" + ui.RESET + ui.GREY + " · " + ui.RESET
        parts += ui.CYAN + "submit" + ui.RESET + ui.GREY + " · " + ui.RESET
        parts += ui.CYAN + "helpme" + ui.RESET
        print("  " + parts + ui.GREY + "   (you are in the lab shell)" + ui.RESET)
        print()
        return
    ui.head("  Lab shell commands")
    for name, desc in rows:
        print("  " + ui.CYAN + name.ljust(10) + ui.RESET + desc)
    print()


# ----------------------------------------------------------------- run loop

def preflight(fix=True):
    problems = []
    if not cluster.which("kubectl"):
        problems.append("kubectl is not installed "
                        "(brew install kubectl, or see kubernetes.io/docs/tasks/tools)")
    if not cluster.which("docker"):
        problems.append("docker is not installed -- Docker Desktop, Colima, "
                        "OrbStack or podman-with-docker-cli all work")
    elif fix:
        okd, info = cluster.ensure_docker_running(cb=lambda m: print(m, flush=True))
        if not okd:
            problems.append(info + " -- start Docker and try again")
    else:
        okd, info = cluster.docker_ok()
        if not okd:
            problems.append(info + " -- start Docker and try again")
    if not cluster.which("kind") and fix:
        try:
            ui.info("kind is missing; fetching it into ~/.kubestronaut/bin")
            cluster.install_kind()
            ui.ok("kind installed")
        except Exception as e:
            problems.append("could not install kind automatically: %s" % e)
    return problems


def provision(lab, fresh=False):
    """Build (or recycle) the cluster. The scenario is seeded separately, so
    that neither the build nor the seeding is charged to your timer."""
    spec = lab["cluster"]
    ui.head("  Building your cluster")
    if lab.get("requires_tools"):
        missing = [t for t in lab["requires_tools"] if not cluster.which(t)]
        if missing:
            ui.info("this lab needs %s; fetching into ~/.kubestronaut/bin"
                    % ", ".join(missing))
        cluster.ensure_extra_tools(lab["requires_tools"],
                                   cb=lambda m: print(m, flush=True))
    mode = cluster.ensure_cluster(spec, force_fresh=fresh,
                                  cb=lambda m: print(m, flush=True))
    if lab.get("destructive"):
        cluster.mark_dirty()
    ui.ok("cluster ready (%s)" % mode)
    return mode


def progress_poller(stop_event, lab, lab_id, attempt_n, interval=30):
    """Quietly re-run the checks so the browser shows a live progress bar."""
    while not stop_event.wait(interval):
        try:
            if os.path.exists(os.path.join(store.RUNTIME, "verify.lock")):
                continue
            checks = cluster.run_checks(lab, timeout_each=25)
            api("POST", "/api/session/verify",
                {"lab_id": lab_id, "attempt": attempt_n, "checks": checks,
                 "count_run": False}, timeout=90)
        except Exception:
            pass  # a background nicety must never break the lab


def start_lab(lab_id, fresh=False):
    lab = load_lab(lab_id)

    unmet = [r for r in lab["requires"]
             if (r == "linux" and sys.platform != "linux")]
    if unmet:
        ui.warn("this lab needs %s; parts of it will not behave correctly here"
                % ", ".join(unmet))

    try:
        provision(lab, fresh=fresh)
    except cluster.ClusterError as e:
        ui.bad(str(e))
        api("POST", "/api/session/update", {"session": None})
        return

    st = store.Store()
    # Seed the scenario into a working directory before the clock starts.
    next_n = len(st.lab(lab_id).get("attempts") or []) + 1
    adir = st.attempt_dir(lab_id, next_n)
    workdir = os.path.join(adir, "work")
    os.makedirs(workdir, exist_ok=True)
    if lab.get("setup"):
        ui.info("seeding the scenario")
        try:
            cluster.apply_setup(lab, cb=lambda m: print(m, flush=True),
                                workdir=workdir)
        except cluster.ClusterError as e:
            ui.bad(str(e))
            api("POST", "/api/session/update", {"session": None})
            return

    attempt = st.start_attempt(lab_id, len(lab["verify"]))
    api("POST", "/api/session/update", {
        "session": {"lab_id": lab_id, "attempt": attempt["n"],
                    "started_ts": attempt["started_ts"], "state": "running",
                    "progress": 0.0, "checks": [], "hints_used": [],
                    "title": lab["title"], "category": lab["category"],
                    "exams": lab["exams"], "par_minutes": lab["par_minutes"]},
        "labs": {lab_id: st.lab(lab_id)}})

    log_path = os.path.join(adir, "commands.log")
    open(log_path, "a").close()

    write_active({"lab_id": lab_id, "attempt": attempt["n"],
                  "started_ts": attempt["started_ts"], "log": log_path,
                  "workdir": workdir, "dir": adir})

    print_brief(lab, attempt["n"])
    ui.info("dropping you into the lab shell -- type %ssubmit%s when you are done"
            % (ui.BOLD, ui.RESET))

    stop = threading.Event()
    poller = threading.Thread(target=progress_poller,
                              args=(stop, lab, lab_id, attempt["n"]), daemon=True)
    poller.start()

    rc = build_rc(lab, workdir, log_path)
    bash = shutil.which("bash") or "/bin/bash"
    env = dict(os.environ)
    env["KLAB_REAL_PATH"] = os.environ.get("PATH", "")
    env.pop("KLAB_LOG", None)
    # KLAB_TEST_SHELL runs a scripted session instead of an interactive one.
    # It exists so the platform's own smoke test can drive a whole lab.
    script = os.environ.get("KLAB_TEST_SHELL")
    if script:
        # Drive the same interactive shell the learner gets, feeding it a
        # scripted session on stdin, so the smoke test exercises the real
        # rcfile, the real PATH shim and the real helper functions.
        subprocess.run([bash, "--rcfile", rc, "-i"], env=env, cwd=workdir,
                       input=script, text=True)
    else:
        subprocess.call([bash, "--rcfile", rc, "-i"], env=env, cwd=workdir)
    stop.set()

    # the shell is gone: either submit/giveup already closed the attempt,
    # or the learner walked away mid-lab
    if read_active() is not None:
        record_partial(lab, lab_id, attempt["n"], reason="left the lab shell")
    write_active(None)
    show_return_hint()


def record_partial(lab, lab_id, attempt_n, reason=""):
    try:
        checks = cluster.run_checks(lab, timeout_each=25)
    except Exception:
        checks = []
    res = api("POST", "/api/session/finish", {
        "lab_id": lab_id, "attempt": attempt_n, "checks": checks,
        "outcome": "abandoned", "solution": "", "commands": []})
    passed = sum(1 for c in checks if c.get("passed"))
    ui.warn("attempt saved as unfinished (%s) -- %d/%d checks were passing"
            % (reason, passed, len(checks)))
    if res.get("score") is not None:
        ui.dim("your progress is kept; pick the lab again to carry on")


def show_return_hint():
    ep = endpoint()
    print(ui.rule())
    ui.info("back at the dashboard: %s" % (ep["url"] if ep else "(dashboard offline)"))
    ui.dim("pick another lab in the browser -- this terminal is waiting")


def wait_loop():
    ui.head("  Waiting for you to start a lab in the browser")
    ui.dim("press Ctrl-C here to quit")
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while True:
        try:
            res = api("GET", "/api/pending", timeout=20)
        except Exception:
            time.sleep(1)
            continue
        pending = res.get("pending")
        if pending:
            sys.stdout.write("\r" + " " * 60 + "\r")
            claimed = api("POST", "/api/session/claim", {}).get("claimed")
            if claimed:
                start_lab(claimed["lab_id"], fresh=claimed.get("fresh"))
                ui.head("  Waiting for you to start a lab in the browser")
            continue
        if sys.stdout.isatty():
            sys.stdout.write("\r  %s%s%s listening…" % (ui.CYAN, spinner[i % len(spinner)], ui.RESET))
            sys.stdout.flush()
            i += 1
        time.sleep(0.9)


def open_browser(url):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        for cmd in ("xdg-open", "gio", "sensible-browser", "x-www-browser"):
            exe = shutil.which(cmd)
            if exe:
                args = [exe, "open", url] if cmd == "gio" else [exe, url]
                subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
    except OSError:
        pass
    return False


def cmd_run(args):
    store.ensure_dirs()
    ui.banner("Kubestronaut lab platform",
              "KCNA · KCSA · CKA · CKAD · CKS  —  practice cluster on demand")

    err = srv.CAT.reload()
    if err:
        ui.bad("the lab catalogue failed to load: %s" % err)
        return 1
    ui.ok("%d labs loaded across %d categories"
          % (len(srv.CAT.labs), len(labmod.categories(srv.CAT.labs))))

    problems = preflight(fix=not args.no_install)
    if problems:
        ui.head("  Before you can run a lab")
        for p in problems:
            ui.bad(p)
        ui.dim("the dashboard still works -- fix the above, then start a lab")

    host = args.host or ("0.0.0.0" if args.remote else "127.0.0.1")
    httpd, port, token = srv.serve(host, args.port)
    display_host = "127.0.0.1" if host in ("127.0.0.1", "localhost") else srv.lan_ip()
    url = "http://%s:%d/?t=%s" % (display_host, port, token)
    with open(ENDPOINT, "w") as fh:
        json.dump({"port": port, "token": token, "url": url, "host": host}, fh)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    ui.head("  Dashboard")
    ui.kv("local", url)
    if host == "0.0.0.0":
        ui.kv("this machine", "http://%s:%d/?t=%s" % (srv.lan_ip(), port, token))
        ui.dim("open that on any device on the same network")
    else:
        ui.dim("headless box? re-run with --remote to serve it on your LAN,")
        ui.dim("or from your laptop: ssh -L %d:127.0.0.1:%d user@this-host" % (port, port))

    if not args.no_browser and host != "0.0.0.0":
        if open_browser(url):
            ui.ok("opened your browser")
        else:
            ui.warn("could not open a browser automatically -- use the link above")

    try:
        wait_loop()
    except KeyboardInterrupt:
        print()
        ui.info("stopping. your cluster is left running for next time"
                " (./klab cleanup removes it)")
    return 0


# ----------------------------------------------------------------- helpers

def _ctx():
    act = read_active()
    if not act:
        raise SystemExit("no lab is running in this shell")
    return act, load_lab(act["lab_id"])


def run_checks_for_active(quiet=False, count=True):
    """Run the active lab's checks. Returns the list of check results."""
    act, lab = _ctx()
    lockf = os.path.join(store.RUNTIME, "verify.lock")
    open(lockf, "w").close()
    try:
        if not quiet:
            ui.head("  Checking your work")
        checks = cluster.run_checks(lab)
    finally:
        if os.path.exists(lockf):
            os.unlink(lockf)

    api("POST", "/api/session/verify",
        {"lab_id": act["lab_id"], "attempt": act["attempt"], "checks": checks,
         "count_run": count})

    passed = [c for c in checks if c["passed"]]
    if not quiet:
        for c in checks:
            (ui.ok if c["passed"] else ui.bad)(c["desc"])
            if not c["passed"] and c.get("detail"):
                ui.dim("   " + c["detail"].splitlines()[-1][:120])
        pts = sum(c["points"] for c in passed)
        print()
        print("  " + ui.bar(len(passed) / max(1, len(checks))) +
              "  %d/%d checks · %d/%d points"
              % (len(passed), len(checks), pts, lab["points_total"]))
        if len(passed) < len(checks):
            nxt = next(c for c in checks if not c["passed"])
            if nxt.get("hint"):
                print()
                ui.info("nudge: " + nxt["hint"])
            else:
                ui.dim("stuck? try  hint  for a nudge")
        else:
            print()
            ui.ok("everything passes -- run %ssubmit%s to finish and be graded"
                  % (ui.BOLD, ui.RESET))
        print()
    return checks


def cmd_verify(args):
    """The `verify` command: exit 0 only when every check passes."""
    checks = run_checks_for_active(quiet=False, count=True)
    return 0 if checks and all(c["passed"] for c in checks) else 1


def cmd_hint(args):
    act, lab = _ctx()
    if not lab["hints"]:
        ui.warn("this lab has no hints -- read the failing check messages from verify")
        return 0
    data = store.load()
    rec = data["labs"].get(act["lab_id"], {})
    att = next((a for a in rec.get("attempts", []) if a["n"] == act["attempt"]), {})
    used = att.get("hints_used", [])

    if args.index is None:
        ui.head("  Hints")
        for i, h in enumerate(lab["hints"]):
            tag = ui.GREEN + "revealed" + ui.RESET if i in used else \
                ui.YELLOW + ("costs %d pts" % h["cost"]) + ui.RESET
            print("  %s%d%s  %-32s %s" % (ui.BOLD, i + 1, ui.RESET,
                                          h.get("title", "Hint %d" % (i + 1)), tag))
            if i in used:
                print(ui.wrap(h["text"].strip(), indent="      "))
        print()
        ui.dim("reveal one with:  hint 1")
        return 0

    idx = args.index - 1
    if idx < 0 or idx >= len(lab["hints"]):
        ui.bad("there is no hint %d" % args.index)
        return 1
    res = api("POST", "/api/session/hint",
              {"lab_id": act["lab_id"], "attempt": act["attempt"], "index": idx})
    h = res.get("hint") or lab["hints"][idx]
    ui.head("  " + h.get("title", "Hint %d" % args.index))
    print(ui.wrap(h["text"].strip(), indent="  "))
    print()
    ui.dim("hint points used so far: %d" % res.get("cost", 0))
    return 0


def cmd_tasks(args):
    act, lab = _ctx()
    print_brief(lab, act["attempt"])
    return 0


def cmd_status(args):
    act, lab = _ctx()
    data = store.load()
    rec = data["labs"].get(act["lab_id"], {})
    att = next((a for a in rec.get("attempts", []) if a["n"] == act["attempt"]), {})
    elapsed = time.time() - act["started_ts"]
    checks = att.get("checks") or []
    passed = sum(1 for c in checks if c.get("passed"))
    ui.head("  %s" % lab["title"])
    ui.kv("elapsed", ui.hms(elapsed) + ui.GREY + "   (par %d min)" % lab["par_minutes"] + ui.RESET)
    ui.kv("checks", "%d/%d passing" % (passed, len(lab["verify"])) if checks
          else "not verified yet -- run  verify")
    if checks:
        print("  " + " " * 14 + ui.bar(passed / max(1, len(lab["verify"]))))
    ui.kv("hints used", "%d (%d points)" % (len(att.get("hints_used", [])),
                                            att.get("hint_cost", 0)))
    ui.kv("verify runs", att.get("verify_runs", 0))
    ui.kv("workdir", act["workdir"])
    print()
    return 0


def cmd_solution(args):
    act, lab = _ctx()
    if not lab.get("solution"):
        ui.warn("no reference solution is written for this lab yet")
        return 0
    if not args.yes:
        ui.warn("this reveals the answer and is recorded against this attempt")
        try:
            resp = input("  show it anyway? [y/N] ").strip().lower()
        except EOFError:
            resp = "n"
        if resp not in ("y", "yes"):
            return 0
    # treat it as using every hint: you did not solve this unaided
    for i in range(len(lab["hints"])):
        api("POST", "/api/session/hint",
            {"lab_id": act["lab_id"], "attempt": act["attempt"], "index": i})
    ui.head("  Reference solution")
    print(ui.wrap(lab["solution"].strip(), indent="  "))
    print()
    return 0


def _collect_solution(act, lab):
    diff = []
    try:
        # only the namespaces the learner could have touched -- dumping
        # kube-system into every write-up would drown the actual answer
        r = cluster.sh(
            "kubectl get ns -o name | sed 's|namespace/||' "
            "| grep -vE '^(kube-system|kube-public|kube-node-lease|local-path-storage)$' "
            "| while read ns; do "
            "kubectl -n $ns get deploy,sts,ds,job,cronjob,svc,ing,netpol,cm,secret,pvc,pod "
            "--no-headers -o custom-columns=K:.kind,N:.metadata.name 2>/dev/null "
            "| grep -v 'kube-root-ca.crt' "
            "| sed \"s|^|$ns/|\"; done | head -60", timeout=90)
        diff = [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    sol = recorder.build_solution(act["log"], lab, cluster_diff=diff)
    path = os.path.join(act["dir"], "solution.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(sol["markdown"])
    return sol


def _print_result(lab, res, checks, duration):
    score = res.get("score")
    verdict = res.get("verdict") or {}
    print()
    print(ui.rule("═"))
    colour = ui.score_colour(score)
    print("  %sExam confidence  %s%d%%%s   %s%s%s"
          % (ui.BOLD, colour, score, ui.RESET, ui.BOLD, verdict.get("label", ""), ui.RESET))
    print("  " + ui.bar((score or 0) / 100.0, size=40))
    print()
    print(ui.wrap(verdict.get("blurb", ""), indent="  "))
    print()
    for key in ("correctness", "time", "hints", "flow"):
        b = (res.get("breakdown") or {}).get(key)
        if not b:
            continue
        print("  %s%-13s%s %5.1f / %-5.1f  %s%s%s"
              % (ui.GREY, key, ui.RESET, b["earned"], b["weight"],
                 ui.GREY, b["detail"], ui.RESET))
    pen = (res.get("breakdown") or {}).get("familiarity_penalty")
    if pen:
        print("  %s%-13s%s %5.1f          %sseen this lab before%s"
              % (ui.GREY, "repeat", ui.RESET, pen, ui.GREY, ui.RESET))
    print()
    for ex, v in (verdict.get("per_exam") or {}).items():
        mark = ui.GREEN + "would pass" if v["would_pass"] else ui.RED + "would fail"
        print("  %-6s %s%s%s  %s(cut score %d%%, you are %+d)%s"
              % (ex, mark, ui.RESET, "", ui.GREY, v["pass_mark"], v["margin"], ui.RESET))
    print()
    ui.kv("time taken", ui.hms(duration) + ui.GREY + "  (par %d min)" % lab["par_minutes"] + ui.RESET)
    print(ui.rule("═"))


def cmd_finish(args):
    act, lab = _ctx()
    checks = run_checks_for_active(quiet=True, count=True)
    failed = [c for c in checks if not c["passed"]]

    if failed:
        ui.head("  Not finished yet")
        for c in checks:
            (ui.ok if c["passed"] else ui.bad)(c["desc"])
            if not c["passed"] and c.get("detail"):
                ui.dim("   " + c["detail"].splitlines()[-1][:120])
        print()
        print("  " + ui.bar((len(checks) - len(failed)) / max(1, len(checks))) +
              "  %d of %d checks pass" % (len(checks) - len(failed), len(checks)))
        print()
        nxt = failed[0]
        if nxt.get("hint"):
            ui.info("start with: " + nxt["hint"])
        ui.dim("the lab is still running -- fix it and run  submit  again")
        ui.dim("stuck? try  hint  ·  really stuck?  answer  ·  stop here?  giveup")
        print()
        return 1

    duration = int(time.time() - act["started_ts"])
    ui.info("all checks pass -- recording your solution")
    sol = _collect_solution(act, lab)
    res = api("POST", "/api/session/finish", {
        "lab_id": act["lab_id"], "attempt": act["attempt"], "checks": checks,
        "outcome": "passed", "duration_s": duration,
        "solution": sol["markdown"], "commands": sol["commands"],
        "solution_stats": sol["stats"]})
    if res.get("error"):
        ui.bad(res["error"])
        return 1

    _print_result(lab, res, checks, duration)
    ui.dim("recorded %d cluster-changing commands from your session"
           % sol["stats"]["mutating_commands"])
    ep = endpoint()
    if ep:
        ui.info("full write-up: %s#/lab/%s" % (ep["url"], act["lab_id"]))
    write_active(None)
    print()
    return 0


def cmd_giveup(args):
    act, lab = _ctx()
    checks = run_checks_for_active(quiet=True, count=False)
    duration = int(time.time() - act["started_ts"])
    sol = _collect_solution(act, lab)
    api("POST", "/api/session/finish", {
        "lab_id": act["lab_id"], "attempt": act["attempt"], "checks": checks,
        "outcome": "abandoned", "duration_s": duration,
        "solution": sol["markdown"], "commands": sol["commands"],
        "solution_stats": sol["stats"]})
    passed = sum(1 for c in checks if c["passed"])
    ui.warn("stopped with %d/%d checks passing -- saved as an unfinished attempt"
            % (passed, len(checks)))
    if lab.get("solution"):
        ui.dim("the reference solution is on the lab page in the dashboard")
    write_active(None)
    return 0


def cmd_shellhelp(args):
    shell_help(compact=args.compact)
    return 0


def cmd_doctor(args):
    ui.banner("klab doctor")
    problems = preflight(fix=True)
    for name in ("docker", "kubectl", "kind", "helm", "jq"):
        p = cluster.which(name)
        (ui.ok if p else ui.warn)("%-8s %s" % (name, p or "not found"))
    okd, info = cluster.docker_ok()
    (ui.ok if okd else ui.bad)("docker daemon: %s" % info)
    try:
        cat = labmod.load_catalogue()
        ui.ok("catalogue: %d labs" % len(cat))
    except Exception as e:
        ui.bad("catalogue: %s" % e)
    meta = cluster.read_meta()
    if meta:
        ui.info("cluster cached with key %s (created %s)"
                % (meta.get("key"), meta.get("created_at")))
    for p in problems:
        ui.bad(p)
    return 1 if problems else 0


def cmd_cleanup(args):
    cluster.delete_cluster(cb=lambda m: print(m))
    ui.ok("practice cluster removed (your progress is untouched)")
    return 0


def cmd_validate(args):
    """Static + optional live validation of the catalogue."""
    import validate
    return validate.main(args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="labctl", add_help=True)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run"); p.set_defaults(fn=cmd_run)
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--host", default=None)
    p.add_argument("--remote", action="store_true",
                   help="serve on all interfaces for headless machines")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-install", action="store_true")

    for name, fn in (("verify", cmd_verify), ("tasks", cmd_tasks),
                     ("status", cmd_status), ("giveup", cmd_giveup),
                     ("finish", cmd_finish), ("doctor", cmd_doctor),
                     ("cleanup", cmd_cleanup)):
        p = sub.add_parser(name); p.set_defaults(fn=fn)

    p = sub.add_parser("hint"); p.set_defaults(fn=cmd_hint)
    p.add_argument("index", nargs="?", type=int, default=None)

    p = sub.add_parser("solution"); p.set_defaults(fn=cmd_solution)
    p.add_argument("-y", "--yes", action="store_true")

    p = sub.add_parser("shellhelp"); p.set_defaults(fn=cmd_shellhelp)
    p.add_argument("--compact", action="store_true")

    p = sub.add_parser("validate"); p.set_defaults(fn=cmd_validate)
    p.add_argument("--live", action="store_true",
                   help="actually build clusters and run every lab's checks")
    p.add_argument("--only", default=None, help="lab id or category substring")
    p.add_argument("--solve", action="store_true",
                   help="apply each lab's reference solution and expect a pass")

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args(["run"] + (argv or [])[0:0])
        args.fn = cmd_run
        args.port, args.host, args.remote = 9000, None, False
        args.no_browser = args.no_install = False
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
