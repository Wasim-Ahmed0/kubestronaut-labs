#!/usr/bin/env python3
"""Check that the README still describes the code that exists.

Documentation drifts silently: a flag gets added, an alias gets renamed, a
lab file is dropped in, and the README quietly becomes wrong.  This runs as
part of `klab validate` so that cannot happen unnoticed.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import labs as labmod  # noqa: E402


def audit():
    """Returns a list of human-readable problems (empty when all is well)."""
    problems = []
    readme_path = os.path.join(ROOT, "README.md")
    if not os.path.exists(readme_path):
        return ["README.md is missing"]

    readme = open(readme_path, encoding="utf-8").read()
    klab = open(os.path.join(ROOT, "klab"), encoding="utf-8").read()
    labctl = open(os.path.join(HERE, "labctl.py"), encoding="utf-8").read()

    # subcommands the README promises must really be dispatched
    documented = set(re.findall(r"\./klab\s+([a-z]+)", readme)) - {"help"}
    actual = {"run", "start"}
    for m in re.findall(r"^  ([a-z|\-]+)\)", klab, re.M):
        actual.update(x for x in m.split("|") if x and not x.startswith("-"))
    for cmd in sorted(documented - actual):
        problems.append("README documents `./klab %s`, which does not exist" % cmd)

    # flags the README promises must really be parsed
    documented_flags = set(re.findall(r"\./klab[^\n`]*?(--[a-z\-]+)", readme))
    real_flags = set(re.findall(r'add_argument\("(--[a-z\-]+)"', labctl))
    for f in sorted(documented_flags - real_flags):
        problems.append("README documents the flag %s, which is not defined" % f)

    # lab-shell commands must exist in both directions
    shell_fns = set(re.findall(r"^([a-z]+)\(\)\s*\{", labctl, re.M))
    for fn in sorted(shell_fns):
        if "`%s`" % fn not in readme:
            problems.append("lab-shell command `%s` exists but is undocumented" % fn)

    # the counts in the opening paragraph must match the catalogue
    catalogue = labmod.load_catalogue()
    n_labs = len(catalogue)
    n_cats = len(labmod.categories(catalogue))
    if "**%d labs" % n_labs not in readme:
        problems.append("README lab count is stale (the catalogue has %d)" % n_labs)
    if "%d categories" % n_cats not in readme:
        problems.append("README category count is stale (there are %d)" % n_cats)

    # every category a learner can see must be described in the curriculum table
    for name in sorted({l["category"] for l in catalogue}):
        if name not in readme:
            problems.append("category '%s' is missing from the README" % name)

    # the paths people need in order to back up or debug
    for path in ("~/.kubestronaut/state.json", "~/.kubestronaut/bin",
                 "~/.kubestronaut/runtime/token"):
        if path not in readme:
            problems.append("README does not mention %s" % path)

    return problems


if __name__ == "__main__":
    found = audit()
    for p in found:
        print("docs: " + p)
    print("docs audit: %d problem(s)" % len(found))
    sys.exit(1 if found else 0)
