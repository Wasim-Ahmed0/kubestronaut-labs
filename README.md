# Kubestronaut Labs

A self-contained practice platform for the five CNCF Kubernetes certifications —
**KCNA, KCSA, CKA, CKAD, CKS**.

You browse labs in a dashboard, press Start, and the cluster is built on demand
in your terminal. You solve the lab against a real Kubernetes cluster. When you
type `submit`, your work is tested by the lab's own check suite, your solution is
recorded automatically from the commands you ran, and you get a score out of 100
answering one question: *if this lab had been the whole exam, would you have
passed?*

**102 labs across 20 categories**, ordered from "never touched Kubernetes" to
CKS-level incident response.

---

## Requirements

| Tool | Why | Notes |
|---|---|---|
| **Docker** | the cluster nodes are containers | Docker Desktop, Colima, OrbStack or Rancher Desktop all work. It must be **running**. |
| **kubectl** | you need it anyway | `brew install kubectl`, or your distro's package |
| **Python 3.8+** | runs the platform | already on macOS and every Linux |
| **bash** | the lab shell | already present |

`kind` and `helm` are downloaded automatically into `~/.kubestronaut/bin` the
first time they are needed. Nothing is installed system-wide, and there are no
Python packages to install — the platform is stdlib only.

Check everything at once:

```bash
./klab doctor      # are the tools there and is Docker running?
./klab selftest    # run one lab start to finish, unattended, and prove it works
```

`selftest` builds the cluster, seeds a lab, confirms its checks fail on an
untouched cluster, applies the reference solution, confirms they all pass, and
grades it. If that comes back green, everything works.

---

## Quick start

```bash
./klab
```

That starts the dashboard, opens your browser, and waits. On macOS it will also
start Docker Desktop for you if it is not already running, and wait for it. On
Linux, start Docker yourself first (`systemctl start docker` or similar).

1. **In the browser** — pick a lab, read the scenario, press **Start lab**
2. **Switch to your terminal** — the cluster is built (about a minute the first
   time, seconds after that) and you are dropped into a lab shell
3. **Solve it** — `verify` whenever you want to see how you are doing
4. **`submit`** — you are graded, and the result appears back in the browser

The timer starts *after* the cluster is built and the scenario is seeded, so
waiting for Docker never counts against you.

---

## The lab shell

Inside a lab you get these commands:

| Command | Also | What it does |
|---|---|---|
| `verify` | `check` | run the lab's check suite and see exactly what still fails |
| `tasks` | `brief` | reprint the scenario and the task list |
| `hint` | | list the hints; `hint 1` reveals one (costs points) |
| `progress` | | elapsed time, checks passed, hints used, your workdir |
| `submit` | `finish` | verify, then finish and be graded |
| `giveup` | | stop here and save a partial attempt |
| `answer` | | show the reference solution (marks the attempt as assisted) |
| `knode` | | list cluster nodes; `knode <name>` shells into one |
| `helpme` | | print this list again |

Exam shortcuts, preloaded:

| Shortcut | Expands to |
|---|---|
| `k` | `kubectl` |
| `kn <ns>` | `kubectl config set-context --current --namespace <ns>` |
| `$do` | `--dry-run=client -o yaml` |
| `$now` | `--grace-period=0 --force` |

kubectl bash completion is on, and works through the `k` alias too.

Your shell starts in a per-attempt working directory. Anything you write there
is kept with the attempt, so you can go back and look at the YAML you wrote.

**`submit` will not end a lab you have not finished.** If a check fails it tells
you which one, gives you a nudge, and leaves the lab running so you can carry on.

---

## How grading works

Every lab carries a **check suite** — a set of scripts that inspect the real
cluster. They are what `verify` and `submit` run. A check tests the end state,
not the commands you typed, so any correct route to the answer passes.

The score out of 100 is:

| Component | Weight | What it measures |
|---|---|---|
| correctness | 70 | how many check points you earned |
| time | 15 | full marks at or under par, decaying to zero at three times par |
| hints | 10 | how many hint points you spent |
| flow | 5 | how many failed `verify` runs you burned |

A repeat attempt is discounted slightly (you had seen the problem before), and an
incomplete solution can never read as exam-ready regardless of speed.

The result is shown against each exam's real cut score — 75% for KCNA and KCSA,
66% for CKA and CKAD, 67% for CKS — so a lab tagged `CKA` tells you whether you
cleared 66%.

### Your solution is recorded for you

A shim at the front of `PATH` inside the lab shell logs every `kubectl`, `helm`
and `etcdctl` invocation, plus any YAML you piped into `apply -f -`. On `submit`
the platform writes that up as a markdown solution — the commands that changed
the cluster, the manifests you applied, and the resources that ended up existing.

It appears on the lab page in the dashboard, and there is a notes box next to it
for anything you want to add in your own words.

---

## The dashboard

**Path** — the tab you land on, and the answer to "what should I do next". Pick
one of the five certifications (or *All five*, the whole Kubestronaut journey)
and you get that track's labs as a single continuously numbered list, in the
order they should be done. The next lab you have not finished is pulled out into
a card at the top with a Start button, and marked *start here* in the list.
Finished labs collapse to a green tick and their score.

The order is deliberate: labs are grouped by category in teaching sequence, and
within a category they run easiest-first. Each lab assumes the ones above it, so
working straight down the list never leaves you missing a concept. Category
headings sit inline as dividers, but the numbering runs continuously — so
"lab 34 of 69" means something.

Your chosen track is remembered, and every lab page shows where it sits in it
("lab 10 of 23 in the CKS path").

**Labs** — every lab, filterable by exam, category, difficulty and status
(not started / in progress / completed / bookmarked / scored under 80%). Cards
show a progress bar for a lab you are part-way through, and your best score.

**Progress** — exam readiness per certification (coverage × quality, so a
high score on three of forty labs does not read as ready), progress by category
and difficulty, your weakest topics, topics you have not touched at all, a
recent-attempts log and an activity heatmap.

While a lab is running the dashboard shows a live strip with the timer and the
checks passing in real time — a background poller re-runs the checks every 30
seconds so the bar moves as you work.

---

## Running headless

On a server with no browser:

```bash
./klab --remote
```

This serves the dashboard on all interfaces and prints a LAN URL with an access
token. Open it from your laptop or phone.

Or keep it on loopback and tunnel:

```bash
ssh -L 9000:127.0.0.1:9000 user@your-server
```

Other flags: `--no-browser`, `--port 8080`, `--host 0.0.0.0`.

API access from a non-loopback address requires the token, which lives in
`~/.kubestronaut/runtime/token`.

---

## The curriculum

Labs are ordered as a learning path — this is what the **Path** tab walks you
through. Work top to bottom and the concepts build. Within a lab file, labs run
in the order they are written, so reordering a track is just a matter of moving
a block in the YAML.

| # | Category | Focus |
|---|---|---|
| 1 | Getting Started | first kubectl commands, first outage — assumes nothing |
| 2 | Core Concepts & Architecture | objects, labels, the API, the control plane |
| 3 | Cloud Native Foundations | containers, CRI, the declarative model, the CNCF landscape, the 4Cs |
| 4 | Pods & Containers | sidecars, init containers, probes, QoS, lifecycle hooks, static pods |
| 5 | Controllers, Rollouts & Scaling | Deployments, DaemonSets, StatefulSets, Jobs, CronJobs, HPA, PDB |
| 6 | Configuration & Resource Governance | ConfigMaps, Secrets, subPath, quotas, LimitRanges |
| 7 | Services & Networking | ClusterIP/NodePort, DNS, Ingress, NetworkPolicy (with real enforcement) |
| 8 | Storage | volumes, PV/PVC, StorageClasses, reclaim policies, binding failures |
| 9 | Application Design & Deployment Patterns | blue/green, canary, ambassador, adapter, native sidecars |
| 10 | Observability & Logging | logs, metrics-server, events, instrumenting an app |
| 11 | Scheduling & Node Management | affinity, taints, topology spread, drain, priority, manual scheduling |
| 12 | Troubleshooting | crash loops, a dead API server, a NotReady node, broken DNS |
| 13 | Cluster Architecture & Lifecycle | etcd backup, certificates and the CSR API, kubeconfig, upgrades, kubelet |
| 14 | Helm & Kustomize | releases and rollbacks, writing a chart, overlays |
| 15 | Extending Kubernetes | CRDs, operator RBAC, ownerReferences and finalizers |
| 16 | Identity, RBAC & Access Control | Roles, ClusterRoles, ServiceAccount tokens, privilege audits |
| 17 | Security Hardening | securityContext, Pod Security Admission, capabilities, secrets hygiene |
| 18 | Supply Chain Security | image scanning, minimal images, registry allowlists, manifest review |
| 19 | Runtime & Platform Security | seccomp, audit logging, encryption at rest, admission policy |
| 20 | Production Scenarios | multi-fault incidents: load, noisy neighbours, cascading config, compromise |

Filter by exam in the dashboard to see only what a given certification covers.
Many labs count towards several.

---

## Command reference

### Starting it

```bash
./klab                       # dashboard + wait for you to pick a lab
./klab --remote              # serve on all interfaces (headless machines)
./klab --no-browser          # do not try to open a browser
./klab --port 8080            # use a different port (default 9000)
./klab --host 192.168.1.10   # bind a specific address
./klab --no-install          # never auto-download kind or helm
```

Flags can be combined: `./klab --remote --port 8080`.

### Everything else

| Command | Aliases | What it does |
|---|---|---|
| `./klab doctor` | | check docker, kubectl, kind, helm and the catalogue; installs kind if missing |
| `./klab selftest` | | run one lab end to end, unattended, and prove the install works |
| `./klab labs` | `list` | print every lab, grouped by category, with difficulty and exams |
| `./klab validate` | `test` | schema and `bash -n` checks over the whole catalogue |
| `./klab cleanup` | `clean` | delete the practice cluster (your progress is untouched) |
| `./klab help` | `-h`, `--help` | the short version of this table |

### Validating labs

```bash
./klab validate                          # static checks, no cluster needed (seconds)
./klab validate --only storage           # match a lab id, title or category
./klab validate --live                   # build real clusters and seed each lab
./klab validate --live --solve           # ...then apply each reference solution
./klab validate --live --solve --only ctrl-hpa
```

`--live` also asserts that a lab does **not** already pass before you touch it —
a check that passes on an untouched cluster is measuring nothing, and gets
flagged.

---

## Where things live

```
klab                    launcher
engine/                 the platform (Python, stdlib only)
  labctl.py             terminal runner and lab shell commands
  server.py             dashboard API
  cluster.py            kind lifecycle, cluster reuse, check execution
  grader.py             scoring and exam verdicts
  recorder.py           automatic solution capture
  validate.py           catalogue tests
  selftest.py           end-to-end install check
  miniyaml.py           dependency-free YAML subset parser
labs/*.yaml             the lab catalogue
web/                    the dashboard

~/.kubestronaut/
  state.json            your progress — the only file that matters, back it up
  attempts/<lab>/attempt-NN/
    work/               your working directory for that attempt
    commands.log        raw command capture
    solution.md         the generated write-up
  bin/                  kind, helm
  runtime/              kubeconfig, cluster metadata, dashboard token
```

Your progress is one file: `~/.kubestronaut/state.json`. Back that up and you
can move to another machine, or reinstall everything, without losing anything.
Deleting the repo does not touch it, and `./klab cleanup` removes the cluster
but not your history.

---

## Writing your own labs

Drop a YAML file in `labs/`. The schema:

```yaml
category: My Category
labs:
  - id: my-lab
    title: What the learner sees
    difficulty: intro | easy | medium | hard | expert
    exams: [CKA, CKAD]
    concepts: [things, this, teaches]
    par_minutes: 10
    blurb: One line for the card.
    scenario: |
      Markdown. Explain the situation and why it matters.
    docs:
      - https://kubernetes.io/docs/...      # what the exam lets you look at
    tasks:
      - text: "What they have to do."
    setup:                                   # runs before the timer starts
      - kind: shell
        run: kubectl create ns demo
      - kind: manifest
        content: |
          apiVersion: v1
          ...
      - kind: file                           # starter file in their workdir
        path: broken.yaml
        content: |
          ...
    verify:                                  # THE TESTS — exit 0 means pass
      - id: c1
        desc: What this check proves
        points: 50
        hint: shown when this check fails
        run: |
          eq "$(jp deploy web '{.spec.replicas}' -n demo)" "3"
    hints:
      - title: A nudge
        cost: 8
        text: |
          Markdown.
    solution: |
      The reference answer, shown on request.
    solve_script: |
      # optional: solves the lab non-interactively, used by ./klab validate --live
```

Check scripts run in bash against the live cluster with a few helpers preloaded:

- `jp KIND NAME JSONPATH [-n NS]` — print a field
- `has KIND NAME [-n NS]` — does it exist
- `eq A B` — string equality
- `retry CMD...` — retry until it succeeds (`RETRY=30` to change the attempt count)
- `rollout NS KIND/NAME [timeout]` — wait for a workload
- `ready_pods NS SELECTOR` — count ready pods

A cluster spec is optional and defaults to a single node:

```yaml
    cluster:
      nodes: 3                 # up to 4
      cni: calico              # for labs where NetworkPolicy must really be enforced
      k8s_version: v1.31.0     # pin a node image
      kind_nodes: |            # full control over the kind node list
        - role: control-plane
          extraPortMappings: [...]
      install:                 # add-ons applied after the cluster is up
        - name: metrics-server
          run: kubectl apply -f ...
    requires_tools: [helm]     # fetched automatically if missing
    destructive: true          # rebuild the cluster after this lab
```

Then:

```bash
./klab validate                        # schema + bash syntax over everything
./klab validate --live --solve --only my-lab
```

`--live --solve` builds the real cluster, seeds the lab, checks that it does
**not** already pass (a check that passes before you do anything is a broken
check), applies your `solve_script`, and requires every check to flip to passing.
That is the test that proves a lab is actually solvable.

---

## Notes and limitations

- Clusters are reused between labs that want the same shape, and recycled back to
  a clean baseline rather than rebuilt — so the second lab of a session starts in
  seconds. A lab marked `destructive` forces a rebuild afterwards.
- NetworkPolicy labs run Calico so policies are genuinely enforced. Those
  clusters take longer to build.
- Some CKS labs edit control plane static pod manifests. That is the real task,
  and it is why they are marked destructive.
- A few labs are more faithful on Linux than macOS (AppArmor, for instance).
  Anything affected is tagged `requires:` and the dashboard warns you.
- Exam mode — a timed multi-question simulation — is not built yet. It is the
  obvious next thing.
