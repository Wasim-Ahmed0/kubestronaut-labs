#!/usr/bin/env python3
"""Catalogue tests.

Static mode (the default) parses every lab, enforces the schema and runs
`bash -n` over every setup and verify snippet.  `--live` goes further: it
builds the real cluster for each lab, seeds it, checks that the lab does
NOT already pass (otherwise the check is vacuous), optionally applies the
reference solution with `--solve` and requires every check to flip to
passing.  That is what proves a lab is actually solvable.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cluster  # noqa: E402
import labs as labmod  # noqa: E402
import ui  # noqa: E402


def bash_syntax(script, label, errors):
    r = subprocess.run(["bash", "-n"], input=cluster.PREAMBLE + "\n" + script,
                       capture_output=True, text=True)
    if r.returncode != 0:
        errors.append("%s: bash syntax error: %s" % (label, r.stderr.strip()[:200]))


def static_checks(catalogue):
    errors, warnings = [], []
    ids = set()
    for lab in catalogue:
        lid = lab["id"]
        if lid in ids:
            errors.append("duplicate id %s" % lid)
        ids.add(lid)

        if not lab["id"].replace("-", "").replace("_", "").isalnum():
            errors.append("%s: id should be kebab-case alphanumerics" % lid)
        if len(lab["scenario"].strip()) < 60:
            warnings.append("%s: scenario is very short" % lid)
        if not lab["tasks"]:
            warnings.append("%s: no explicit task list" % lid)
        if not lab["solution"]:
            warnings.append("%s: no reference solution (cannot --solve)" % lid)
        if not lab["hints"]:
            warnings.append("%s: no hints" % lid)
        if lab["points_total"] < 10:
            warnings.append("%s: only %d points" % (lid, lab["points_total"]))
        if lab["par_minutes"] <= 0:
            errors.append("%s: par_minutes must be positive" % lid)

        seen_check = set()
        for chk in lab["verify"]:
            if chk["id"] in seen_check:
                errors.append("%s: duplicate check id %s" % (lid, chk["id"]))
            seen_check.add(chk["id"])
            bash_syntax(chk["run"], "%s/%s" % (lid, chk["id"]), errors)
            if not any(t in chk["run"] for t in
                       ("kubectl", "docker", "jp ", "has ", "ready_pods",
                        "rollout ", "curl", "helm")):
                warnings.append("%s/%s: check never talks to the cluster"
                                % (lid, chk["id"]))
        for i, st in enumerate(lab["setup"]):
            if st["kind"] == "shell":
                bash_syntax(st["run"], "%s/setup[%d]" % (lid, i), errors)
        for step in lab["cluster"].get("install") or []:
            bash_syntax(step["run"], "%s/install" % lid, errors)
    return errors, warnings


def live_check(lab, solve=False, keep=False):
    """Build the lab for real and prove its checks mean something."""
    t0 = time.time()
    result = {"id": lab["id"], "ok": False, "notes": []}
    try:
        cluster.ensure_extra_tools(lab.get("requires_tools"), cb=lambda m: None)
        cluster.ensure_cluster(lab["cluster"], force_fresh=False,
                               cb=lambda m: None)
        cluster.apply_setup(lab, cb=lambda m: None)
    except cluster.ClusterError as e:
        result["notes"].append("setup failed: %s" % e)
        return result

    before = cluster.run_checks(lab, timeout_each=40)
    pre_pass = [c for c in before if c["passed"] and c.get("points", 0) > 0]
    if pre_pass:
        result["notes"].append(
            "these checks already pass before any work: %s"
            % ", ".join(c["id"] for c in pre_pass))

    if solve and lab.get("solve_script"):
        r = cluster.sh(lab["solve_script"], timeout=420)
        if r.returncode != 0:
            result["notes"].append("solve script failed: %s"
                                   % (r.stderr or r.stdout).strip()[-300:])
            return result
        after = cluster.run_checks(lab, timeout_each=60)
        failed = [c for c in after if not c["passed"]]
        if failed:
            result["notes"].append(
                "after the reference solution these still fail: %s"
                % "; ".join("%s (%s)" % (c["id"], c["detail"][-90:]) for c in failed))
            return result
        result["notes"].append("solved cleanly")
    elif solve:
        result["notes"].append("no solve_script -- cannot auto-solve")

    result["ok"] = not pre_pass and (not solve or lab.get("solve_script"))
    result["seconds"] = round(time.time() - t0, 1)
    return result


def main(args):
    try:
        catalogue = labmod.load_catalogue()
    except labmod.LabError as e:
        ui.bad(str(e))
        return 1

    if getattr(args, "only", None):
        q = args.only.lower()
        catalogue = [l for l in catalogue
                     if q in l["id"].lower() or q in l["category"].lower()
                     or q in l["title"].lower()]

    ui.banner("Catalogue validation", "%d labs" % len(catalogue))
    errors, warnings = static_checks(catalogue)

    for w in warnings:
        ui.warn(w)
    for e in errors:
        ui.bad(e)
    if not errors:
        ui.ok("static checks passed for %d labs" % len(catalogue))

    # the docs are part of the product, so they get checked too
    if not getattr(args, "only", None):
        import docs_audit
        doc_problems = docs_audit.audit()
        for d in doc_problems:
            ui.warn("docs: " + d)
        if not doc_problems:
            ui.ok("README matches the code")

    by_exam = {}
    for lab in catalogue:
        for ex in lab["exams"]:
            by_exam[ex] = by_exam.get(ex, 0) + 1
    ui.head("  Coverage")
    for ex in labmod.EXAMS:
        ui.kv(ex, "%d labs" % by_exam.get(ex, 0))
    cats = labmod.categories(catalogue)
    for c, n in sorted(cats.items()):
        ui.kv(c, "%d" % n, pad=42)

    if not getattr(args, "live", False):
        if errors:
            ui.bad("%d error(s)" % len(errors))
        return 1 if errors else 0

    ui.head("  Live validation (this builds real clusters -- go and have a coffee)")
    okd, info = cluster.docker_ok()
    if not okd:
        ui.bad(info)
        return 1

    # group by cluster shape so we rebuild as rarely as possible
    catalogue.sort(key=lambda l: l["cluster_key"])
    failures = []
    for i, lab in enumerate(catalogue, 1):
        sys.stdout.write("  [%d/%d] %-34s " % (i, len(catalogue), lab["id"]))
        sys.stdout.flush()
        res = live_check(lab, solve=getattr(args, "solve", False))
        if res["ok"]:
            print(ui.GREEN + "ok" + ui.RESET + ui.GREY
                  + "  %ss" % res.get("seconds", "?") + ui.RESET)
        else:
            print(ui.RED + "FAIL" + ui.RESET)
            failures.append(res)
        for n in res["notes"]:
            ui.dim("     " + n)

    print()
    if failures:
        ui.bad("%d lab(s) failed live validation" % len(failures))
        return 1
    ui.ok("every lab builds, seeds and is solvable")
    return 0


def list_labs():
    catalogue = labmod.load_catalogue()
    cat = None
    for lab in catalogue:
        if lab["category"] != cat:
            cat = lab["category"]
            print("\n" + ui.BOLD + cat + ui.RESET)
        print("  %s%-26s%s %-52s %s%-7s%s %s"
              % (ui.CYAN, lab["id"], ui.RESET, lab["title"][:52],
                 ui.GREY, lab["difficulty"], ui.RESET, "/".join(lab["exams"])))
    print("\n%d labs total\n" % len(catalogue))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--solve", action="store_true")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    if a.list:
        list_labs()
        sys.exit(0)
    sys.exit(main(a))
