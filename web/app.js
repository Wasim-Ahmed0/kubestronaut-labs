/* Kubestronaut lab dashboard -- vanilla, no build step. */
(() => {
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t")
  || localStorage.getItem("klab_token") || "";
if (TOKEN) localStorage.setItem("klab_token", TOKEN);

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    n.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
  return n;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-Klab-Token": TOKEN,
               ...(opts.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || ("HTTP " + res.status));
  return body;
}
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });

function toast(msg, ms = 3200) {
  const t = el("div", { class: "toast" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), ms);
}

/* ------------------------------------------------------------ formatting */

const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function md(src) {
  if (!src) return "";
  const blocks = [];
  let s = String(src).replace(/\r\n/g, "\n");
  // pull fenced code out first so nothing else mangles it
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push('<pre><code class="lang-' + esc(lang) + '">'
      + esc(code.replace(/\n$/, "")) + "</code></pre>");
    return "\n@@KLABCODE" + (blocks.length - 1) + "@@\n";
  });
  s = esc(s);
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener">$2</a>');

  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };
  for (const raw of s.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (/^@@KLABCODE\d+@@$/.test(line.trim())) {
      closeList(); out.push(line.trim()); continue;
    }
    if (!line.trim()) { closeList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList();
      const lvl = m[1].length + 1;
      out.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">");
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push("<li>" + inline(line.replace(/^\s*[-*]\s+/, "")) + "</li>");
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push("<li>" + inline(line.replace(/^\s*\d+[.)]\s+/, "")) + "</li>");
      continue;
    }
    if (/^&gt;\s?/.test(line)) {
      closeList();
      out.push("<blockquote>" + inline(line.replace(/^&gt;\s?/, "")) + "</blockquote>");
      continue;
    }
    closeList();
    out.push("<p>" + inline(line) + "</p>");
  }
  closeList();
  return out.join("\n").replace(/@@KLABCODE(\d+)@@/g, (_, i) => blocks[+i]);
}

const hms = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? (h + "h " + String(m).padStart(2, "0") + "m")
           : (m + ":" + String(x).padStart(2, "0"));
};
const scoreClass = (n) => (n === null || n === undefined)
  ? "" : (n >= 80 ? "s-hi" : n >= 66 ? "s-mid" : "s-lo");
const scoreVar = (n) => (n >= 80 ? "ok" : n >= 66 ? "warn" : "bad");
const track = (frac, cls = "") => el("div", { class: "track " + cls },
  el("i", { style: "width:" + Math.round((frac || 0) * 100) + "%" }));

/* ------------------------------------------------------------ state */

const DEFAULT_FILTERS = () => ({
  q: "", exams: new Set(), cat: "", diff: "", status: "", sort: "curriculum",
});

const S = {
  catalogue: null,
  filters: DEFAULT_FILTERS(),
  session: null,
  runner: false,
  path: localStorage.getItem("klab_path") || null,
};

const savedF = JSON.parse(localStorage.getItem("klab_filters") || "null");
if (savedF) Object.assign(S.filters, savedF, { exams: new Set(savedF.exams || []) });
const saveFilters = () => localStorage.setItem("klab_filters",
  JSON.stringify(Object.assign({}, S.filters, { exams: [...S.filters.exams] })));

async function loadCatalogue() {
  const c = await api("/api/catalogue");
  if (c.error) throw new Error(c.error);
  S.catalogue = c; S.session = c.session; S.runner = c.runner;
  return c;
}

/* ------------------------------------------------------------ chrome */

function renderChrome() {
  const c = S.catalogue;
  const r = $("#runner");
  r.classList.toggle("live", !!S.runner);
  $(".txt", r).textContent = S.runner ? "terminal ready" : "terminal not running";
  for (const a of document.querySelectorAll(".nav a")) {
    a.classList.toggle("active", (location.hash || "#/path").startsWith("#/" + a.dataset.route));
  }
  if (c) {
    const done = c.labs.filter((l) => l.state.status === "completed").length;
    const pct = c.labs.length ? done / c.labs.length : 0;
    $("#overall").replaceChildren(
      track(pct, pct === 1 ? "ok" : ""),
      el("span", {}, done + "/" + c.labs.length));
    $("#foot-count").textContent = c.labs.length + " labs across "
      + Object.keys(c.categories).length + " categories";
  }
  renderSessionStrip();
}

let timerHandle = null;
function renderSessionStrip() {
  const strip = $("#session-strip");
  const s = S.session;
  if (!s || s.state !== "running") {
    strip.classList.add("hidden");
    if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
    return;
  }
  strip.classList.remove("hidden");
  const checks = s.checks || [];
  const passed = checks.filter((c) => c.passed).length;
  strip.replaceChildren(
    el("span", { class: "live-dot" }),
    el("b", {}, s.title || s.lab_id),
    el("span", { class: "tag" }, "attempt #" + s.attempt),
    el("span", { style: "font-size:12.5px;color:var(--tx-2)" },
      checks.length ? (passed + "/" + checks.length + " checks passing")
                    : "solve it in your terminal"),
    el("div", { style: "width:150px" },
      track(s.progress || 0, s.progress === 1 ? "ok" : "warn")),
    el("span", { class: "timer", id: "live-timer" }, "0:00"),
    el("a", { class: "btn sm ghost", href: "#/lab/" + s.lab_id }, "open lab"),
  );
  const tick = () => {
    const t = $("#live-timer");
    if (t) t.textContent = hms(Date.now() / 1000 - s.started_ts);
  };
  tick();
  if (timerHandle) clearInterval(timerHandle);
  timerHandle = setInterval(tick, 1000);
}

/* ------------------------------------------------------------ labs page */

function labCard(l) {
  const st = l.state;
  const cls = st.status === "completed" ? "done"
    : st.status === "in_progress" ? "wip" : "";
  const showBar = st.status === "in_progress" && st.progress > 0;
  const hasScore = st.best_score !== null && st.best_score !== undefined;
  return el("div", {
    class: "card " + cls,
    onclick: (e) => {
      if (e.target.classList.contains("star")) return;
      location.hash = "#/lab/" + l.id;
    },
  },
    el("div", { class: "row" },
      el("h4", {}, l.title),
      hasScore ? el("span", { class: "score-badge " + scoreClass(st.best_score) },
        st.best_score + "%") : null,
      el("span", {
        class: "star " + (st.bookmarked ? "on" : ""), title: "bookmark",
        onclick: async (e) => {
          e.stopPropagation();
          const next = !st.bookmarked;
          try {
            await post("/api/bookmark", { lab_id: l.id, bookmarked: next });
            st.bookmarked = next;
            e.target.classList.toggle("on", next);
            e.target.textContent = next ? "★" : "☆";
          } catch (err) { toast("could not save bookmark"); }
        },
      }, st.bookmarked ? "★" : "☆"),
    ),
    el("p", { class: "blurb" }, l.blurb),
    el("div", { class: "tags" },
      ...l.exams.map((x) => el("span", { class: "tag exam " + x }, x)),
      el("span", { class: "tag cat" }, l.category),
    ),
    showBar ? el("div", { class: "trackrow" }, track(st.progress, "warn"),
      el("span", { style: "font-size:11px;color:var(--tx-3)" },
        Math.round(st.progress * 100) + "%")) : null,
    el("div", { class: "meta" },
      el("span", { class: "diff " + l.difficulty }, l.difficulty),
      el("span", {}, "~" + Math.round(l.estimated_minutes) + " min"),
      el("span", {}, l.checks + " checks"),
      l.nodes > 1 ? el("span", {}, l.nodes + " nodes") : null,
      st.attempts ? el("span", { style: "margin-left:auto" },
        st.attempts + (st.attempts > 1 ? " attempts" : " attempt")) : null,
    ),
  );
}

const DIFF_ORDER = ["intro", "easy", "medium", "hard", "expert"];

/* The Kubestronaut journey, in the order the certifications are normally
   taken. "ALL" is the whole thing end to end. */
const EXAM_PATHS = ["KCNA", "KCSA", "CKA", "CKAD", "CKS"];
const EXAM_INFO = {
  KCNA: { full: "Kubernetes and Cloud Native Associate",
          note: "The foundations. Mostly conceptual, no prior experience needed." },
  KCSA: { full: "Kubernetes and Cloud Native Security Associate",
          note: "Security concepts and the threat model. Builds straight on KCNA." },
  CKA:  { full: "Certified Kubernetes Administrator",
          note: "Running and repairing clusters. The big hands-on one." },
  CKAD: { full: "Certified Kubernetes Application Developer",
          note: "Building and shipping workloads. Heavy overlap with CKA." },
  CKS:  { full: "Certified Kubernetes Security Specialist",
          note: "Hardening and incident response. Requires a valid CKA." },
  ALL:  { full: "The full Kubestronaut journey",
          note: "Every lab, in curriculum order, across all five certifications." },
};

function filtered() {
  const f = S.filters;
  const q = f.q.trim().toLowerCase();
  const list = S.catalogue.labs.filter((l) => {
    const st = l.state;
    if (f.exams.size && !l.exams.some((e) => f.exams.has(e))) return false;
    if (f.cat && l.category !== f.cat) return false;
    if (f.diff && l.difficulty !== f.diff) return false;
    if (f.status === "completed" && st.status !== "completed") return false;
    if (f.status === "in_progress" && st.status !== "in_progress") return false;
    if (f.status === "not_started" && st.status !== "not_started") return false;
    if (f.status === "todo" && st.status === "completed") return false;
    if (f.status === "bookmarked" && !st.bookmarked) return false;
    if (f.status === "weak"
      && !(st.best_score !== null && st.best_score !== undefined && st.best_score < 80)) {
      return false;
    }
    if (q) {
      const hay = [l.id, l.title, l.category, l.blurb,
        l.concepts.join(" "), l.exams.join(" ")].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const by = {
    curriculum: (a, b) => (a.order || 0) - (b.order || 0),
    difficulty: (a, b) => DIFF_ORDER.indexOf(a.difficulty) - DIFF_ORDER.indexOf(b.difficulty)
      || a.id.localeCompare(b.id),
    shortest: (a, b) => a.estimated_minutes - b.estimated_minutes,
    score: (a, b) => ((a.state.best_score === null || a.state.best_score === undefined)
      ? 999 : a.state.best_score)
      - ((b.state.best_score === null || b.state.best_score === undefined)
        ? 999 : b.state.best_score),
  };
  return list.sort(by[f.sort] || by.curriculum);
}

function viewLabs() {
  const c = S.catalogue;
  const f = S.filters;
  const rerender = () => { saveFilters(); viewLabs(); };

  const controls = el("div", { class: "controls" },
    el("label", { class: "search" }, "⌕",
      el("input", {
        type: "search", placeholder: "search labs, topics, concepts…", value: f.q,
        oninput: (e) => { f.q = e.target.value; saveFilters(); repaintGrid(); },
      })),
    el("div", { class: "chips" }, ...c.exams.map((x) =>
      el("span", {
        class: "chip " + x + (f.exams.has(x) ? " on" : ""),
        onclick: () => {
          if (f.exams.has(x)) f.exams.delete(x); else f.exams.add(x);
          rerender();
        },
      }, x))),
    el("select", { onchange: (e) => { f.cat = e.target.value; rerender(); } },
      el("option", { value: "" }, "All categories"),
      ...Object.entries(c.categories).map(([k, n]) =>
        el("option", { value: k, selected: f.cat === k }, k + " (" + n + ")"))),
    el("select", { onchange: (e) => { f.diff = e.target.value; rerender(); } },
      el("option", { value: "" }, "Any difficulty"),
      ...c.difficulties.map((d) => el("option", { value: d, selected: f.diff === d }, d))),
    el("select", { onchange: (e) => { f.status = e.target.value; rerender(); } },
      ...[["", "All labs"], ["todo", "Not completed"], ["not_started", "Not started"],
          ["in_progress", "In progress"], ["completed", "Completed"],
          ["bookmarked", "Bookmarked"], ["weak", "Scored under 80%"]]
        .map(([v, t]) => el("option", { value: v, selected: f.status === v }, t))),
    el("select", { onchange: (e) => { f.sort = e.target.value; rerender(); } },
      ...[["curriculum", "Learning path order"], ["difficulty", "Easiest first"],
          ["shortest", "Quickest first"], ["score", "Weakest score first"]]
        .map(([v, t]) => el("option", { value: v, selected: f.sort === v }, t))),
    el("button", {
      class: "btn sm ghost",
      onclick: () => { S.filters = DEFAULT_FILTERS(); rerender(); },
    }, "reset"),
  );

  $("#view").replaceChildren(
    el("h1", {}, "Labs"),
    el("p", { class: "sub" },
      "Pick a lab, press Start, then switch to your terminal — the cluster is built on demand."),
    S.runner ? null : el("div", { class: "banner-note" }, "⚠️",
      el("div", { html: "The klab terminal is not listening. Run <code>./klab</code> "
        + "in a terminal so a lab can start a cluster." })),
    controls,
    el("div", { id: "gridwrap" }));
  repaintGrid();
}

function repaintGrid() {
  const wrap = $("#gridwrap");
  if (!wrap) return;
  const list = filtered();
  wrap.replaceChildren(
    el("p", { class: "sub", style: "margin:0 0 12px" },
      list.length + (list.length === 1 ? " lab" : " labs")),
    list.length
      ? el("div", { class: "grid" }, ...list.map(labCard))
      : el("div", { class: "empty" }, el("div", { class: "big" }, "⌕"),
          el("div", {}, "No labs match those filters.")),
  );
}

/* ------------------------------------------------------------ path page */

/* The labs for one track, in curriculum order, numbered continuously. */
function pathLabs(track) {
  const all = (S.catalogue && S.catalogue.labs) || [];
  const picked = track === "ALL"
    ? all.slice()
    : all.filter((l) => l.exams.includes(track));
  picked.sort((a, b) => (a.order || 0) - (b.order || 0));
  return picked;
}

function pathStats(labs) {
  const done = labs.filter((l) => l.state.status === "completed");
  const scored = done.map((l) => l.state.best_score)
    .filter((n) => n !== null && n !== undefined);
  const remaining = labs.filter((l) => l.state.status !== "completed");
  return {
    total: labs.length,
    done: done.length,
    pct: labs.length ? Math.round((done.length / labs.length) * 100) : 0,
    avg: scored.length
      ? Math.round(scored.reduce((a, b) => a + b, 0) / scored.length) : null,
    minutesLeft: remaining.reduce((a, l) => a + (l.estimated_minutes || 0), 0),
    next: remaining[0] || null,
  };
}

/* Default to the first certification that is not finished yet, so the page
   opens on whatever you are actually working towards. */
function defaultTrack() {
  if (S.path) return S.path;
  for (const ex of EXAM_PATHS) {
    const st = pathStats(pathLabs(ex));
    if (st.done < st.total) return ex;
  }
  return "ALL";
}

function fmtMinutes(m) {
  m = Math.round(m || 0);
  if (m < 60) return m + " min";
  const h = Math.floor(m / 60), r = m % 60;
  return r ? h + "h " + r + "m" : h + "h";
}

function pathRow(lab, n, isNext) {
  const st = lab.state;
  const done = st.status === "completed";
  const wip = st.status === "in_progress";
  const hasScore = st.best_score !== null && st.best_score !== undefined;
  return el("div", {
    class: "prow " + (done ? "done" : "") + (wip ? " wip" : "") + (isNext ? " next" : ""),
    onclick: () => { location.hash = "#/lab/" + lab.id; },
  },
    el("div", { class: "pnum" }, done ? "✓" : String(n)),
    el("div", { class: "pmain" },
      el("div", { class: "ptitle" }, lab.title,
        isNext ? el("span", { class: "chip-next" }, "start here") : null),
      el("div", { class: "pmeta" },
        el("span", { class: "diff " + lab.difficulty }, lab.difficulty),
        el("span", {}, "~" + Math.round(lab.estimated_minutes) + " min"),
        el("span", {}, lab.checks + " checks"),
        wip && st.progress > 0
          ? el("span", { style: "color:var(--warn)" },
              Math.round(st.progress * 100) + "% done")
          : null,
        st.attempts ? el("span", {},
          st.attempts + (st.attempts > 1 ? " attempts" : " attempt")) : null,
      )),
    el("div", { class: "pright" },
      hasScore
        ? el("span", { class: "score-badge " + scoreClass(st.best_score) },
            st.best_score + "%")
        : el("span", { class: "pstatus" }, wip ? "in progress" : "not started")),
  );
}

function viewPath() {
  const track = defaultTrack();
  const labs = pathLabs(track);
  const stats = pathStats(labs);
  const info = EXAM_INFO[track] || {};

  const selector = el("div", { class: "trackbar" },
    ...[...EXAM_PATHS, "ALL"].map((ex) => {
      const st = pathStats(pathLabs(ex));
      return el("button", {
        class: "trackbtn " + ex + (ex === track ? " on" : ""),
        onclick: () => {
          S.path = ex;
          localStorage.setItem("klab_path", ex);
          viewPath();
        },
      },
        el("b", {}, ex === "ALL" ? "All five" : ex),
        el("span", { class: "tb-sub" }, st.done + "/" + st.total),
        el("div", { class: "tb-track" },
          el("i", { style: "width:" + st.pct + "%" })));
    }));

  const hero = stats.next
    ? el("div", { class: "panel path-hero" },
        el("div", { class: "ph-label" },
          stats.done ? "Pick up where you left off" : "Start here"),
        el("div", { class: "ph-title" }, stats.next.title),
        el("div", { class: "ph-blurb" }, stats.next.blurb),
        el("div", { class: "ph-meta" },
          el("span", { class: "tag" },
            "lab " + (labs.indexOf(stats.next) + 1) + " of " + stats.total),
          el("span", { class: "tag" }, stats.next.category),
          el("span", { class: "tag diff " + stats.next.difficulty },
            stats.next.difficulty),
          el("span", { class: "tag" },
            "~" + Math.round(stats.next.estimated_minutes) + " min")),
        el("a", { class: "btn primary", style: "margin-top:14px",
                  href: "#/lab/" + stats.next.id }, "Open this lab  →"))
    : el("div", { class: "panel path-hero done" },
        el("div", { class: "ph-label" }, "Track complete"),
        el("div", { class: "ph-title" },
          "You have finished every lab in this track"),
        el("div", { class: "ph-blurb" },
          "Average score " + (stats.avg === null ? "—" : stats.avg + "%")
          + ". Re-run anything you scored under 80% to tighten it up."));

  // continuous numbering, with a quiet divider whenever the category changes
  const rows = [];
  let lastCat = null;
  labs.forEach((lab, i) => {
    if (lab.category !== lastCat) {
      lastCat = lab.category;
      rows.push(el("div", { class: "pcat" }, lab.category));
    }
    rows.push(pathRow(lab, i + 1, stats.next && lab.id === stats.next.id));
  });

  $("#view").replaceChildren(
    el("h1", {}, "Learning path"),
    el("p", { class: "sub" },
      "Pick a certification and work down the list. The order builds on itself, "
      + "so each lab assumes the ones above it."),
    selector,
    el("div", { class: "panel path-summary" },
      el("div", {},
        el("div", { class: "ps-name" }, track === "ALL" ? "All five certifications" : track),
        el("div", { class: "ps-full" }, info.full || ""),
        el("div", { class: "ps-note" }, info.note || "")),
      el("div", { class: "ps-stats" },
        el("div", {}, el("b", {}, stats.done + " / " + stats.total),
          el("span", {}, "labs done")),
        el("div", {}, el("b", { class: scoreClass(stats.avg) },
          stats.avg === null ? "—" : stats.avg + "%"), el("span", {}, "avg score")),
        el("div", {}, el("b", {}, fmtMinutes(stats.minutesLeft)),
          el("span", {}, "left to go")))),
    hero,
    el("div", { class: "panel" }, el("div", { class: "plist" }, ...rows)),
  );
}

/* ------------------------------------------------------------ lab page */

async function viewLab(id) {
  const view = $("#view");
  let d;
  try {
    d = await api("/api/lab/" + encodeURIComponent(id));
  } catch (e) {
    view.replaceChildren(el("div", { class: "empty" }, "Unknown lab: " + id));
    return;
  }

  const lab = d.lab, st = d.state, attempts = d.attempts || [];
  const sess = d.session;
  const running = sess && sess.lab_id === id && sess.state === "running";
  const otherRunning = sess && sess.lab_id !== id && sess.state === "running";

  const startBtn = el("button", {
    class: "btn primary", disabled: running || otherRunning,
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        const fresh = $("#fresh") && $("#fresh").checked;
        await post("/api/start", { lab_id: id, fresh: fresh });
        toast("Sent to your terminal — switch to it now");
        pulse();
      } catch (err) {
        toast("Could not start: " + err.message);
        e.target.disabled = false;
      }
    },
  }, running ? "Running in your terminal"
     : attempts.length ? "▶  Reattempt this lab" : "▶  Start lab");

  const clusterDesc = lab.cluster.nodes + (lab.cluster.nodes > 1 ? " nodes" : " node")
    + (lab.cluster.cni === "calico" ? " · Calico CNI (policy enforced)" : "")
    + (lab.cluster.k8s_version ? " · Kubernetes " + lab.cluster.k8s_version : "");

  // where this lab sits in the track you are currently following
  let position = null;
  const track = defaultTrack();
  if (S.catalogue) {
    const seq = pathLabs(track);
    const idx = seq.findIndex((l) => l.id === id);
    if (idx >= 0) {
      position = el("a", {
        class: "crumb-pos", href: "#/path",
        title: "see the whole " + track + " path",
      }, "lab " + (idx + 1) + " of " + seq.length + " in the "
         + (track === "ALL" ? "full" : track) + " path");
    }
  }

  const header = el("div", { class: "panel" },
    el("div", { class: "crumb" },
      el("a", { href: "#/path" }, "Path"), " / ",
      el("a", { href: "#/labs" }, "Labs"), " / ", lab.category,
      position ? el("span", {}, "  ·  ") : null, position),
    el("h1", {}, lab.title),
    el("div", { class: "tags", style: "margin-bottom:12px" },
      ...lab.exams.map((x) => el("span", { class: "tag exam " + x }, x)),
      el("span", { class: "tag" }, lab.category),
      el("span", { class: "tag diff " + lab.difficulty }, lab.difficulty),
      ...lab.concepts.map((cn) => el("span", { class: "tag" }, cn)),
    ),
    el("dl", { class: "kv", style: "margin-bottom:14px" },
      el("dt", {}, "Target time"),
      el("dd", {}, lab.par_minutes + " min par · ~"
        + Math.round(lab.estimated_minutes) + " min typical"),
      el("dt", {}, "Cluster"), el("dd", {}, clusterDesc),
      el("dt", {}, "Graded on"),
      el("dd", {}, lab.checks.length + " checks · " + lab.points_total + " points"),
      el("dt", {}, "Best score"),
      el("dd", { class: scoreClass(st.best_score), style: "font-weight:700" },
        (st.best_score === null || st.best_score === undefined) ? "—" : st.best_score + "%"),
      el("dt", {}, "Time invested"), el("dd", {}, hms(st.total_time_s)),
    ),
    (lab.requires && lab.requires.length)
      ? el("div", { class: "banner-note" }, "⚠️",
          el("div", {}, "This lab really wants: " + lab.requires.join(", ")
            + ". It will still run, but some checks may not behave the same way."))
      : null,
    el("div", { style: "display:flex; gap:12px; align-items:center; flex-wrap:wrap" },
      startBtn,
      el("label", {
        style: "font-size:12.5px;color:var(--tx-3);display:flex;gap:6px;align-items:center",
      }, el("input", { type: "checkbox", id: "fresh" }), "rebuild the cluster from scratch"),
    ),
    running ? el("div", { class: "panel terminal-cta", style: "margin:14px 0 0" },
        el("b", {}, "This lab is live in your terminal — switch to it"),
        el("div", { class: "mono", html:
          "verify   <span style='color:var(--tx-3)'>see what still fails</span><br>"
          + "hint 1   <span style='color:var(--tx-3)'>a nudge (costs points)</span><br>"
          + "submit   <span style='color:var(--tx-3)'>finish and get graded</span>" }))
      : otherRunning ? el("div", { class: "banner-note" }, "⏳",
          el("div", { html: 'Another lab is running: <a href="#/lab/'
            + esc(sess.lab_id) + '">' + esc(sess.lab_id)
            + "</a>. Finish or give up on that one first." }))
      : null,
  );

  const brief = el("div", { class: "panel" },
    el("h2", {}, "Scenario"),
    el("div", { class: "md", html: md(lab.scenario) }),
    (lab.tasks && lab.tasks.length) ? el("div", {},
      el("h2", {}, "What you have to do"),
      el("ol", { class: "md" }, ...lab.tasks.map((t) =>
        el("li", { html: md(t.text).replace(/^<p>/, "").replace(/<\/p>$/, "") })))) : null,
    (lab.docs && lab.docs.length) ? el("div", {},
      el("h3", {}, "Docs you are allowed to use in the real exam"),
      el("ul", { class: "md" }, ...lab.docs.map((u) =>
        el("li", {}, el("a", { href: u, target: "_blank", rel: "noopener" }, u))))) : null,
  );

  const liveChecks = running && sess.checks && sess.checks.length ? sess.checks : null;
  const checks = el("div", { class: "panel" },
    el("h2", {}, "How you are graded"),
    el("ul", { class: "checklist" }, ...lab.checks.map((c) => {
      const live = liveChecks && liveChecks.find((x) => x.id === c.id);
      return el("li", {},
        el("span", { class: "mark " + (live ? (live.passed ? "pass" : "fail") : "") },
          live ? (live.passed ? "✓" : "✗") : ""),
        el("span", {}, c.desc),
        el("span", { class: "pts" }, c.points + " pts"));
    })),
  );

  const hints = (lab.hints && lab.hints.length) ? el("div", { class: "panel" },
    el("h2", {}, "Hints"),
    el("p", { class: "sub", style: "margin-bottom:12px" },
      "Reveal one in the terminal with hint 1 — each costs points. "
      + "They are listed here so you can see what is on offer before you commit."),
    ...lab.hints.map((h) => el("details", { class: "hintbox" },
      el("summary", {}, h.title + " — costs " + h.cost + " points"),
      el("div", { class: "md", html: md(h.text) }))),
  ) : null;

  const refSolution = lab.solution ? el("div", { class: "panel" },
    el("details", { class: "hintbox", style: "background:transparent;border:0;padding:0" },
      el("summary", { style: "color:var(--tx-2)" }, "Show the reference solution"),
      el("div", { class: "md", html: md(lab.solution) })),
  ) : null;

  const history = el("div", { class: "panel" },
    el("h2", {}, "Your attempts (" + attempts.length + ")"),
    attempts.length
      ? el("div", {}, ...attempts.slice().reverse().map((a) => attemptBlock(lab, a)))
      : el("p", { class: "sub" }, "No attempts yet. Press Start lab above."),
  );

  view.replaceChildren(header,
    el("div", { class: "cols" },
      el("div", {}, brief, history),
      el("div", {}, checks, hints, refSolution)));
}

function attemptBlock(lab, a) {
  const passed = a.outcome === "passed";
  const bd = a.breakdown || {};
  const rows = [["correctness", bd.correctness], ["time", bd.time],
                ["hints", bd.hints], ["flow", bd.flow]];
  const notes = el("textarea", {
    placeholder: "Your own notes — what tripped you up, what to remember next time…",
  });
  notes.value = a.notes || "";
  const hasScore = a.score !== null && a.score !== undefined;

  return el("details", { class: "attempt" },
    el("summary", {},
      el("span", { class: "score-badge " + scoreClass(a.score) },
        hasScore ? a.score + "%" : "—"),
      el("b", {}, "Attempt #" + a.n),
      el("span", { style: "color:var(--tx-3);font-size:12.5px" },
        (passed ? "completed" : "unfinished") + " · " + hms(a.duration_s)
        + " · " + a.checks_passed + "/" + a.checks_total + " checks"),
      el("span", { style: "margin-left:auto;color:var(--tx-3);font-size:12px" },
        (a.ended_at || "").replace("T", " ").slice(0, 16)),
    ),
    el("div", { class: "body" },
      a.verdict ? el("div", { class: "verdict" },
        el("div", { class: "big-score", style: "color:var(--" + scoreVar(a.score) + ")" },
          a.score + "%"),
        el("div", {},
          el("b", {}, a.verdict.label),
          el("div", { style: "color:var(--tx-2);font-size:13px" }, a.verdict.blurb),
          el("div", { class: "exam-verdicts" },
            ...Object.entries(a.verdict.per_exam || {}).map(([ex, v]) =>
              el("span", { class: "ev " + (v.would_pass ? "pass" : "fail") },
                ex + ": " + (v.would_pass ? "pass" : "fail") + " ("
                + (v.margin >= 0 ? "+" : "") + v.margin + " vs " + v.pass_mark + "%)"))),
        )) : null,
      el("div", { class: "bd" }, ...rows.flatMap(([k, v]) => v ? [
        el("span", { class: "lbl" }, k),
        track(v.earned / v.weight, (v.earned / v.weight) > 0.8 ? "ok" : "warn"),
        el("span", { class: "vl" }, v.earned + "/" + v.weight + " · " + v.detail),
      ] : [])),
      bd.familiarity_penalty ? el("p", { class: "sub", style: "margin:0 0 12px" },
        "Repeat penalty " + bd.familiarity_penalty
        + " — you had seen this lab before.") : null,
      el("h3", {}, "Checks"),
      el("ul", { class: "checklist" }, ...(a.checks || []).map((c) =>
        el("li", {}, el("span", { class: "mark " + (c.passed ? "pass" : "fail") },
          c.passed ? "✓" : "✗"),
          el("span", {}, c.desc), el("span", { class: "pts" }, c.points + " pts")))),
      a.solution ? el("div", {},
        el("h3", {}, "Your recorded solution"),
        el("div", { class: "md", html: md(a.solution) })) : null,
      el("h3", {}, "Your notes"),
      notes,
      el("button", {
        class: "btn sm", style: "margin-top:8px",
        onclick: async (e) => {
          try {
            await post("/api/notes", { lab_id: lab.id, attempt: a.n, notes: notes.value });
            e.target.textContent = "saved ✓";
            setTimeout(() => { e.target.textContent = "save notes"; }, 1500);
          } catch (err) { toast("could not save notes"); }
        },
      }, "save notes"),
    ));
}

/* ------------------------------------------------------------ progress page */

function lastNDays(n) {
  const out = [];
  const now = Date.now();
  for (let i = n - 1; i >= 0; i--) {
    out.push(new Date(now - i * 86400000).toISOString().slice(0, 10));
  }
  return out;
}

async function viewProgress() {
  const view = $("#view");
  const p = await api("/api/progress");
  const t = p.totals;

  const stat = (n, l, cls) => el("div", { class: "stat" },
    el("div", { class: "n " + (cls || "") }, n), el("div", { class: "l" }, l));

  const readyCards = Object.entries(p.readiness)
    .sort((a, b) => b[1].readiness - a[1].readiness)
    .map(([ex, r]) => el("div", { class: "ready " + ex },
      el("div", { class: "name" }, ex),
      el("div", { class: "pct " + scoreClass(r.readiness) }, r.readiness + "%"),
      el("div", { style: "color:var(--tx-3);font-size:12px" }, "readiness"),
      el("div", { style: "margin-top:10px" },
        track(r.readiness / 100, r.readiness >= 80 ? "ok" : "warn")),
      el("dl", { class: "kv", style: "margin-top:11px;font-size:12px" },
        el("dt", {}, "coverage"), el("dd", {}, r.completed + "/" + r.total + " labs"),
        el("dt", {}, "avg score"),
        el("dd", {}, r.avg_score === null ? "—" : r.avg_score + "%"),
        el("dt", {}, "cut score"), el("dd", {}, r.pass_mark + "%")),
    ));

  const barList = (obj) => el("div", { class: "barlist" },
    ...Object.entries(obj)
      .sort((a, b) => b[1].total - a[1].total)
      .map(([k, v]) => el("div", { class: "barrow" },
        el("span", { class: "nm", title: k }, k),
        track(v.completed / Math.max(1, v.total), v.pct === 100 ? "ok" : ""),
        el("span", { class: "vl" }, v.completed + "/" + v.total
          + (v.avg_score !== null ? " · avg " + v.avg_score + "%" : "")))));

  const heat = el("div", { class: "heat" }, ...lastNDays(56).map((d) => {
    const n = p.activity_days[d] || 0;
    return el("i", { class: n ? "l" + Math.min(4, n) : "", title: d + ": " + n + " attempt(s)" });
  }));

  view.replaceChildren(
    el("h1", {}, "Progress"),
    el("p", { class: "sub" },
      "Readiness blends how much of a track you have covered with how well you scored on it."),
    el("div", { class: "stat-row" },
      stat(t.pct + "%", "of all labs completed"),
      stat(t.completed, "labs completed"),
      stat(t.in_progress, "in progress"),
      stat(t.avg_score === null ? "—" : t.avg_score + "%", "average best score",
        scoreClass(t.avg_score)),
      stat(hms(t.total_time_s), "time on labs"),
      stat(t.attempts, "total attempts"),
    ),
    el("h2", {}, "Exam readiness"),
    el("div", { class: "ready-grid" }, ...readyCards),
    el("div", { class: "cols", style: "margin-top:20px" },
      el("div", {},
        el("div", { class: "panel" }, el("h2", {}, "By category"), barList(p.by_category)),
        el("div", { class: "panel" }, el("h2", {}, "Recent attempts"),
          p.timeline.length ? el("table", { class: "log" },
            el("thead", {}, el("tr", {}, ...["When", "Lab", "Result", "Time", "Score"]
              .map((h) => el("th", {}, h)))),
            el("tbody", {}, ...p.timeline.map((r) => el("tr", {},
              el("td", { style: "color:var(--tx-3);white-space:nowrap" },
                (r.at || "").replace("T", " ").slice(0, 16)),
              el("td", {}, el("a", { href: "#/lab/" + r.lab_id }, r.title),
                el("div", { style: "color:var(--tx-3);font-size:11.5px" }, r.category)),
              el("td", {}, r.outcome === "passed" ? "completed" : "unfinished"),
              el("td", {}, hms(r.duration_s)),
              el("td", {}, el("span", { class: "score-badge " + scoreClass(r.score) },
                r.score === null ? "—" : r.score + "%"))))))
            : el("p", { class: "sub" }, "Nothing yet — go and finish a lab.")),
      ),
      el("div", {},
        el("div", { class: "panel" }, el("h2", {}, "By difficulty"), barList(p.by_difficulty)),
        el("div", { class: "panel" }, el("h2", {}, "Weakest topics"),
          p.weak_concepts.length ? el("div", { class: "barlist" },
            ...p.weak_concepts.map((w) => el("div", { class: "barrow" },
              el("span", { class: "nm" }, w.concept),
              track((w.avg_score || 0) / 100, w.avg_score >= 80 ? "ok" : "warn"),
              el("span", { class: "vl" }, w.avg_score + "%"))))
            : el("p", { class: "sub" },
                "Complete a few labs and your weak spots show up here.")),
        el("div", { class: "panel" }, el("h2", {}, "Topics you have not touched"),
          p.untouched_concepts.length ? el("div", { class: "tags" },
            ...p.untouched_concepts.map((w) =>
              el("span", { class: "tag" }, w.concept + " (" + w.total + ")")))
            : el("p", { class: "sub" }, "You have touched every topic. Nice.")),
        el("div", { class: "panel" }, el("h2", {}, "Last 8 weeks"), heat),
      ),
    ),
  );
}

/* ------------------------------------------------------------ routing */

async function renderRoute() {
  const h = location.hash || "#/path";
  if (h.startsWith("#/lab/")) await viewLab(decodeURIComponent(h.slice(6)));
  else if (h.startsWith("#/progress")) await viewProgress();
  else if (h.startsWith("#/path")) viewPath();
  else viewLabs();
}

async function route() {
  try {
    if (!S.catalogue) await loadCatalogue();
    renderChrome();
    await renderRoute();
    renderChrome();
  } catch (e) {
    $("#view").replaceChildren(el("div", { class: "empty" },
      el("div", { class: "big" }, "⚠️"),
      el("div", {}, "Could not load: " + e.message),
      el("p", { class: "sub" }, "Is ./klab still running in your terminal?")));
  }
}

let lastPulse = -1;
async function pulse() {
  try {
    const p = await api("/api/pulse");
    const sessionChanged = JSON.stringify(p.session) !== JSON.stringify(S.session);
    S.session = p.session;
    S.runner = p.runner;
    if (p.v !== lastPulse) {
      lastPulse = p.v;
      await loadCatalogue();
      await renderRoute();
    } else if (sessionChanged) {
      renderSessionStrip();
    }
    renderChrome();
  } catch (_) {
    S.runner = false;
    renderChrome();
  }
}

window.addEventListener("hashchange", route);
route();
setInterval(pulse, 2000);
})();
