// The jobs page. State lives in the Zustand store (store.js); this file draws
// it and turns clicks, filters and scan events into store actions. Every
// change schedules one redraw per animation frame, and each part of the page
// redraws only when the state it shows has changed.
import {
  checksJob, isFiltered, isLocked, jobById, mySkills, postedAt, store, topJobs, visibleJobs,
} from "./store.js";

const $ = (id) => document.getElementById(id);
const act = () => store.getState();          // the store's actions

// key -> the plain-language claim the bar is measuring.
const CHECKS = [
  ["skills",    "you know the stack"],
  ["seniority", "level fits you"],
  ["location",  "you can work there"],
  ["domain",    "field you've worked in"],
  ["recency",   "experience is current"],
  ["reach",     "realistic to land"],
];

// --- application status ------------------------------------------------------
const STATUS_LABEL = {
  not_applied: "Not applied", queued: "In apply queue", applying: "AI applying",
  needs_you: "AI needs you", failed: "AI apply failed", in_process: "In process",
  referral_asked: "Asked for referral", referral_got: "Got referral",
  applied: "Applied", ai_applied: "AI apply done", contacted: "Contacted", assessment: "Assessment",
  interview: "Interview", offer: "Offer", rejected: "Rejected",
  expired: "Expired", withdrawn: "Withdrawn", over_exp: "Over exp",
};

const WORK_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };
const TYPE_LABEL = { "full-time": "Full-time", "part-time": "Part-time", contract: "Contract",
  temporary: "Temporary", internship: "Internship" };

// Dot colours on the tiles, grouped the way styles.css colours them.
const STATUS_DOTS = [
  ["var(--st-app)", ["queued", "applying", "contacted"]],
  ["var(--st-proc)", ["in_process", "needs_you"]],
  ["var(--st-ref-ask)", ["referral_asked"]],
  ["var(--st-ref-got)", ["referral_got"]],
  ["var(--st-good)", ["applied", "ai_applied", "assessment", "interview", "offer"]],
  ["#e5484d", ["failed"]],
  ["var(--st-bad)", ["rejected", "expired", "withdrawn", "over_exp"]],
];

// --- small helpers -------------------------------------------------------------

function bucket(score) {
  if (score === null || score === undefined) return "";
  return "s" + Math.min(5, Math.max(1, Math.ceil(score / 2)));
}

function logo(domain) {
  return domain ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64` : "";
}

function addInitial(el, company) {
  const s = document.createElement("span");
  s.className = "init";
  s.textContent = (company || "?").slice(0, 2).toUpperCase();
  el.appendChild(s);
}

function attachLogo(el, job, px) {
  // Google 404s with a generic globe for domains it has no icon for, so a
  // failed load falls back to the company's initials rather than a broken
  // image or a globe that says nothing.
  const src = logo(job.domain);
  if (!src) { addInitial(el, job.company); return; }
  const img = new Image();
  img.src = src;
  img.alt = "";
  if (px) { img.width = px; img.height = px; }
  img.onerror = () => { img.remove(); addInitial(el, job.company); };
  el.appendChild(img);
}

function safeParse(s) { try { return JSON.parse(s) || {}; } catch { return {}; } }

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function dayLabel(ts) {
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function postedLabel(job) {
  const t = postedAt(job);
  if (!t) return "posting date unknown";
  const days = Math.floor((Date.now() / 1000 - t) / 86400);
  const when = days < 1 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
  return `posted ${when}`;
}

// Saved as "Node.js, React"; a fresh scan event may carry a list.
function splitSkills(v) {
  if (Array.isArray(v)) return v;
  return (v || "").split(", ").filter(Boolean);
}

// --- the grid ----------------------------------------------------------------------

// job id -> its tile. A tile is kept while its job object is unchanged, so a
// reload or a score update rebuilds only the tiles whose job changed.
const tiles = new Map();

function makeTile(job) {
  const el = document.createElement("div");
  el.className = "tile " + bucket(job.score);
  el.title = `${job.title} — ${job.company}` + (job.score !== null ? ` · ${job.score}/10` : "") +
    (job.app_status ? ` · ${STATUS_LABEL[job.app_status] || job.app_status}` : "") +
    (job.role ? ` · ${job.role}` : "") + (job.experience ? ` · exp: ${job.experience}` : "") +
    (job.work_mode ? ` · ${WORK_LABEL[job.work_mode] || job.work_mode}` : "");
  if (job.app_status && job.app_status !== "not_applied") el.dataset.st = job.app_status;
  attachLogo(el, job);
  el.onclick = () => act().select(job.id);
  el.ondblclick = () => { if (job.url) window.open(job.url, "_blank", "noopener"); };
  el._job = job;
  return el;
}

function renderGrid(s) {
  const list = visibleJobs(s);
  const keep = new Set();
  const frag = document.createDocumentFragment();
  for (const j of list) {
    let el = tiles.get(j.id);
    if (!el || el._job !== j) { el = makeTile(j); tiles.set(j.id, el); }
    keep.add(j.id);
    frag.appendChild(el);
  }
  for (const id of [...tiles.keys()]) if (!keep.has(id)) tiles.delete(id);
  $("grid").replaceChildren(frag);
  return list;
}

function markSelected(s) {
  for (const [id, el] of tiles) el.classList.toggle("sel", id === s.selectedId);
}

// Fetching is the long half of a scan: light up the company being read (or
// the job being scored) so the grid shows progress instead of sitting still.
let lit = [];
function applyHighlight(s, scroll) {
  for (const el of lit) el.classList.remove("active");
  lit = [];
  const h = s.scan.highlight;
  if (h && h.id) {
    const el = tiles.get(h.id);
    if (el) lit.push(el);
  } else if (h && h.company) {
    for (const j of s.jobs) {
      if (j.company !== h.company) continue;
      const el = tiles.get(j.id);
      if (el) lit.push(el);
      if (lit.length >= 40) break;
    }
  }
  for (const el of lit) el.classList.add("active");
  if (scroll && lit[0]) lit[0].scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderGridNote(s, shown) {
  const sc = s.scan;
  let text;
  if (sc.watching && (sc.alert || sc.note)) {
    text = sc.alert || sc.note;
  } else if (!s.jobs.length) {
    text = "no jobs yet — press Scan";
  } else if (!shown) {
    text = "nothing matches — clear the filters";
  } else {
    text = (isFiltered(s.filters) ? `${shown} of ${s.jobs.length} jobs` : `${s.jobs.length} jobs`) +
      " · click a tile to select it, double-click to open the posting";
    if (sc.alert) text = `${sc.alert} · ${text}`;      // the last scan's warning stays in view
  }
  $("gridnote").textContent = text;
}

function renderLegend(s) {
  const counts = {};
  for (const j of s.jobs) if (j.app_status) counts[j.app_status] = (counts[j.app_status] || 0) + 1;
  $("stlegend").innerHTML = STATUS_DOTS.map(([color, sts]) => {
    const n = sts.reduce((a, st) => a + (counts[st] || 0), 0);
    const names = sts.map((st) => STATUS_LABEL[st] || st).join(" / ");
    return `<span class="lg${n ? "" : " zero"}"><i style="background:${color}"></i>` +
      `${escapeHtml(names)} <b>${n}</b></span>`;
  }).join("");
}

// A filter dropdown filled from the jobs themselves. A choice no job has any
// more is cleared, in the store too, so the grid never filters by a ghost.
function fillSelect(id, allLabel, names, filterKey, current) {
  const sel = $(id);
  sel.textContent = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = allLabel;
  sel.appendChild(all);
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  if (current && !names.includes(current)) act().setFilter(filterKey, "");
  sel.value = names.includes(current) ? current : "";
}

function fillCompanies(s) {
  fillSelect("co", "all companies", [...new Set(s.jobs.map((j) => j.company))].sort(), "company",
             s.filters.company);
}

// Role names come from the jobs themselves (config.yaml filters.roles plus
// "Skill match"), so the list never drifts from what is stored. Ordered by
// role priority, as in the grid.
function fillRoles(s) {
  const rank = {};
  for (const j of s.jobs) if (j.role) rank[j.role] = Math.min(rank[j.role] ?? 99, j.role_rank || 9);
  const names = Object.keys(rank).sort((a, b) => rank[a] - rank[b] || a.localeCompare(b));
  fillSelect("rolefilter", "all roles", names, "role", s.filters.role);
}

// --- side panels ----------------------------------------------------------------

function renderStats(st) {
  $("s-checked").textContent = st.checked;
  $("s-total").textContent = st.total;
  $("s-hire").textContent = st.would_hire;
  $("s-avg").textContent = Number(st.avg).toFixed(2);
  // Only /api/state sends the company counts; score updates leave them be.
  if (st.crawled !== undefined) {
    $("s-crawled").textContent = st.crawled;
    $("s-indb").textContent = st.in_db;
    $("s-companies").title = `${st.crawled} companies scanned out of ${st.in_db} in the DB. ` +
      `${st.listed} have a job board Job Radar can read; the rest are kept with their ` +
      `careers page only.`;
  }
}

// One line per crawl from the DB: how many companies it read out of all on
// file, and why the rest were not read (fresh, or no readable board).
function renderCrawls(rows) {
  const ul = $("crawls");
  if (!rows.length) { ul.innerHTML = `<li class="muted">no crawl recorded yet</li>`; return; }
  ul.innerHTML = rows.map((c) => {
    const when = new Date(c.started_at * 1000).toLocaleString([], {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    const checked = c.ok + c.failed;
    const running = c.finished_at ? "" : ` <span class="cr-live">running</span>`;
    const detail = [
      `${c.ok} ok`, c.failed ? `${c.failed} failed` : "",
      c.skipped ? `${c.skipped} skipped (read recently)` : "",
      c.in_db - c.readable > 0 ? `${c.in_db - c.readable} no readable board` : "",
      `${c.new_jobs} new jobs`,
    ].filter(Boolean).join(" · ");
    return `<li><div><b>${checked}</b> of ${c.in_db} companies checked${running}
      <span class="muted">· ${escapeHtml(c.source)} · ${when}</span></div>
      <div class="muted">${detail}</div></li>`;
  }).join("");
}

function renderTops(s) {
  const top = topJobs(s);
  const box = $("tops");
  box.textContent = "";
  if (!top.length) {
    box.innerHTML = `<div class="muted">${s.scoringOn ? "nothing scored yet" : "ranking needs a model"}</div>`;
    return;
  }
  for (const j of top) {
    const a = document.createElement("a");
    a.className = "top"; a.href = j.url || "#"; a.target = "_blank"; a.rel = "noopener";
    a.title = `${j.title} — ${j.company}` + (j.reason ? `\n${j.reason}` : "");
    const mark = document.createElement("div");
    mark.className = "toplogo";
    attachLogo(mark, j, 24);
    a.appendChild(mark);
    const pct = document.createElement("div");
    pct.className = "pct"; pct.textContent = `${j.score * 10}%`;
    const co = document.createElement("div");
    co.className = "co"; co.textContent = j.company;
    a.append(pct, co);
    box.appendChild(a);
  }
}

function renderChecks(s, job) {
  const ul = $("checks");
  ul.textContent = "";
  if (!s.scoringOn) {
    ul.innerHTML = '<li class="offnote">Scoring is off. Openings, closures and the ' +
      'weekly email still work — set <code>llm.provider</code> in config.yaml to rank them.</li>';
    return;
  }
  // Values come from the model's own per-criterion judgement. If a job has no
  // breakdown the bars read zero rather than inventing a number.
  let bd = {};
  if (job && job.breakdown) {
    bd = typeof job.breakdown === "string" ? safeParse(job.breakdown) : job.breakdown;
  }
  for (const [key, claim] of CHECKS) {
    const val = Math.max(0, Math.min(100, Number(bd[key]) || 0));
    const li = document.createElement("li");
    li.innerHTML = `<span class="k">${key}</span>
      <span class="claim">${claim}</span>
      <span class="bar"><i style="width:${val}%"></i></span>
      <span class="n">${val}</span>`;
    ul.appendChild(li);
  }
}

function renderProfile(d) {
  if (!d) return;
  if (d.name) {
    $("p-name").textContent = d.name;
    $("who").textContent = d.name.split(" ")[0];
    $("avatar").textContent = d.name.split(" ").map((w) => w[0]).slice(0, 2).join("");
  }
  $("p-headline").textContent = d.headline || "";
  $("p-location").textContent = d.location || "";
  const ul = $("exp");
  ul.textContent = "";
  if (!(d.experience || []).length) {
    ul.innerHTML = `<li class="muted">${escapeHtml(d.error || "no resume parsed")}</li>`;
    return;
  }
  for (const e of d.experience) {
    const li = document.createElement("li");
    li.innerHTML = `<div><div class="org">${escapeHtml(e.org)}</div>
                    <div class="role">${escapeHtml(e.role)}</div></div>
                    <div class="yr">${escapeHtml(e.years)}</div>`;
    ul.appendChild(li);
  }
}

// Resume skills, most done first (skill.py), then per track what the collected
// jobs ask for that the resume only lists or lacks.
function renderSkills(d) {
  if (!d) return;
  const box = $("skills");
  if (!(d.skills || []).length) {
    box.innerHTML = `<span class="muted">${escapeHtml(d.error || "no skills found on the resume")}</span>`;
    return;
  }
  box.innerHTML = d.skills.map((k) =>
    `<span class="sk ${k.tier}" title="${escapeHtml(k.category)} · work ${k.work} · projects ${k.projects}` +
    `${k.listed ? " · listed" : ""} · ${k.rating ? `your rating ${k.rating}/10` : `score ${k.score}`}">${escapeHtml(k.name)}</span>`).join("");
  $("workon").innerHTML = (d.tracks || []).map((t) => {
    const items = t.work_on.length
      ? t.work_on.map((a) => `<span class="sk ${a.yours}" title="asked by ${Math.round(a.share * 100)}% of ` +
          `these jobs; on your resume: ${a.yours}">${escapeHtml(a.name)} ` +
          `<b>${Math.round(a.share * 100)}%</b></span>`).join("")
      : `<span class="muted">${t.jobs ? "you cover what these jobs ask" : "no jobs of this kind collected yet"}</span>`;
    return `<div class="track"><div class="tr-head">${escapeHtml(t.label)}
      <span class="muted">· ${t.jobs} jobs</span></div><div class="skills">${items}</div></div>`;
  }).join("");
}

// --- the selected job ------------------------------------------------------------

function statusSelect(current, onChange) {
  const sel = document.createElement("select");
  sel.className = "stsel";
  for (const [value, label] of Object.entries(STATUS_LABEL)) {
    const o = document.createElement("option");
    o.value = value; o.textContent = label;
    sel.appendChild(o);
  }
  sel.value = current || "not_applied";
  sel.onchange = () => onChange(sel.value);
  return sel;
}

// A posting's skills, marked by whether your resume names them.
function skillRow(label, names, must, yours) {
  const row = document.createElement("div");
  row.className = "sj-skills";
  const have = names.filter((n) => yours.has(n.toLowerCase())).length;
  const head = document.createElement("div");
  head.className = "sj-meta";
  head.textContent = must && yours.size ? `${label} · ${have} of ${names.length} on your resume` : label;
  const chips = document.createElement("div");
  chips.className = "skills";
  for (const n of names) {
    const chip = document.createElement("span");
    const mine = yours.has(n.toLowerCase());
    chip.className = "sk " + (mine ? "good" : must ? "missing" : "listed");
    chip.title = mine ? "on your resume" : "not on your resume";
    chip.textContent = n;
    chips.appendChild(chip);
  }
  row.append(head, chips);
  return row;
}

function renderSelected(s, job) {
  const box = $("seljob");
  box.textContent = "";
  if (!job) {
    box.innerHTML = '<div class="muted">click a tile to see the job and set its status</div>';
    return;
  }
  const title = document.createElement("div");
  title.className = "sj-title"; title.textContent = job.title;
  const meta = document.createElement("div");
  meta.className = "sj-meta";
  meta.textContent = [job.company, job.location,
    job.score !== null && job.score !== undefined ? `${job.score}/10` : "not scored yet",
    postedLabel(job)]
    .filter(Boolean).join(" · ");
  const exp = document.createElement("div");
  exp.className = "sj-meta";
  exp.textContent = `Role: ${job.role || "—"} · Experience asked: ${job.experience || "not stated"}`;
  box.append(title, meta, exp);
  // What the posting states as data (details/): work mode, type, pay, dates.
  const facts = [WORK_LABEL[job.work_mode], TYPE_LABEL[job.employment_type], job.salary,
    job.closes_at ? `applications close ${dayLabel(job.closes_at)}` : "",
    // an evergreen posting: re-dated by edits, first put up long ago
    job.first_posted && (job.posted_at || 0) - job.first_posted > 30 * 86400
      ? `first posted ${dayLabel(job.first_posted)}` : ""].filter(Boolean);
  if (facts.length) {
    const f = document.createElement("div");
    f.className = "sj-meta sj-facts";
    f.textContent = facts.join(" · ");
    box.appendChild(f);
  }
  const yours = mySkills(s);
  const must = splitSkills(job.skills), nice = splitSkills(job.skills_nice);
  if (must.length) box.appendChild(skillRow("Must have", must, true, yours));
  if (nice.length) box.appendChild(skillRow("Nice to have", nice, false, yours));
  if (job.reason) {
    const why = document.createElement("div");
    why.className = "sj-reason"; why.textContent = job.reason;
    box.appendChild(why);
  }
  const note = s.statusNote && s.statusNote.id === job.id ? s.statusNote : null;
  const busy = !!(note && note.busy);
  const actions = document.createElement("div");
  actions.className = "sj-actions";
  const sel = statusSelect(job.app_status, (status) => saveStatus(job, status));
  sel.disabled = busy;
  actions.appendChild(sel);
  const inQueue = ["queued", "applying", "needs_you"].includes(job.app_status);
  const applied = job.app_status && !["not_applied", "failed"].includes(job.app_status) && !inQueue;
  const q = document.createElement("button");
  q.type = "button"; q.className = "sj-queue";
  q.textContent = inQueue ? "In apply queue" : "Add to apply queue";
  q.disabled = inQueue || applied || busy;
  q.title = applied ? "already applied" : "the AI engine will apply to it - see Job apply queue";
  q.onclick = () => queueJob(job);
  actions.appendChild(q);
  if (job.url) {
    const open = document.createElement("a");
    open.className = "sj-open"; open.href = job.url; open.target = "_blank"; open.rel = "noopener";
    open.textContent = "Open posting ↗";
    actions.appendChild(open);
  }
  const saved = document.createElement("span");
  saved.className = "sj-saved";
  saved.textContent = note ? note.text : "";
  actions.appendChild(saved);
  box.appendChild(actions);
}

async function saveStatus(job, status) {
  const { noteStatus, patchJob } = act();
  noteStatus(job.id, "saving…", true);
  const send = () => fetch(`/api/jobs/${encodeURIComponent(job.id)}/status`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  try {
    let r;
    try {
      r = await send();
    } catch {               // network blip: one retry before giving up
      await new Promise((ok) => setTimeout(ok, 1500));
      try { r = await send(); } catch {
        throw new Error("the Job Radar server is not running - start it with .\\start, then try again");
      }
    }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `server error ${r.status}`);
    const done = await r.json().catch(() => ({}));
    patchJob(job.id, { app_status: status });
    noteStatus(job.id, done.sheet ? `saved to history · ${done.sheet}` : "saved to history");
  } catch (e) {
    noteStatus(job.id, `not saved: ${e.message}`);     // the dropdown redraws at the saved status
  }
}

async function queueJobs(ids) {
  let r;
  try {
    r = await fetch("/api/apply/queue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids }),
    });
  } catch {
    return { added: [], refused: ids.map(() => ({ why: "the Job Radar server is not running" })) };
  }
  return r.ok ? r.json() : { added: [], refused: [{ why: `server error ${r.status}` }] };
}

async function queueJob(job) {
  const { noteStatus, patchJob } = act();
  noteStatus(job.id, "adding…", true);
  const d = await queueJobs([job.id]);
  if (d.added.length) {
    patchJob(job.id, { app_status: "queued" });
    noteStatus(job.id, "added - open Job apply queue to start");
  } else {
    noteStatus(job.id, `not queued: ${(d.refused[0] || {}).why || "refused"}`);
  }
}

// --- the scan: progress, the range lock, the timer ------------------------------------

function renderScan(s) {
  const sc = s.scan;
  const locked = isLocked(s);
  const elsewhere = s.server.scanning && !sc.watching;      // another tab, the scheduler, a reload

  const lk = sc.looking;
  $("lk-company").textContent = lk ? lk.company : "idle";
  $("lk-title").textContent = lk ? lk.title : "";
  $("lk-score").textContent = lk ? lk.score : "";

  const btn = $("scan");
  btn.disabled = locked;
  btn.textContent = sc.watching && sc.batch ? `Scanning ${sc.batch.index}/${sc.batch.count}…`
    : locked ? "Scanning…" : "Scan";

  // The time range is locked while a scan runs - in this tab, or one found
  // running after a reload (the server says which range it was started with).
  const sel = $("posted");
  if (sel.value !== String(s.filters.days)) sel.value = String(s.filters.days);
  sel.disabled = locked;
  const range = sel.options[sel.selectedIndex]?.text || "";
  const started = sc.watching ? sc.startedAt / 1000 : s.server.started;
  $("scanstatus").innerHTML = locked
    ? `Scanning jobs posted in <b>${escapeHtml(range)}</b>${started ? ` · started ${clock(started)}` : ""}` +
      " · the range unlocks when the scan is done"
    : "";

  // One click walks every company, a batch at a time. Jobs from earlier
  // batches (and earlier scans) stay on the grid; each batch adds to them.
  $("batchbar").hidden = !(sc.watching || elsewhere || sc.done || sc.batch);
  $("bb-label").textContent = sc.watching && sc.batch ? `Batch ${sc.batch.index} of ${sc.batch.count}`
    : elsewhere ? "Scan running" : sc.watching ? "Starting scan" : sc.done ? "Scan complete" : "";
  const where = $("bb-range");
  where.textContent = sc.details ? `· ${sc.details.line}`
    : sc.watching && sc.batch ? `· companies ${sc.batch.first}–${sc.batch.last}` +
      (sc.batch.total ? ` of ${sc.batch.total}` : "")
    : elsewhere ? "· new jobs appear here batch by batch" : "";
  // what reading the postings themselves found, per board
  where.title = sc.details ? sc.details.errors.map(([why, n]) => `${why} ×${n}`).join("\n") : "";
  $("bb-fill").style.width = `${sc.fill}%`;
  let kept = "";
  if (elsewhere) {
    kept = `${s.jobs.length} jobs so far`;
  } else if (sc.watching || sc.done) {
    kept = `${sc.newJobs} new jobs added this scan` +
      (sc.due !== null ? ` · ${sc.checked} of ${sc.due} companies checked` : "");
    // slow careers sites read in their own queue, so the fast ones never wait
    if (sc.lanes && sc.lanes.slow) {
      kept += ` · slow queue: ${sc.lanes.slow} sites` +
        (sc.lanes.moved ? ` (${sc.lanes.moved} timed out in the fast queue)` : "");
    }
  }
  $("bb-kept").textContent = kept;
}

let timer = null;
function renderTimer(s) {
  clearInterval(timer);
  if (s.scan.watching) {
    const t0 = s.scan.startedAt;
    timer = setInterval(() => { $("s-time").textContent = ((Date.now() - t0) / 1000).toFixed(1); }, 100);
  } else if (s.scan.elapsed !== null) {
    $("s-time").textContent = s.scan.elapsed.toFixed(1);
  }
}

// --- drawing: the state onto the page ------------------------------------------------

let drawn = { scan: {}, server: {}, filters: {} };   // the state last drawn
let shownJobs = [];
let lastChecks, lastSelected;
let queued = false;

function draw() {
  queued = false;
  const s = store.getState();
  const p = drawn;
  drawn = s;
  const gridChanged = s.jobs !== p.jobs || s.filters !== p.filters || s.searchIds !== p.searchIds;
  if (gridChanged) shownJobs = renderGrid(s);
  if (gridChanged || s.selectedId !== p.selectedId) markSelected(s);
  if (gridChanged || s.scan.highlight !== p.scan.highlight) applyHighlight(s, s.scan.highlight !== p.scan.highlight);
  if (gridChanged || s.scan !== p.scan) renderGridNote(s, shownJobs.length);
  if (s.jobs !== p.jobs) { renderLegend(s); fillCompanies(s); fillRoles(s); }
  if (s.jobs !== p.jobs || s.scoringOn !== p.scoringOn) renderTops(s);
  if (s.stats && s.stats !== p.stats) renderStats(s.stats);
  if (s.crawls !== p.crawls) renderCrawls(s.crawls);
  const cj = checksJob(s);
  if (cj !== lastChecks || s.scoringOn !== p.scoringOn) { renderChecks(s, cj); lastChecks = cj; }
  const sel = jobById(s, s.selectedId);
  if (sel !== lastSelected || s.statusNote !== p.statusNote || s.skills !== p.skills) {
    renderSelected(s, sel);
    lastSelected = sel;
  }
  if (s.profile !== p.profile) renderProfile(s.profile);
  if (s.skills !== p.skills) renderSkills(s.skills);
  if (s.scan !== p.scan || s.server !== p.server || s.filters.days !== p.filters.days ||
      (s.server.scanning && s.jobs !== p.jobs)) renderScan(s);
  if (s.scan.watching !== p.scan.watching || s.scan.elapsed !== p.scan.elapsed) renderTimer(s);
}

store.subscribe(() => {
  if (!queued) { queued = true; requestAnimationFrame(draw); }
});

// --- talking to the server -----------------------------------------------------------

let pollTimer = null;
async function loadState() {
  let d;
  try { d = await fetch("/api/state").then((r) => r.json()); } catch { return; }
  act().loaded(d);
  // A scan started elsewhere (another tab, the scheduler, before a reload):
  // no live stream here, so refresh every 15s until it finishes. Each refresh
  // adds that batch's jobs to the grid; nothing already shown is removed.
  clearTimeout(pollTimer);
  if (d.scanning && !act().scan.watching) pollTimer = setTimeout(loadState, 15000);
}

async function loadProfile() {
  try { act().setProfile(await fetch("/api/profile").then((r) => r.json())); } catch { /* panel stays as is */ }
}

async function loadSkills() {
  try { act().setSkills(await fetch("/api/skills").then((r) => r.json())); } catch { /* panel stays as is */ }
}

// Ids matching the text query, resolved server-side so descriptions are
// searchable without shipping megabytes of them to the browser.
async function runSearch() {
  const raw = ($("q").value || "").trim();
  if (!raw) { act().searched("", null); return; }
  let ids = null;
  try {
    const d = await fetch(`/api/search?q=${encodeURIComponent(raw)}`).then((r) => r.json());
    ids = d.ids === null ? null : new Set(d.ids);
  } catch { /* a failed lookup shows everything, not nothing */ }
  if (($("q").value || "").trim() === raw) act().searched(raw.toLowerCase(), ids);   // not a stale answer
}

function startScan() {
  act().scanStarted();
  // smart: every careers page, every time - companies with India tech jobs
  // first; the "posted" range: only jobs posted in it are scored
  const es = new EventSource(`/api/scan?limit=3000&smart=true&days=${act().filters.days}`);
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    act().scanEvent(d);
    if (d.type === "fetched") {
      loadState();                       // that batch's jobs onto the grid
    } else if (d.type === "done") {
      es.close();
      // reload first so the finale ranks the full board, not just this run's batch
      loadState().then(showMatchMade);
    }
  };
  es.onerror = () => {
    es.close();
    act().scanLost();
    loadState();
  };
}

function showMatchMade() {
  const s = store.getState();
  const top = topJobs(s);
  if (!top.length) return;
  const name = (s.profile && s.profile.name) || $("p-name").textContent;
  const initials = $("avatar").textContent;
  const cards = top.map((j) => `
    <a class="mm-card" href="${escapeHtml(j.url || "#")}" target="_blank" rel="noopener"
       title="${escapeHtml(j.title)} — ${escapeHtml(j.reason || "")}">
      <div class="mm-tile" data-domain="${escapeHtml(j.domain || "")}"
           data-company="${escapeHtml(j.company || "?")}"></div>
      <div class="mm-pct">${j.score * 10}%</div>
      <div class="mm-co">${escapeHtml(j.company)}</div>
    </a>`).join("");

  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `
    <div class="mm">
      <div class="mm-profile">
        <div class="mm-av">${escapeHtml(initials)}</div>
        <div><div class="kicker">Profile</div>
             <div class="pname">${escapeHtml(name)}</div>
             <div class="pmeta">${escapeHtml((s.profile && s.profile.headline) || "")}</div></div>
      </div>
      <div class="kicker mm-kicker">Match made</div>
      <h2 class="mm-head">High probability of interviewing you</h2>
      <div class="mm-cards">${cards}</div>
      <button class="mm-close">Back to the grid</button>
    </div>`;
  document.body.appendChild(ov);
  for (const slot of ov.querySelectorAll(".mm-tile")) {
    attachLogo(slot, { domain: slot.dataset.domain, company: slot.dataset.company }, 56);
  }
  requestAnimationFrame(() => ov.classList.add("show"));
  const close = () => ov.remove();
  ov.querySelector(".mm-close").onclick = close;
  ov.onclick = (e) => { if (e.target === ov) close(); };
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });
}

// Job apply queue opens as its own window on the right half of the screen; the
// engine's Chrome window goes on the left half, so you can watch it and step in.
function openApplyView(e) {
  e.preventDefault();
  const sc = window.screen;
  const half = Math.floor(sc.availWidth / 2);
  const left = (sc.availLeft || 0) + half, top = sc.availTop || 0;
  const width = sc.availWidth - half, height = sc.availHeight;
  const w = window.open("/apply", "jobradar-apply",
                        `popup=yes,left=${left},top=${top},width=${width},height=${height}`);
  if (!w) { location.href = "/apply"; return; }      // pop-ups blocked: same tab
  try { w.moveTo(left, top); w.resizeTo(width, height); } catch { /* not ours to move */ }
  w.focus();
  fetch("/api/apply/arrange", { method: "POST" }).catch(() => {});
}

// --- controls -> store ---------------------------------------------------------------

// Every status is filterable, in pipeline order (STATUS_LABEL follows it).
for (const [st, label] of Object.entries(STATUS_LABEL)) {
  if (st === "not_applied") continue;               // that is "not applied yet"
  const o = document.createElement("option");
  o.value = st; o.textContent = label;
  $("stfilter").appendChild(o);
}

const FILTER_CONTROLS = [
  ["minscore", "min", (el) => Number(el.value || 0)],
  ["co", "company", (el) => el.value || ""],
  ["newonly", "newOnly", (el) => el.checked],
  ["stfilter", "status", (el) => el.value || ""],
  ["rolefilter", "role", (el) => el.value || ""],
];
for (const [id, key, read] of FILTER_CONTROLS) {
  $(id).addEventListener("change", () => act().setFilter(key, read($(id))));
  act().setFilter(key, read($(id)));                // a value the browser restored on reload
}

// Your last range comes back after a reload (only in this browser).
if (![...$("posted").options].some((o) => o.value === String(act().filters.days))) act().setFilter("days", 0);
$("posted").addEventListener("change", () => {
  try { localStorage.setItem("jr-posted", $("posted").value); } catch { /* not kept */ }
  act().setFilter("days", Number($("posted").value || 0));
});

let debounce = null;
$("q").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(runSearch, 220);
});
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { $("q").value = ""; act().searched("", null); }
});

$("applylink").onclick = openApplyView;
$("scan").onclick = startScan;

draw();
loadProfile();
loadSkills();
loadState();
