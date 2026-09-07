"""Automatic solution capture.

A tiny shim is put at the front of PATH inside the lab shell.  It logs
every kubectl/helm invocation (and the YAML piped into `apply -f -`)
before handing off to the real binary, so when you finish a lab we can
write your solution down for you without you typing it up.
"""
from __future__ import annotations

import os
import re
import shlex
import stat

import store

SHIM_DIR = os.path.join(store.RUNTIME, "shim")
SHIMMED = ("kubectl", "helm", "kustomize", "crictl", "etcdctl")

# Commands that only look at the cluster -- noise in a written solution.
READ_ONLY = {
    "get", "describe", "logs", "explain", "version", "api-resources",
    "api-versions", "cluster-info", "top", "events", "config", "auth",
    "diff", "wait", "completion", "options", "proxy", "port-forward",
    "help", "list", "status", "search", "show", "repo", "history", "env",
}

_SHIM_TEMPLATE = r"""#!/usr/bin/env bash
# klab command recorder -- logs the invocation, then runs the real binary.
__klab_real=""
IFS=':' read -r -a __klab_paths <<< "$KLAB_REAL_PATH"
for __d in "${__klab_paths[@]}"; do
  if [ -x "$__d/__NAME__" ]; then __klab_real="$__d/__NAME__"; break; fi
done
if [ -z "$__klab_real" ]; then
  echo "klab: cannot find the real __NAME__ on PATH" >&2
  exit 127
fi

if [ -n "$KLAB_LOG" ]; then
  __klab_stdin=""
  # Only intercept stdin when the user is piping a manifest in, so that
  # interactive things like `kubectl exec -it` keep working.
  for __a in "$@"; do
    if [ "$__a" = "-" ]; then __klab_stdin="yes"; fi
  done
  if [ -n "$__klab_stdin" ] && [ ! -t 0 ]; then
    __klab_tmp="$(dirname "$KLAB_LOG")/stdin-$(date +%s%N).yaml"
    cat > "$__klab_tmp"
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$PWD" "$__klab_tmp" \
      "$(printf '%q ' __NAME__ "$@")" >> "$KLAB_LOG"
    "$__klab_real" "$@" < "$__klab_tmp"
    exit $?
  fi
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$PWD" "-" \
    "$(printf '%q ' __NAME__ "$@")" >> "$KLAB_LOG"
fi
exec "$__klab_real" "$@"
"""


def install_shims(real_path: str) -> str:
    """(Re)create the shim directory. ``real_path`` is the un-shimmed PATH."""
    os.makedirs(SHIM_DIR, exist_ok=True)
    for name in SHIMMED:
        path = os.path.join(SHIM_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_SHIM_TEMPLATE.replace("__NAME__", name))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return SHIM_DIR


def parse_log(log_path: str):
    """Read the shim log into structured records."""
    out = []
    if not os.path.exists(log_path):
        return out
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            ts, cwd, stdin_file, cmd = parts[0], parts[1], parts[2], parts[3]
            try:
                argv = shlex.split(cmd)
            except ValueError:
                argv = cmd.split()
            if not argv:
                continue
            out.append({"ts": int(ts) if ts.isdigit() else 0, "cwd": cwd,
                        "stdin_file": None if stdin_file == "-" else stdin_file,
                        "argv": argv, "cmd": " ".join(shlex.quote(a) for a in argv)})
    return out


# Global flags that consume the next argument, so it must not be mistaken
# for the subcommand.  `kubectl -n hello get pods` is a *read*, not a write.
VALUE_FLAGS = {
    "-n", "--namespace", "--context", "--cluster", "--kubeconfig", "--server",
    "-s", "--user", "--as", "--as-group", "--token", "--request-timeout",
    "--cache-dir", "--certificate-authority", "--client-certificate",
    "--client-key", "--tls-server-name", "--log-flush-frequency", "-v",
    "--v", "--profile", "--profile-output", "--warnings-as-errors",
}


def _subcommand(argv):
    skip = False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            if "=" not in a and a in VALUE_FLAGS:
                skip = True
            continue
        return a
    return ""


def is_mutating(rec) -> bool:
    sub = _subcommand(rec["argv"])
    if not sub:
        return False
    if sub in READ_ONLY:
        # `kubectl config set-context` does change things
        return sub == "config" and any(
            a.startswith("set") or a == "use-context" for a in rec["argv"][2:])
    return True


def _read_manifest(path, cwd):
    for candidate in (path, os.path.join(cwd, path)):
        try:
            if os.path.isfile(candidate) and os.path.getsize(candidate) < 200_000:
                with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
        except OSError:
            pass
    return None


def collect_manifests(records):
    """Grab the YAML behind every `-f`, whether piped or from a file."""
    seen, out = set(), []
    for rec in records:
        argv = rec["argv"]
        if rec["stdin_file"]:
            body = _read_manifest(rec["stdin_file"], rec["cwd"])
            if body and body.strip() and body not in seen:
                seen.add(body)
                out.append({"source": "piped into %s" % _subcommand(argv), "body": body.strip()})
        for i, a in enumerate(argv):
            if a in ("-f", "--filename") and i + 1 < len(argv):
                target = argv[i + 1]
            elif a.startswith("--filename=") or a.startswith("-f="):
                target = a.split("=", 1)[1]
            else:
                continue
            if target == "-" or re.match(r"^https?://", target):
                continue
            body = _read_manifest(target, rec["cwd"])
            if body and body.strip() and body not in seen:
                seen.add(body)
                out.append({"source": target, "body": body.strip()})
    return out


def build_solution(log_path: str, lab: dict, cluster_diff=None) -> dict:
    """Assemble the markdown write-up of what the learner actually did."""
    records = parse_log(log_path)
    mutating, deduped = [], None
    for rec in records:
        if not is_mutating(rec):
            continue
        if rec["cmd"] == deduped:
            continue
        deduped = rec["cmd"]
        mutating.append(rec)

    manifests = collect_manifests(records)

    lines = ["# Your recorded solution -- %s" % lab["title"], ""]
    lines.append("_Captured automatically from the commands you ran._")
    lines.append("")
    if mutating:
        lines += ["## Commands that changed the cluster", "", "```bash"]
        for rec in mutating:
            cmd = rec["cmd"]
            if rec["stdin_file"]:
                cmd += "   # (manifest piped in -- see below)"
            lines.append(cmd)
        lines += ["```", ""]
    else:
        lines += ["_No cluster-changing commands were recorded._", ""]

    if manifests:
        lines += ["## Manifests you applied", ""]
        for m in manifests:
            lines += ["**%s**" % m["source"], "", "```yaml", m["body"], "```", ""]

    if cluster_diff:
        lines += ["## Resources that ended up in the cluster", "", "```"]
        lines += cluster_diff
        lines += ["```", ""]

    stats = {
        "total_commands": len(records),
        "mutating_commands": len(mutating),
        "read_commands": len(records) - len(mutating),
        "manifests": len(manifests),
    }
    return {"markdown": "\n".join(lines), "commands": [r["cmd"] for r in mutating],
            "all_commands": [r["cmd"] for r in records], "stats": stats}
