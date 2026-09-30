const $ = (id) => document.getElementById(id);
const tiles = new Map();          // job id -> tile element
let jobs = [];
let scoringOn = true;
let t0 = 0, timer = null;

// key -> the plain-language claim the bar is measuring.
const CHECKS = [
  ["skills",    "you know the stack"],
  ["seniority", "level fits you"],
  ["location",  "you can work there"],
  ["domain",    "field you've worked in"],
  ["recency",   "experience is current"],
  ["reach",     "realistic to land"],
];

function bucket(score) {
  if (score === null || score === undefined) return "";
  return "s" + Math.min(5, Math.max(1, Math.ceil(score / 2)));
}

function logo(domain) {
  return domain ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64` : "";
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

function makeTile(job) {
  const el = document.createElement("div");
  el.className = "tile " + bucket(job.score) + (selectedId === job.id ? " sel" : "");
  el.title = `${job.title} — ${job.company}` + (job.score !== null ? ` · ${job.score}/10` : "") +
    (job.app_status ? ` · ${STATUS_LABEL[job.app_status] || job.app_status}` : "") +
    (job.role ? ` · ${job.role}` : "") + (job.experience ? ` · exp: ${job.experience}` : "");
  if (job.app_status && job.app_status !== "not_applied") el.dataset.st = job.app_status;
  attachLogo(el, job);
  el.onclick = () => selectJob(job);
  el.ondblclick = () => { if (job.url) window.open(job.url, "_blank", "noopener"); };
  return el;
}

// --- application status ------------------------------------------------------
const STATUS_LABEL = {
  not_applied: "Not applied", queued: "In apply queue", applying: "AI applying",
  needs_you: "AI needs you", failed: "AI apply failed", in_process: "In process", referral: "Referral",
  applied: "Applied", ai_applied: "AI apply done", contacted: "Contacted", assessment: "Assessment",
  interview: "Interview", offer: "Offer", rejected: "Rejected",
  expired: "Expired", withdrawn: "Withdrawn",
};
let selectedId = null;

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

function selectJob(job) {
  if (selectedId) tiles.get(selectedId)?.classList.remove("sel");
  selectedId = job.id;
  tiles.get(job.id)?.classList.add("sel");
  renderChecks(job);

  const box = $("seljob");
  box.textContent = "";
  const title = document.createElement("div");
  title.className = "sj-title"; title.textContent = job.title;
  const meta = document.createElement("div");
  meta.className = "sj-meta";
  meta.textContent = [job.company, job.location,
    job.score !== null && job.score !== undefined ? `${job.score}/10` : "not scored yet"]
    .filter(Boolean).join(" · ");
  const exp = document.createElement("div");
  exp.className = "sj-meta";
  exp.textContent = `Role: ${job.role || "—"} · Experience asked: ${job.experience || "not stated"}`;
  box.append(title, meta, exp);
  if (job.reason) {
    const why = document.createElement("div");
    why.className = "sj-reason"; why.textContent = job.reason;
    box.appendChild(why);
  }
  const actions = document.createElement("div");
  actions.className = "sj-actions";
  const saved = document.createElement("span");
  saved.className = "sj-saved";
  const sel = statusSelect(job.app_status, async (status) => {
    saved.textContent = "saving…";
    try {
      const r = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      job.app_status = status;
      saved.textContent = "saved to history";
      renderGrid();
    } catch (e) {
      saved.textContent = `not saved: ${e.message}`;
      sel.value = job.app_status || "not_applied";
    }
  });
  actions.appendChild(sel);
  const inQueue = ["queued", "applying", "needs_you"].includes(job.app_status);
  const applied = job.app_status && !["not_applied", "failed"].includes(job.app_status) && !inQueue;
  const q = document.createElement("button");
  q.type = "button"; q.className = "sj-queue";
  q.textContent = inQueue ? "In apply queue" : "Add to apply queue";
  q.disabled = inQueue || applied;
  q.title = applied ? "already applied" : "the AI engine will apply to it - see Apply status";
  q.onclick = async () => {
    q.disabled = true;
    const d = await queueJobs([job.id]);
    if (d.added.length) {
      job.app_status = "queued"; sel.value = "queued";
      q.textContent = "In apply queue";
      saved.textContent = "added - open Apply status to start";
      renderGrid();
    } else {
      q.disabled = false;
      saved.textContent = `not queued: ${(d.refused[0] || {}).why || "refused"}`;
    }
  };
  actions.appendChild(q);
  if (job.url) {
    const open = document.createElement("a");
    open.className = "sj-open"; open.href = job.url; open.target = "_blank"; open.rel = "noopener";
    open.textContent = "Open posting ↗";
    actions.appendChild(open);
  }
  actions.appendChild(saved);
  box.appendChild(actions);
}

async function queueJobs(ids) {
  const r = await fetch("/api/apply/queue", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids: ids }),
  });
  return r.ok ? r.json() : { added: [], refused: [{ why: `error ${r.status}` }] };
}

// Every job the filters show that has no application yet goes to the queue.
async function queueShown() {
  const f = currentFilter();
  const ids = jobs.filter((j) => matches(j, f) && statusMatches(j, "none")).map((j) => j.id);
  if (!ids.length) { alert("No unapplied jobs match the filters."); return; }
  if (!confirm(`Add ${ids.length} job(s) to the AI apply queue?\n` +
               "Jobs below your minimum match score are skipped.")) return;
  const d = await queueJobs(ids);
  const why = {};                 // "match 3/10 is below 5" x 12, not twelve lines
  for (const x of d.refused) {
    const k = x.why.replace(/[\d.]+\/10/, "N/10");
    why[k] = (why[k] || 0) + 1;
  }
  alert(`${d.added.length} added to the apply queue.` +
        (d.refused.length ? `\n${d.refused.length} not added:\n` +
          Object.entries(why).map(([w, n]) => `  ${n} × ${w}`).join("\n") : ""));
  await loadState();
}

function addInitial(el, company) {
  const s = document.createElement("span");
  s.className = "init";
  s.textContent = (company || "?").slice(0, 2).toUpperCase();
  el.appendChild(s);
}

// --- filtering -------------------------------------------------------------
const WEEK = 7 * 24 * 3600;

function currentFilter() {
  return {
    q: ($("q").value || "").trim().toLowerCase(),
    min: Number($("minscore").value || 0),
    company: $("co").value || "",
    newOnly: $("newonly").checked,
    status: $("stfilter").value || "",
    role: $("rolefilter").value || "",
  };
}

function statusMatches(job, want) {
  const st = job.app_status && job.app_status !== "not_applied" ? job.app_status : "";
  if (!want) return true;
  if (want === "none") return !st;
  if (want === "any") return !!st;
  return st === want;
}

// Ids matching the current text query, resolved server-side so descriptions
// are searchable without shipping megabytes of them to the browser.
// null means "no query running", not "nothing matched".
let searchIds = null;

function matches(job, f) {
  if (f.company && job.company !== f.company) return false;
  if (f.min && (job.score === null || job.score === undefined || job.score < f.min)) return false;
  if (f.newOnly && (!job.first_seen || Date.now() / 1000 - job.first_seen > WEEK)) return false;
  if (f.q && searchIds && !searchIds.has(job.id)) return false;
  if (!statusMatches(job, f.status)) return false;
  if (f.role && job.role !== f.role) return false;
  return true;
}

async function runSearch() {
  const q = ($("q").value || "").trim();
  if (!q) { searchIds = null; renderGrid(); return; }
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const d = await r.json();
    searchIds = d.ids === null ? null : new Set(d.ids);
  } catch {
    searchIds = null;        // a failed lookup shows everything, not nothing
  }
  renderGrid();
}

function renderGrid() {
  const grid = $("grid");
  const f = currentFilter();
  grid.textContent = "";
  tiles.clear();
  let shown = 0;
  for (const j of jobs) {
    if (!matches(j, f)) continue;
    const el = makeTile(j);
    tiles.set(j.id, el);
    grid.appendChild(el);
    shown++;
  }
  const filtered = f.q || f.min || f.company || f.newOnly || f.status || f.role;
  if (!jobs.length) {
    $("gridnote").textContent = "no jobs yet — press Scan";
  } else if (!shown) {
    $("gridnote").textContent = "nothing matches — clear the filters";
  } else if (f.q && !searchIds) {
    $("gridnote").textContent = `${shown} jobs · searching…`;
  } else {
    $("gridnote").textContent =
      (filtered ? `${shown} of ${jobs.length} jobs` : `${jobs.length} jobs`) +
      " · click a tile to select it, double-click to open the posting";
  }
}

function fillCompanies() {
  const sel = $("co");
  const chosen = sel.value;
  const names = [...new Set(jobs.map((j) => j.company))].sort();
  sel.textContent = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = "all companies";
  sel.appendChild(all);
  for (const n of names) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }
  sel.value = chosen;
}

let debounce = null;
function onFilterChange() {
  clearTimeout(debounce);
  debounce = setTimeout(runSearch, 220);
}

function renderStats(s) {
  $("s-checked").textContent = s.checked;
  $("s-total").textContent = s.total;
  $("s-hire").textContent = s.would_hire;
  $("s-avg").textContent = Number(s.avg).toFixed(2);
  // Only /api/state sends the company counts; scoring updates leave them be.
  if (s.crawled !== undefined) {
    $("s-crawled").textContent = s.crawled;
    $("s-listed").textContent = s.listed;
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

// Lists sort by score plus the role's bonus (frontend -1), then role priority.
function rankCmp(a, b) {
  return (b.score + (b.role_bonus || 0)) - (a.score + (a.role_bonus || 0)) ||
         (a.role_rank || 9) - (b.role_rank || 9);
}

function renderTops() {
  const scored = jobs.filter((j) => j.score !== null && j.score !== undefined);
  scored.sort(rankCmp);
  const top = scored.slice(0, 5);
  const box = $("tops");
  box.textContent = "";
  if (!top.length) {
    box.innerHTML = `<div class="muted">${
      scoringOn ? "nothing scored yet" : "ranking needs a model"}</div>`;
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

function renderChecks(job) {
  const ul = $("checks");
  ul.textContent = "";
  if (!scoringOn) {
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

function safeParse(s) { try { return JSON.parse(s) || {}; } catch { return {}; } }

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let watching = false;      // true while this tab has the live scan stream open
let pollTimer = null;

async function loadState() {
  const r = await fetch("/api/state");
  const d = await r.json();
  jobs = d.jobs;
  scoringOn = d.scoring !== false;
  fillCompanies();
  renderGrid(); renderStats(d.stats); renderTops(); renderCrawls(d.crawls || []);
  if (!selectedId) renderChecks(jobs.find((j) => j.score !== null) || null);
  // A scan started elsewhere (another tab, the scheduler, before a reload):
  // no live stream here, so refresh every 15s until it finishes. Each refresh
  // adds that batch's jobs to the grid; nothing already shown is removed.
  clearTimeout(pollTimer);
  if (d.scanning && !watching) {
    $("batchbar").hidden = false;
    $("bb-label").textContent = "Scan running";
    $("bb-range").textContent = "· new jobs appear here batch by batch";
    $("bb-kept").textContent = `${jobs.length} jobs so far`;
    $("scan").disabled = true; $("scan").textContent = "Scanning…";
    pollTimer = setTimeout(loadState, 15000);
  } else if (!watching && $("scan").disabled) {
    $("scan").disabled = false; $("scan").textContent = "Scan";
    $("bb-label").textContent = "Scan complete";
    $("bb-range").textContent = "";
  }
}

async function loadProfile() {
  const d = await fetch("/api/profile").then((r) => r.json());
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
async function loadSkills() {
  const d = await fetch("/api/skills").then((r) => r.json());
  const box = $("skills");
  if (!(d.skills || []).length) {
    box.innerHTML = `<span class="muted">${escapeHtml(d.error || "no skills found on the resume")}</span>`;
    return;
  }
  box.innerHTML = d.skills.map((s) =>
    `<span class="sk ${s.tier}" title="${escapeHtml(s.category)} · work ${s.work} · projects ${s.projects}` +
    `${s.listed ? " · listed" : ""} · ${s.rating ? `your rating ${s.rating}/10` : `score ${s.score}`}">${escapeHtml(s.name)}</span>`).join("");
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

let lit = [];
let batchNew = 0;         // new jobs added across the batches of the current scan
function clearLit() {
  for (const el of lit) el.classList.remove("active");
  lit = [];
}

function highlightCompany(company) {
  clearLit();
  let first = null;
  for (const j of jobs) {
    if (j.company !== company) continue;
    const el = tiles.get(j.id);
    if (!el) continue;
    el.classList.add("active");
    lit.push(el);
    if (!first) first = el;
    if (lit.length >= 40) break;
  }
  if (first) first.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function startScan() {
  const btn = $("scan");
  btn.disabled = true; btn.textContent = "Scanning…";
  t0 = Date.now();
  timer = setInterval(() => {
    $("s-time").textContent = ((Date.now() - t0) / 1000).toFixed(1);
  }, 100);

  watching = true;
  const es = new EventSource("/api/scan?limit=3000");
  batchNew = 0;
  $("bb-kept").textContent = "";
  let active = null;

  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "fetch") {
      $("lk-company").textContent = d.company;
      $("lk-title").textContent = d.error ? "fetch failed" : `${d.count} open`;
      $("lk-score").textContent = "";
      // Fetching is the long half of a scan. Light up that company's tiles so
      // the grid shows progress instead of sitting still for two minutes.
      highlightCompany(d.company);
    } else if (d.type === "batch") {
      // One click walks every company, a batch at a time. Jobs from earlier
      // batches (and earlier scans) stay on the grid; each batch adds to them.
      const first = d.first ?? (d.index - 1) * 50 + 1;
      const last = d.last ?? first + d.companies - 1;
      $("batchbar").hidden = false;
      $("bb-label").textContent = `Batch ${d.index} of ${d.count}`;
      $("bb-range").textContent = `· companies ${first}–${last}` + (d.total ? ` of ${d.total}` : "");
      $("bb-fill").style.width = `${Math.round(((d.index - 1) / d.count) * 100)}%`;
      $("lk-company").textContent = `batch ${d.index} of ${d.count}`;
      $("lk-title").textContent = `companies ${first}–${last}`;
      $("scan").textContent = `Scanning ${d.index}/${d.count}…`;
    } else if (d.type === "fetched") {
      loadState();
      const where = d.batches > 1 ? `batch ${d.batch}/${d.batches}: ` : "";
      $("gridnote").textContent = `${where}${d.new} new · ${d.closed} closed · ${d.total} kept`;
      if (d.batches) {
        $("bb-fill").style.width = `${Math.round((d.batch / d.batches) * 100)}%`;
      }
      batchNew += d.new || 0;
      $("bb-kept").textContent = `${batchNew} new jobs added this scan` +
        (d.due !== undefined ? ` · ${d.checked} of ${d.due} companies checked` : "");
    } else if (d.type === "scoring") {
      const where = d.batches > 1 ? `batch ${d.batch}/${d.batches}: ` : "";
      $("gridnote").textContent = `${where}scoring ${d.count} of ${d.backlog} unscored`;
    } else if (d.type === "looking") {
      clearLit();
      active = tiles.get(d.id) || null;
      if (active) {
        active.classList.add("active");
        lit.push(active);
        active.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      $("lk-company").textContent = d.company;
      $("lk-title").textContent = d.title;
      $("lk-score").textContent = "…";
    } else if (d.type === "scored") {
      const el = tiles.get(d.id);
      if (el) {
        el.className = "tile " + bucket(d.score);
        el.title = `${d.title} — ${d.company} · ${d.score}/10 — ${d.reason || ""}`;
      }
      const j = jobs.find((x) => x.id === d.id);
      if (j) { j.score = d.score; j.reason = d.reason; j.breakdown = d.breakdown || {}; }
      $("lk-score").textContent = (d.score / 10).toFixed(2);
      renderChecks(j || null);
      renderTops();
      const scored = jobs.filter((x) => x.score !== null && x.score !== undefined);
      renderStats({
        checked: scored.length, total: jobs.length,
        would_hire: scored.filter((x) => x.score >= 6).length,
        avg: scored.length ? scored.reduce((a, b) => a + b.score, 0) / scored.length / 10 : 0,
      });
    } else if (d.type === "throttled") {
      $("gridnote").textContent = `rate limited after ${d.scored} — press Scan again to continue`;
    } else if (d.type === "error") {
      $("gridnote").textContent = d.message;
    } else if (d.type === "done") {
      // Score state only lands in `jobs` via the stream; reload first so the
      // finale ranks the full board, not just this run's batch.
      loadState().then(showMatchMade);
      $("s-time").textContent = d.elapsed.toFixed(1);
      $("lk-company").textContent = "idle"; $("lk-title").textContent = ""; $("lk-score").textContent = "";
      clearLit();
      es.close(); clearInterval(timer); watching = false;
      btn.disabled = false; btn.textContent = "Scan";
      $("bb-label").textContent = "Scan complete";
      $("bb-fill").style.width = "100%";
      loadState();
    }
  };
  es.onerror = () => {
    clearLit();
    es.close(); clearInterval(timer); watching = false;
    btn.disabled = false; btn.textContent = "Scan";
    loadState();
  };
}

function showMatchMade() {
  const scored = jobs.filter((j) => j.score !== null && j.score !== undefined);
  if (!scored.length) return;
  scored.sort(rankCmp);
  const top = scored.slice(0, 5);

  const name = $("p-name").textContent;
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
             <div class="pmeta">${escapeHtml($("p-headline").textContent)}</div></div>
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

$("scan").onclick = startScan;
$("queueshown").onclick = queueShown;
$("q").addEventListener("input", onFilterChange);
$("minscore").addEventListener("change", renderGrid);
$("co").addEventListener("change", renderGrid);
$("newonly").addEventListener("change", renderGrid);
$("stfilter").addEventListener("change", renderGrid);
$("rolefilter").addEventListener("change", renderGrid);
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { $("q").value = ""; searchIds = null; renderGrid(); }
});
loadProfile();
loadSkills();
loadState();
