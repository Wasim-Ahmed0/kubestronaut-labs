"""Provision and recycle the practice cluster.

kind is the provider: its nodes are real kubeadm nodes running in Docker,
which is what makes the CKA-style work (etcd snapshots, static pods,
kubelet breakage, cluster upgrades) possible at all.

Creating a cluster costs the better part of a minute, so clusters are
keyed by their spec and reused: if the next lab wants the same shape of
cluster we wipe it back to the post-install baseline instead of
rebuilding it.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import store

CLUSTER_NAME = os.environ.get("KLAB_CLUSTER_NAME", "kubestronaut")
KUBECONFIG = os.path.join(store.RUNTIME, "kubeconfig")
META = os.path.join(store.RUNTIME, "cluster.json")
BINDIR = os.path.join(store.HOME, "bin")

KIND_VERSION = "v0.30.0"
HELM_VERSION = "v3.16.3"
CALICO_URL = ("https://raw.githubusercontent.com/projectcalico/calico/"
              "v3.29.1/manifests/calico.yaml")

# Namespaces that ship with a fresh cluster and must survive a reset.
SYSTEM_NS = {"default", "kube-system", "kube-public", "kube-node-lease",
             "local-path-storage"}

NAMESPACED_KINDS = (
    "deployments,statefulsets,daemonsets,replicasets,jobs,cronjobs,pods,"
    "services,ingresses,configmaps,secrets,serviceaccounts,roles,rolebindings,"
    "persistentvolumeclaims,networkpolicies,resourcequotas,limitranges,"
    "horizontalpodautoscalers,poddisruptionbudgets,endpoints"
)
CLUSTER_KINDS = ("clusterroles", "clusterrolebindings", "crds",
                 "persistentvolumes", "storageclasses", "priorityclasses",
                 "validatingwebhookconfigurations",
                 "mutatingwebhookconfigurations", "runtimeclasses",
                 "ingressclasses")


class ClusterError(RuntimeError):
    pass


def log(msg, cb=None):
    if cb:
        cb(msg)
    else:
        print(msg, flush=True)


# --------------------------------------------------------------- tooling

def which(name):
    return shutil.which(name) or (
        os.path.join(BINDIR, name) if os.path.exists(os.path.join(BINDIR, name)) else None)


def _arch():
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


def _os():
    return "darwin" if sys.platform == "darwin" else "linux"


def docker_ok():
    exe = which("docker")
    if not exe:
        return False, "docker is not installed"
    try:
        r = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, "could not talk to docker: %s" % e
    if r.returncode != 0:
        return False, "the docker daemon is not running"
    return True, r.stdout.strip()


def _find_docker_app():
    """Locate a Docker-Desktop-shaped .app on this Mac, if there is one."""
    for name in ("Docker.app", "Docker Desktop.app", "OrbStack.app",
                 "Rancher Desktop.app"):
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                return p
    return None


def ensure_docker_running(cb=None, timeout=150):
    """Start Docker Desktop (or whatever GUI app provides it) if it is
    installed but not running, and wait for the daemon to answer.

    Returns (ok, message).  Never raises -- a Mac with no Docker at all, or
    one where the daemon just never comes up, should fall through to the
    existing preflight error message rather than crash the launcher.
    """
    ok, info = docker_ok()
    if ok:
        return True, info
    if not which("docker"):
        return False, info

    app = _find_docker_app() if sys.platform == "darwin" else None
    if not app:
        return False, info

    log("  starting %s (this can take a minute the first time)"
        % os.path.basename(app), cb)
    try:
        subprocess.Popen(["open", "-g", "-a", app],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return False, "could not launch %s: %s" % (app, e)

    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        ok, info = docker_ok()
        if ok:
            log("  docker is up", cb)
            return True, info
        if cb is None:
            dots = (dots + 1) % 4
            sys.stdout.write("\r  waiting for docker to finish starting" + "." * dots
                             + " " * 4)
            sys.stdout.flush()
        time.sleep(2)
    if cb is None:
        sys.stdout.write("\r" + " " * 60 + "\r")
    return False, "docker did not finish starting within %ds" % timeout


def _download(url, dest, cb=None):
    """Fetch a file. Prefers curl/wget because a stock macOS python often has
    no CA bundle wired up, which breaks urllib with CERTIFICATE_VERIFY_FAILED."""
    tmp = dest + ".part"
    for tool in (["curl", "-fsSL", "--retry", "2", "-o", tmp, url],
                 ["wget", "-qO", tmp, url]):
        exe = shutil.which(tool[0])
        if not exe:
            continue
        r = subprocess.run([exe] + tool[1:], capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, dest)
            return dest
    try:
        ctx = None
        try:
            import certifi
            import ssl
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        with urllib.request.urlopen(url, timeout=180, context=ctx) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        os.replace(tmp, dest)
        return dest
    except Exception as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise ClusterError("could not download %s: %s" % (url, e))


def install_kind(cb=None):
    """Fetch the kind binary into ~/.kubestronaut/bin (no sudo needed)."""
    os.makedirs(BINDIR, exist_ok=True)
    dest = os.path.join(BINDIR, "kind")
    url = "https://kind.sigs.k8s.io/dl/%s/kind-%s-%s" % (KIND_VERSION, _os(), _arch())
    log("  downloading kind %s (%s/%s)" % (KIND_VERSION, _os(), _arch()), cb)
    _download(url, dest, cb)
    os.chmod(dest, 0o755)
    return dest


def install_helm(cb=None):
    """Fetch helm into ~/.kubestronaut/bin. Used by the Helm labs."""
    os.makedirs(BINDIR, exist_ok=True)
    dest = os.path.join(BINDIR, "helm")
    if os.path.exists(dest):
        return dest
    tgz = os.path.join(BINDIR, "helm.tar.gz")
    url = "https://get.helm.sh/helm-%s-%s-%s.tar.gz" % (HELM_VERSION, _os(), _arch())
    log("  downloading helm %s" % HELM_VERSION, cb)
    _download(url, tgz, cb)
    import tarfile
    with tarfile.open(tgz) as tf:
        member = next((m for m in tf.getmembers() if m.name.endswith("/helm")), None)
        if not member:
            raise ClusterError("helm tarball did not contain a helm binary")
        member.name = "helm"
        tf.extract(member, BINDIR)
    os.chmod(dest, 0o755)
    os.unlink(tgz)
    return dest


TOOL_INSTALLERS = {"kind": install_kind, "helm": install_helm}


def ensure_extra_tools(names, cb=None):
    for name in names or []:
        if which(name):
            continue
        installer = TOOL_INSTALLERS.get(name)
        if installer:
            installer(cb)


def ensure_tools(cb=None):
    missing = []
    kind = which("kind")
    if not kind:
        kind = install_kind(cb)
    if not which("kubectl"):
        missing.append("kubectl")
    if missing:
        raise ClusterError(
            "missing required tool(s): %s -- install them and re-run" % ", ".join(missing))
    return kind


# --------------------------------------------------------------- kubectl

def _clean_env():
    """Environment for the platform's own cluster calls.

    Strips the recording shim off PATH and drops KLAB_LOG, so that check
    scripts and cluster inspection never end up in the learner's recorded
    solution -- only what they typed themselves does.
    """
    env = dict(os.environ, KUBECONFIG=KUBECONFIG)
    shim = os.path.join(store.RUNTIME, "shim")
    parts = [p for p in env.get("PATH", "").split(os.pathsep)
             if p and os.path.abspath(p) != os.path.abspath(shim)]
    env["PATH"] = os.pathsep.join([BINDIR] + parts)
    env.pop("KLAB_LOG", None)
    return env


def kubectl(args, check=False, timeout=120, input_text=None):
    env = _clean_env()
    exe = which("kubectl")
    r = subprocess.run([exe] + args, capture_output=True, text=True,
                       env=env, timeout=timeout, input=input_text)
    if check and r.returncode != 0:
        raise ClusterError("kubectl %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r


# Injected in front of every setup and verify snippet so lab authors get a
# few reliable primitives instead of reinventing retry loops.
PREAMBLE = r"""
set -o pipefail
export KLAB=1
# retry CMD... -- run until it succeeds, up to $RETRY attempts, 2s apart
retry() {
  local n="${RETRY:-15}" i=0
  while [ "$i" -lt "$n" ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 2; i=$((i+1))
  done
  return 1
}
# has KIND NAME [-n NS] -- does the object exist?
has() { kubectl get "$@" >/dev/null 2>&1; }
# jp KIND NAME JSONPATH [-n NS] -- print a field, empty on failure
jp() {
  local kind="$1" name="$2" path="$3"; shift 3
  kubectl get "$kind" "$name" "$@" -o jsonpath="$path" 2>/dev/null
}
# eq ACTUAL EXPECTED
eq() { [ "$1" = "$2" ]; }
# ready_pods NS SELECTOR -- number of Ready pods matching a selector
ready_pods() {
  kubectl -n "$1" get pods -l "$2" \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[*].ready}{"\n"}{end}' \
    2>/dev/null | grep -c '^true' || true
}
# rollout NS KIND/NAME -- wait for a workload to be available
rollout() { kubectl -n "$1" rollout status "$2" --timeout="${3:-60s}" >/dev/null 2>&1; }
"""


def sh(script, timeout=300, cwd=None, extra_env=None, preamble=True):
    """Run a bash snippet against the practice cluster."""
    env = _clean_env()
    if extra_env:
        env.update(extra_env)
    body = (PREAMBLE + "\n" + script) if preamble else script
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                          env=env, timeout=timeout, cwd=cwd)


# --------------------------------------------------------------- metadata

def read_meta():
    try:
        with open(META, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def write_meta(meta):
    os.makedirs(store.RUNTIME, exist_ok=True)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)


def cluster_exists(kind_exe=None):
    kind_exe = kind_exe or which("kind")
    if not kind_exe:
        return False
    try:
        r = subprocess.run([kind_exe, "get", "clusters"], capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return CLUSTER_NAME in r.stdout.split()


def nodes_running():
    exe = which("docker")
    try:
        r = subprocess.run([exe, "ps", "--filter",
                            "label=io.x-k8s.kind.cluster=%s" % CLUSTER_NAME,
                            "--format", "{{.Names}}"], capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    return [n for n in r.stdout.split() if n]


def refresh_kubeconfig(cb=None):
    """Rewrite the kubeconfig from kind.

    Docker publishes the API server on a random host port, and picks a NEW
    one every time the daemon restarts.  After a reboot or a Docker Desktop
    restart the saved kubeconfig therefore points at a dead port, and every
    kubectl call fails with 'connection refused' even though the cluster is
    perfectly healthy.  Re-exporting fixes it in a second, instead of
    rebuilding the cluster from scratch.
    """
    kind = which("kind")
    if not kind:
        return False
    try:
        r = subprocess.run([kind, "export", "kubeconfig", "--name", CLUSTER_NAME,
                            "--kubeconfig", KUBECONFIG],
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return False
    if r.returncode != 0:
        return False
    ok = kubectl(["get", "--raw", "/readyz"], timeout=30).returncode == 0
    if ok:
        log("  reconnected to the running cluster (Docker had moved its port)", cb)
    return ok


# --------------------------------------------------------------- creation

def _kind_config(spec):
    lines = ["kind: Cluster", "apiVersion: kind.x-k8s.io/v1alpha4"]
    if spec.get("cni") == "calico":
        lines += ["networking:", "  disableDefaultCNI: true",
                  "  podSubnet: 192.168.0.0/16"]
    lines.append("nodes:")
    if spec.get("kind_nodes"):
        # the lab supplies the whole node list verbatim (extra mounts,
        # kubeadm patches, port mappings)
        lines.append(spec["kind_nodes"].rstrip())
    else:
        image = None
        if spec.get("k8s_version"):
            image = "kindest/node:%s" % spec["k8s_version"]
        for i in range(spec["nodes"]):
            role = "control-plane" if i == 0 else "worker"
            lines.append("- role: %s" % role)
            if image:
                lines.append("  image: %s" % image)
    if spec.get("kind_patches"):
        lines.append(spec["kind_patches"].rstrip())
    return "\n".join(lines) + "\n"


def delete_cluster(cb=None):
    kind = which("kind")
    if kind and cluster_exists(kind):
        log("  tearing down the previous cluster", cb)
        try:
            subprocess.run([kind, "delete", "cluster", "--name", CLUSTER_NAME],
                           capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            log("  kind is not responding; removing the node containers directly", cb)
    # kind can leave containers behind (or hang) when Docker has been
    # restarted underneath it, so always make sure they are really gone.
    leftovers = nodes_running()
    if leftovers:
        exe = which("docker")
        subprocess.run([exe, "rm", "-f"] + leftovers,
                       capture_output=True, text=True, timeout=180)
    if os.path.exists(META):
        os.unlink(META)


def create_cluster(spec, cb=None):
    kind = ensure_tools(cb)
    ok, info = docker_ok()
    if not ok:
        raise ClusterError(info)

    delete_cluster(cb)
    cfg = _kind_config(spec)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(cfg)
        cfgpath = fh.name

    log("  creating a %d-node cluster (this takes a minute the first time)"
        % spec["nodes"], cb)
    os.makedirs(store.RUNTIME, exist_ok=True)
    env = dict(os.environ, KUBECONFIG=KUBECONFIG)
    proc = subprocess.Popen(
        [kind, "create", "cluster", "--name", CLUSTER_NAME, "--config", cfgpath,
         "--kubeconfig", KUBECONFIG, "--wait", "90s"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log("    " + line, cb)
    proc.wait()
    os.unlink(cfgpath)
    if proc.returncode != 0:
        raise ClusterError("kind failed to create the cluster (exit %d)" % proc.returncode)

    if spec.get("cni") == "calico":
        log("  installing Calico so NetworkPolicy is actually enforced", cb)
        r = kubectl(["apply", "-f", CALICO_URL], timeout=300)
        if r.returncode != 0:
            raise ClusterError("calico install failed: %s" % r.stderr.strip())
        kubectl(["-n", "kube-system", "rollout", "status", "ds/calico-node",
                 "--timeout=300s"], timeout=320)

    for item in spec.get("install") or []:
        log("  installing %s" % item.get("name", "add-on"), cb)
        res = sh(item["run"], timeout=item.get("timeout", 300))
        if res.returncode != 0:
            raise ClusterError("add-on %s failed: %s"
                               % (item.get("name"), res.stderr.strip()[:400]))

    wait_ready(cb)
    baseline = snapshot(cb)
    write_meta({"key": spec_key(spec), "spec": spec, "created_at": store.now_iso(),
                "baseline": baseline, "dirty": False})
    return True


def spec_key(spec):
    import labs as _labs
    return _labs.cluster_key(spec)


def wait_ready(cb=None, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = kubectl(["get", "nodes", "-o",
                     "jsonpath={range .items[*]}{.status.conditions[?(@.type=='Ready')].status}{'\\n'}{end}"])
        vals = [v for v in r.stdout.split() if v]
        if vals and all(v == "True" for v in vals):
            kubectl(["-n", "kube-system", "wait", "--for=condition=Ready", "pod",
                     "--all", "--timeout=120s"], timeout=140)
            return True
        time.sleep(3)
    raise ClusterError("cluster nodes did not become Ready in time")


def snapshot(cb=None):
    """Record what a pristine cluster looks like so reset knows what to keep."""
    base = {}
    r = kubectl(["get", "ns", "-o", "name"])
    base["namespaces"] = sorted(x.split("/", 1)[1] for x in r.stdout.split() if "/" in x)
    for kind_ in CLUSTER_KINDS:
        r = kubectl(["get", kind_, "-o", "name"], timeout=60)
        base[kind_] = sorted(x for x in r.stdout.split() if x)
    return base


def reset_cluster(cb=None):
    """Return a reused cluster to its baseline. Returns False if unsalvageable."""
    meta = read_meta()
    base = meta.get("baseline") or {}
    if not base:
        return False
    if not kubectl(["get", "ns"], timeout=30).returncode == 0:
        return False

    log("  recycling the running cluster", cb)
    keep_ns = set(base.get("namespaces", [])) | SYSTEM_NS
    r = kubectl(["get", "ns", "-o", "name"])
    doomed = [x.split("/", 1)[1] for x in r.stdout.split() if "/" in x]
    doomed = [n for n in doomed if n not in keep_ns]
    if doomed:
        kubectl(["delete", "ns"] + doomed + ["--wait=false"], timeout=120)

    # clear user objects out of the namespaces we keep
    for ns in sorted(SYSTEM_NS & keep_ns):
        if ns != "default":
            continue
        sh("kubectl -n default delete %s --all --ignore-not-found "
           "--wait=false >/dev/null 2>&1 || true" % NAMESPACED_KINDS, timeout=120)

    for kind_ in CLUSTER_KINDS:
        r = kubectl(["get", kind_, "-o", "name"], timeout=60)
        current = [x for x in r.stdout.split() if x]
        extra = [x for x in current if x not in set(base.get(kind_, []))]
        if extra:
            sh("kubectl delete %s --ignore-not-found --wait=false >/dev/null 2>&1 || true"
               % " ".join("'%s'" % e for e in extra), timeout=120)

    # a lab may have dropped a static pod manifest on a node; those survive
    # every kubectl-level cleanup, so scrub them back to the kubeadm set
    for node in nodes_running():
        subprocess.run(
            ["docker", "exec", node, "sh", "-c",
             "cd /etc/kubernetes/manifests 2>/dev/null && ls | "
             "grep -vE '^(etcd|kube-apiserver|kube-controller-manager|kube-scheduler)\\.yaml$' | "
             "xargs -r rm -f"],
            capture_output=True, text=True)

    # namespace deletion is async; give it a moment to actually drain
    deadline = time.time() + 60
    while time.time() < deadline and doomed:
        r = kubectl(["get", "ns", "-o", "name"])
        live = {x.split("/", 1)[1] for x in r.stdout.split() if "/" in x}
        if not (set(doomed) & live):
            break
        time.sleep(2)
    return True


def ensure_cluster(spec, force_fresh=False, cb=None):
    """Give the caller a cluster matching ``spec``.  Reuse when possible."""
    meta = read_meta()
    same = (meta.get("key") == spec_key(spec) and not meta.get("dirty"))
    if same and not force_fresh and cluster_exists() and nodes_running():
        healthy = kubectl(["get", "--raw", "/readyz"], timeout=20).returncode == 0
        if not healthy:
            # most likely just a stale port after a Docker restart
            healthy = refresh_kubeconfig(cb)
        if healthy:
            try:
                # a cluster that has just come back with Docker needs a moment
                # for its CNI and control plane pods to settle
                wait_ready(cb, timeout=150)
                ready = True
            except ClusterError:
                ready = False
            if ready and reset_cluster(cb):
                log("  cluster ready (reused in seconds instead of rebuilt)", cb)
                return "reused"
        log("  the existing cluster is unhealthy, rebuilding it", cb)
    create_cluster(spec, cb)
    return "created"


def mark_dirty():
    meta = read_meta()
    if meta:
        meta["dirty"] = True
        write_meta(meta)


def apply_setup(lab, cb=None, workdir=None):
    """Run a lab's setup steps: seed manifests, break things on purpose,
    and drop starter files into the learner's working directory."""
    for i, step in enumerate(lab.get("setup") or [], 1):
        desc = step.get("desc") or "setup step %d" % i
        if step["kind"] == "file":
            if not workdir:
                continue  # nothing to write into (validation runs headless)
            target = os.path.normpath(os.path.join(workdir, step["path"]))
            if not target.startswith(os.path.abspath(workdir)):
                raise ClusterError("setup file path escapes the workdir")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(step.get("content", ""))
        elif step["kind"] == "manifest":
            r = kubectl(["apply", "-f", "-"], input_text=step["content"], timeout=120)
            if r.returncode != 0:
                raise ClusterError("%s failed: %s" % (desc, r.stderr.strip()[:400]))
        elif step["kind"] == "shell":
            r = sh(step["run"], timeout=step.get("timeout", 300), cwd=workdir)
            if r.returncode != 0 and not step.get("ignore_errors"):
                raise ClusterError("%s failed: %s"
                                   % (desc, (r.stderr or r.stdout).strip()[:400]))
        else:
            raise ClusterError("unknown setup step kind %r" % step["kind"])
        log("    seeded: %s" % desc, cb)


def run_checks(lab, timeout_each=90):
    """Execute the lab's verify scripts. Returns a list of result dicts."""
    results = []
    for chk in lab["verify"]:
        try:
            r = sh(chk["run"], timeout=timeout_each)
            passed = r.returncode == 0
            detail = (r.stderr or r.stdout or "").strip()
        except subprocess.TimeoutExpired:
            passed, detail = False, "check timed out"
        results.append({
            "id": chk["id"],
            "desc": chk["desc"],
            "points": chk["points"],
            "passed": passed,
            "hint": chk.get("hint"),
            "detail": detail[-400:] if not passed else "",
        })
    return results
