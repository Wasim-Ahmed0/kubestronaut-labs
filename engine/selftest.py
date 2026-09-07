#!/usr/bin/env python3
"""`klab selftest` -- prove the whole machine works on this computer.

Runs one small lab all the way through without you having to touch it:
builds the cluster, seeds the scenario, confirms the checks FAIL before any
work is done, applies the reference solution, confirms they all PASS, and
grades the result. If this passes, the platform is working end to end.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cluster  # noqa: E402
import grader  # noqa: E402
import labs as labmod  # noqa: E402
import recorder  # noqa: E402
import ui  # noqa: E402

SELFTEST_LAB = "start-first-steps"


def step(n, total, text):
    print("  %s[%d/%d]%s %s" % (ui.GREY, n, total, ui.RESET, text), flush=True)


def main(args=None):
    ui.banner("klab selftest", "one lab, start to finish, unattended")

    total = 8
    failures = []

    step(1, total, "loading the catalogue")
    try:
        catalogue = labmod.load_catalogue()
    except Exception as e:
        ui.bad("the catalogue would not load: %s" % e)
        return 1
    lab = next((l for l in catalogue if l["id"] == SELFTEST_LAB), None)
    if lab is None:
        ui.bad("selftest lab %s is missing from the catalogue" % SELFTEST_LAB)
        return 1
    ui.ok("%d labs loaded" % len(catalogue))

    step(2, total, "checking docker")
    okd, info = cluster.docker_ok()
    if not okd:
        ui.bad(info)
        ui.dim("start Docker and run ./klab selftest again")
        return 1
    ui.ok("docker %s" % info)

    step(3, total, "building (or reusing) the practice cluster")
    t0 = time.time()
    try:
        mode = cluster.ensure_cluster(lab["cluster"], cb=lambda m: ui.dim(m.strip()))
    except cluster.ClusterError as e:
        ui.bad(str(e))
        return 1
    ui.ok("cluster %s in %ds" % (mode, int(time.time() - t0)))

    step(4, total, "seeding the scenario")
    try:
        cluster.apply_setup(lab, cb=lambda m: None)
    except cluster.ClusterError as e:
        ui.bad("setup failed: %s" % e)
        return 1
    ui.ok("scenario seeded")

    step(5, total, "checks must FAIL before any work is done")
    before = cluster.run_checks(lab, timeout_each=40)
    passing = [c for c in before if c["passed"] and c.get("points", 0) > 0]
    if passing:
        ui.bad("these checks passed before anything was done: %s"
               % ", ".join(c["id"] for c in passing))
        failures.append("checks are not measuring anything")
    else:
        ui.ok("all %d checks correctly fail on an untouched cluster" % len(before))

    step(6, total, "applying the reference solution")
    if not lab.get("solve_script"):
        ui.bad("selftest lab has no solve_script")
        return 1
    r = cluster.sh(lab["solve_script"], timeout=420)
    if r.returncode != 0:
        ui.bad("the reference solution failed: %s"
               % (r.stderr or r.stdout).strip()[-300:])
        return 1
    ui.ok("solution applied")

    step(7, total, "checks must now PASS")
    after = cluster.run_checks(lab, timeout_each=60)
    failed = [c for c in after if not c["passed"]]
    if failed:
        for c in failed:
            ui.bad("%s -- %s" % (c["desc"], c["detail"][-120:]))
        failures.append("a correct solution did not pass the checks")
    else:
        ui.ok("all %d checks pass" % len(after))

    step(8, total, "grading")
    score, breakdown = grader.grade(
        checks=after, duration_s=300, par_minutes=lab["par_minutes"],
        hint_cost=0, verify_runs=1, attempt_n=1,
        hint_cost_total=lab["hint_cost_total"])
    verdict = grader.verdict(score, lab["exams"])
    if not (0 <= score <= 100):
        failures.append("the grader produced an impossible score")
    ui.ok("grader returned %d%% (%s)" % (score, verdict["label"]))

    # the recorder is pure text processing, so check it without a shell
    sol = recorder.build_solution(os.devnull, lab)
    if "Your recorded solution" not in sol["markdown"]:
        failures.append("the solution recorder produced nothing")

    print()
    if failures:
        for f in failures:
            ui.bad(f)
        ui.bad("selftest FAILED")
        return 1
    ui.ok("selftest passed -- the platform works on this machine")
    ui.dim("run ./klab to start studying")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
