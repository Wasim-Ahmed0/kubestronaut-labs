"""Load, normalise and validate the lab catalogue."""
from __future__ import annotations

import glob
import hashlib
import json
import os

import miniyaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB_DIR = os.path.join(ROOT, "labs")

DIFFICULTIES = ["intro", "easy", "medium", "hard", "expert"]

# The order a learner should walk the catalogue in, from "never touched
# Kubernetes" to "ready for the CKS".  Anything not listed sorts last.
CATEGORY_ORDER = [
    "Getting Started",
    "Core Concepts & Architecture",
    "Cloud Native Foundations",
    "Pods & Containers",
    "Controllers, Rollouts & Scaling",
    "Configuration & Resource Governance",
    "Services & Networking",
    "Storage",
    "Application Design & Deployment Patterns",
    "Observability & Logging",
    "Scheduling & Node Management",
    "Troubleshooting",
    "Cluster Architecture & Lifecycle",
    "Helm & Kustomize",
    "Extending Kubernetes",
    "Identity, RBAC & Access Control",
    "Security Hardening",
    "Supply Chain Security",
    "Runtime & Platform Security",
    "Production Scenarios",
]


def category_rank(name):
    try:
        return CATEGORY_ORDER.index(name)
    except ValueError:
        return len(CATEGORY_ORDER)
EXAMS = ["KCNA", "KCSA", "CKA", "CKAD", "CKS"]

DIFFICULTY_ORDER = {d: i for i, d in enumerate(DIFFICULTIES)}


class LabError(ValueError):
    pass


def _require(lab, field, path):
    if not lab.get(field):
        raise LabError("%s: lab %r is missing required field %r"
                       % (path, lab.get("id", "?"), field))


def _norm_cluster(spec):
    spec = spec or {}
    out = {
        "nodes": int(spec.get("nodes", 1)),
        "cni": spec.get("cni", "default"),
        "k8s_version": spec.get("k8s_version"),
        "kind_patches": spec.get("kind_patches"),
        "kind_nodes": spec.get("kind_nodes"),
        "extra_files": spec.get("extra_files") or [],
        "install": spec.get("install") or [],
    }
    if out["nodes"] < 1 or out["nodes"] > 4:
        raise LabError("cluster.nodes must be between 1 and 4")
    if out["cni"] not in ("default", "calico"):
        raise LabError("cluster.cni must be 'default' or 'calico'")
    return out


def cluster_key(cluster: dict) -> str:
    """Clusters with the same key can be reused between labs."""
    blob = json.dumps(cluster, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def normalise(lab: dict, path: str) -> dict:
    for f in ("id", "title", "category", "difficulty", "scenario"):
        _require(lab, f, path)
    if lab["difficulty"] not in DIFFICULTIES:
        raise LabError("%s: lab %s has unknown difficulty %r (want one of %s)"
                       % (path, lab["id"], lab["difficulty"], DIFFICULTIES))

    exams = lab.get("exams") or []
    if isinstance(exams, str):
        exams = [exams]
    bad = [e for e in exams if e not in EXAMS]
    if bad:
        raise LabError("%s: lab %s references unknown exams %s" % (path, lab["id"], bad))
    if not exams:
        raise LabError("%s: lab %s must map to at least one exam" % (path, lab["id"]))

    verify = lab.get("verify") or []
    if not verify:
        raise LabError("%s: lab %s has no verify checks" % (path, lab["id"]))
    for i, chk in enumerate(verify):
        if not isinstance(chk, dict) or not chk.get("run"):
            raise LabError("%s: lab %s verify[%d] needs a 'run' script" % (path, lab["id"], i))
        chk.setdefault("id", "c%d" % (i + 1))
        chk.setdefault("desc", chk["id"])
        chk.setdefault("points", 10)
        chk.setdefault("hint", None)

    tasks = lab.get("tasks") or []
    for i, t in enumerate(tasks):
        t.setdefault("id", "t%d" % (i + 1))

    hints = lab.get("hints") or []
    norm_hints = []
    for i, h in enumerate(hints):
        if isinstance(h, str):
            h = {"text": h}
        h.setdefault("cost", 8 + 4 * i)
        h.setdefault("title", "Hint %d" % (i + 1))
        norm_hints.append(h)

    setup = lab.get("setup") or []
    for s in setup:
        if not s.get("kind"):
            s["kind"] = "file" if s.get("path") else (
                "manifest" if "content" in s else "shell")
        if s["kind"] == "file" and not s.get("path"):
            raise LabError("%s: lab %s has a file setup step with no path"
                           % (path, lab.get("id")))

    requires = lab.get("requires") or []
    if isinstance(requires, str):
        requires = [requires]

    out = dict(lab)
    out.update({
        "exams": exams,
        "verify": verify,
        "tasks": tasks,
        "hints": norm_hints,
        "setup": setup,
        "requires": requires,
        "requires_tools": lab.get("requires_tools") or [],
        "concepts": lab.get("concepts") or [],
        "docs": lab.get("docs") or [],
        "par_minutes": float(lab.get("par_minutes") or 10),
        "estimated_minutes": float(lab.get("estimated_minutes")
                                  or lab.get("par_minutes") or 10),
        "solution": lab.get("solution") or "",
        "cluster": _norm_cluster(lab.get("cluster")),
        "points_total": sum(c["points"] for c in verify),
        "hint_cost_total": sum(h["cost"] for h in norm_hints) or 100,
        "source_file": os.path.relpath(path, ROOT),
    })
    out["cluster_key"] = cluster_key(out["cluster"])
    return out


def load_catalogue(lab_dir: str = LAB_DIR):
    labs, seen = [], {}
    files = sorted(glob.glob(os.path.join(lab_dir, "**", "*.yaml"), recursive=True))
    for path in files:
        doc = miniyaml.load_file(path)
        if not doc:
            continue
        entries = doc.get("labs") if isinstance(doc, dict) and "labs" in doc else doc
        if isinstance(entries, dict):
            entries = [entries]
        defaults = {}
        if isinstance(doc, dict):
            for k in ("category", "exams", "cluster", "docs"):
                if k in doc and k != "labs":
                    defaults[k] = doc[k]
        for raw in entries or []:
            merged = dict(defaults)
            merged.update(raw)
            lab = normalise(merged, path)
            if lab["id"] in seen:
                raise LabError("duplicate lab id %r in %s and %s"
                               % (lab["id"], seen[lab["id"]], path))
            seen[lab["id"]] = path
            # position in the source file -- lab files are authored in
            # teaching order, which is a far better sequence than sorting
            # ids alphabetically
            lab["seq"] = len(labs)
            labs.append(lab)
    # Curriculum order: category first, then the order the labs were written
    # in. Difficulty is deliberately NOT a sort key -- each file is authored
    # easiest-first already, and this way moving a lab in the file is all it
    # takes to change where it lands in the learning path.
    labs.sort(key=lambda l: (category_rank(l["category"]), l["category"], l["seq"]))
    for i, lab in enumerate(labs):
        lab["order"] = i
    return labs


def categories(labs):
    """Category -> count, in curriculum order (dicts keep insertion order)."""
    out = {}
    for l in sorted(labs, key=lambda x: (category_rank(x["category"]), x["category"])):
        out.setdefault(l["category"], 0)
        out[l["category"]] += 1
    return out


def summary(lab: dict) -> dict:
    """The trimmed shape the dashboard grid needs."""
    keys = ("id", "title", "category", "difficulty", "exams", "concepts",
            "estimated_minutes", "par_minutes", "points_total", "requires",
            "cluster_key", "order")
    out = {k: lab.get(k) for k in keys}
    out["nodes"] = lab["cluster"]["nodes"]
    out["checks"] = len(lab["verify"])
    out["hints"] = len(lab["hints"])
    out["blurb"] = (lab.get("blurb") or lab["scenario"].strip().split("\n")[0])[:180]
    return out
