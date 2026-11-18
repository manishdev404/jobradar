// The page's state in one Zustand store (the vanilla build - no React).
//
// Three kinds of state live here: what the server sent (jobs, stats, crawls,
// whether a scan is running), what you picked (filters, the selected job), and
// this tab's live scan. app.js changes state only through the actions below
// and redraws from it; nothing else holds a copy.
import { createStore } from "./vendor/zustand-vanilla.js";

function savedDays() {
  try { return Number(localStorage.getItem("jr-posted") || 0); } catch { return 0; }
}

// This tab's live scan, before it starts.
const SCAN_IDLE = {
  watching: false,   // this tab has the scan's event stream open
  startedAt: 0,      // ms, in this tab
  batch: null,       // {index, count, first, last, total}
  fill: 0,           // progress bar, 0-100
  newJobs: 0,
  checked: null, due: null,
  lanes: null,       // {slow, moved}: the fast/slow queues
  details: null,     // {line, errors}: what reading the postings found
  note: "",          // the scan's latest word for the grid note
  alert: "",         // rate limited / error: stays until the next scan
  looking: null,     // {company, title, score}: the "looking at" panel
  highlight: null,   // {company} or {id}: tiles lit on the grid
  scoredId: null,    // the job just scored: its checks are shown
  elapsed: null,     // seconds, once done
  done: false,
};

// A reload hands back every job as a new object. Keep the old object for a
// job that did not change, so its tile (and its logo) is not rebuilt.
function share(prev, next) {
  const old = new Map(prev.map((j) => [j.id, j]));
  return next.map((j) => {
    const o = old.get(j.id);
    return o && JSON.stringify(o) === JSON.stringify(j) ? o : j;
  });
}

function scoreStats(jobs) {
  const scored = jobs.filter((j) => j.score !== null && j.score !== undefined);
  return {
    checked: scored.length, total: jobs.length,
    would_hire: scored.filter((j) => j.score >= 6).length,
    avg: scored.length ? scored.reduce((a, j) => a + j.score, 0) / scored.length / 10 : 0,
  };
}

// One scan event -> the state it changes.
function onScanEvent(s, d) {
  const scan = { ...s.scan };
  const where = d.batches > 1 ? `batch ${d.batch}/${d.batches}: ` : "";
  switch (d.type) {
    case "fetch":
      scan.looking = { company: d.company, title: d.error ? "fetch failed" : `${d.count} open`, score: "" };
      scan.highlight = { company: d.company };
      break;
    case "batch": {
      const first = d.first ?? (d.index - 1) * 50 + 1;
      const last = d.last ?? first + d.companies - 1;
      scan.batch = { index: d.index, count: d.count, first, last, total: d.total };
      scan.fill = Math.round(((d.index - 1) / d.count) * 100);
      scan.looking = { company: `batch ${d.index} of ${d.count}`, title: `companies ${first}–${last}`, score: "" };
      break;
    }
    case "fetched":
      scan.note = `${where}${d.new} new · ${d.closed} closed · ${d.total} kept`;
      if (d.batches) scan.fill = Math.round((d.batch / d.batches) * 100);
      scan.newJobs += d.new || 0;
      if (d.due !== undefined) { scan.checked = d.checked; scan.due = d.due; }
      break;
    case "scoring":
      scan.note = `${where}scoring ${d.count} of ${d.backlog} unscored`;
      break;
    case "looking":
      scan.highlight = { id: d.id };
      scan.looking = { company: d.company, title: d.title, score: "…" };
      break;
    case "scored": {
      scan.looking = { ...(scan.looking || {}), score: (d.score / 10).toFixed(2) };
      scan.scoredId = d.id;
      const jobs = s.jobs.map((j) => (j.id === d.id
        ? { ...j, score: d.score, reason: d.reason, breakdown: d.breakdown || {} } : j));
      return { scan, jobs, stats: { ...s.stats, ...scoreStats(jobs) } };
    }
    case "lanes":
      scan.lanes = { slow: d.slow, moved: d.moved };
      break;
    case "details":
      scan.details = { line: d.line, errors: d.errors || [] };
      break;
    case "throttled":
      scan.alert = `rate limited after ${d.scored} — press Scan again to continue`;
      break;
    case "error":
      scan.alert = d.message;
      break;
    case "done":
      Object.assign(scan, { watching: false, done: true, elapsed: d.elapsed, fill: 100, note: "",
                            looking: null, highlight: null, scoredId: null });
      break;
    default:
      return {};
  }
  return { scan };
}

export const store = createStore((set) => ({
  // --- from the server ---
  jobs: [],
  stats: null,
  crawls: [],
  scoringOn: true,
  server: { scanning: false, days: 0, started: 0 },   // a scan the server says is running
  profile: null,
  skills: null,

  // --- what you picked ---
  filters: { q: "", days: savedDays(), min: 0, company: "", newOnly: false, status: "", role: "" },
  searchIds: null,      // ids matching the text search; null = no search running
  selectedId: null,
  statusNote: null,     // {id, text}: what saving a status or queueing said

  // --- this tab's live scan ---
  scan: SCAN_IDLE,

  // --- actions ---
  loaded: (d) => set((s) => {
    const server = { scanning: !!d.scanning, days: d.scan_days || 0, started: d.scan_started || 0 };
    // a scan running elsewhere finished since the last look
    const finished = s.server.scanning && !server.scanning && !s.scan.watching;
    return {
      jobs: share(s.jobs, d.jobs || []),
      stats: d.stats,
      crawls: d.crawls || [],
      scoringOn: d.scoring !== false,
      server,
      // a scan started elsewhere locks the range to the one it was started with
      filters: server.scanning && !s.scan.watching ? { ...s.filters, days: server.days } : s.filters,
      scan: finished ? { ...s.scan, done: true, fill: 100 } : s.scan,
    };
  }),
  setProfile: (profile) => set({ profile }),
  setSkills: (skills) => set({ skills }),
  setFilter: (key, value) => set((s) => (s.filters[key] === value ? {} : { filters: { ...s.filters, [key]: value } })),
  // the text and its matches land together, so the grid never filters by a
  // new query with the last query's results
  searched: (q, searchIds) => set((s) => ({ filters: { ...s.filters, q }, searchIds })),
  select: (selectedId) => set({ selectedId }),
  patchJob: (id, patch) => set((s) => ({ jobs: s.jobs.map((j) => (j.id === id ? { ...j, ...patch } : j)) })),
  noteStatus: (id, text, busy = false) => set({ statusNote: { id, text, busy } }),
  scanStarted: () => set({ scan: { ...SCAN_IDLE, watching: true, startedAt: Date.now() } }),
  scanEvent: (d) => set((s) => onScanEvent(s, d)),
  scanLost: () => set((s) => ({
    scan: { ...s.scan, watching: false, looking: null, highlight: null, scoredId: null },
  })),
}));

// --- derived state: pure functions of the state ------------------------------

const WEEK = 7 * 24 * 3600;
const now = () => Date.now() / 1000;

// When the job went up, by the job site. No date known: 0, so a time range
// never lets it in on "first seen" - a 9-month-old repost is not new.
export const postedAt = (job) => job.posted_at || 0;

export const isLocked = (s) => s.scan.watching || s.server.scanning;

export function statusMatches(job, want) {
  const st = job.app_status && job.app_status !== "not_applied" ? job.app_status : "";
  if (!want) return true;
  if (want === "none") return !st;
  if (want === "any") return !!st;
  return st === want;
}

function matches(job, f, searchIds) {
  // posting age first: everything else only looks at jobs inside the range
  if (f.days && now() - postedAt(job) > f.days * 86400) return false;
  if (f.company && job.company !== f.company) return false;
  if (f.min && (job.score === null || job.score === undefined || job.score < f.min)) return false;
  if (f.newOnly && (!job.first_seen || now() - job.first_seen > WEEK)) return false;
  if (f.q && searchIds && !searchIds.has(job.id)) return false;
  if (!statusMatches(job, f.status)) return false;
  if (f.role && job.role !== f.role) return false;
  return true;
}

// Grid order: jobs still to act on first; then, toward the bottom, in this
// order: asked for referral, in process (with the stages after it), got
// referral, applied, AI applied, expired, withdrawn, rejected, over experience. The server's
// order (best first, mixed across companies) is kept inside each group.
const GRID_GROUP = {
  referral_asked: 1,
  in_process: 2, contacted: 2, assessment: 2, interview: 2, offer: 2,
  referral_got: 3, applied: 4, ai_applied: 5, expired: 6, withdrawn: 7, rejected: 8,
  over_exp: 9,                          // asks for more experience: last of all
};
const gridGroup = (job) => GRID_GROUP[job.app_status] || 0;    // not applied / AI queue: top

// The jobs the grid shows, in order.
export function visibleJobs(s) {
  const ordered = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((g) => s.jobs.filter((j) => gridGroup(j) === g));
  return ordered.filter((j) => matches(j, s.filters, s.searchIds));
}

// Any filter narrowing the grid - the posting-date range included.
export const isFiltered = (f) => !!(f.q || f.days || f.min || f.company || f.newOnly || f.status || f.role);

export const jobById = (s, id) => (id ? s.jobs.find((j) => j.id === id) || null : null);

// Whose checks the panel shows: the job just scored while a scan runs, else
// the selected one, else the first scored.
export function checksJob(s) {
  return jobById(s, s.scan.watching ? s.scan.scoredId : null) || jobById(s, s.selectedId)
    || s.jobs.find((j) => j.score !== null && j.score !== undefined) || null;
}

// Lists sort by score plus the role's bonus (frontend -1), then role priority.
export function rankCmp(a, b) {
  return (b.score + (b.role_bonus || 0)) - (a.score + (a.role_bonus || 0)) ||
         (a.role_rank || 9) - (b.role_rank || 9);
}

export function topJobs(s, n = 5) {
  return s.jobs.filter((j) => j.score !== null && j.score !== undefined).sort(rankCmp).slice(0, n);
}

// Your resume's skills, lower-cased, to mark a job's skills you have.
let skillsFor = null, skillSetCache = new Set();
export function mySkills(s) {
  if (s.skills !== skillsFor) {
    skillsFor = s.skills;
    skillSetCache = new Set(((s.skills && s.skills.skills) || []).map((k) => k.name.toLowerCase()));
  }
  return skillSetCache;
}
