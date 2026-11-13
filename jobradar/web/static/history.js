const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  not_applied: "Not applied", queued: "In apply queue", applying: "AI applying",
  needs_you: "AI needs you", failed: "AI apply failed", in_process: "In process",
  referral_asked: "Asked for referral", referral_got: "Got referral",
  applied: "Applied", ai_applied: "AI apply done", contacted: "Contacted", assessment: "Assessment",
  interview: "Interview", offer: "Offer", rejected: "Rejected",
  expired: "Expired", withdrawn: "Withdrawn", over_exp: "Over exp",
};
// Counted as "applied" in the header: anything past the decision to apply.
const APPLIED = new Set(["referral_got", "applied", "ai_applied", "contacted", "assessment", "interview",
                         "offer", "rejected", "expired", "withdrawn"]);

let apps = [];
let filter = "";            // status pill; "" = all

function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString(undefined,
    { year: "numeric", month: "short", day: "numeric" });
}

function statusSelect(current, onChange) {
  const sel = document.createElement("select");
  sel.className = "stsel";
  for (const [value, label] of Object.entries(STATUS_LABEL)) {
    const o = document.createElement("option");
    o.value = value; o.textContent = label;
    sel.appendChild(o);
  }
  sel.value = current;
  sel.onchange = () => onChange(sel.value, sel);
  return sel;
}

async function api(path, method, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

async function load() {
  const d = await fetch("/api/applications").then((r) => r.json());
  apps = d.applications;
  render();
}

function matches(a, q) {
  if (filter && a.status !== filter) return false;
  if (!q) return true;
  return [a.company, a.title, a.skills, a.notes, a.location, a.status]
    .some((v) => (v || "").toLowerCase().includes(q));
}

function renderSummary() {
  const counts = {};
  for (const a of apps) counts[a.status] = (counts[a.status] || 0) + 1;
  $("h-total").textContent = apps.length;
  $("h-applied").textContent = apps.filter((a) => APPLIED.has(a.status)).length;
  $("h-process").textContent = counts.in_process || 0;
  $("h-int").textContent = (counts.interview || 0) + (counts.offer || 0);

  const box = $("hsum");
  box.textContent = "";
  const pill = (value, label, n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = filter === value ? "on" : "";
    b.innerHTML = `${label}<b>${n}</b>`;
    b.onclick = () => { filter = filter === value ? "" : value; render(); };
    box.appendChild(b);
  };
  pill("", "All", apps.length);
  for (const [value, label] of Object.entries(STATUS_LABEL)) {
    if (counts[value]) pill(value, label, counts[value]);
  }
}

function render() {
  renderSummary();
  const q = ($("hq").value || "").trim().toLowerCase();
  const tbody = $("rows");
  tbody.textContent = "";
  const shown = apps.filter((a) => matches(a, q));
  if (!shown.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">${
      apps.length ? "Nothing matches." :
      "No history yet. Set a status on a job, or import your tracker: ./run.sh import-applied private/JOB\\ APPLY.xlsx"
    }</td></tr>`;
    return;
  }
  for (const a of shown) tbody.appendChild(row(a));
}

function cell(tr, cls) {
  const td = document.createElement("td");
  if (cls) td.className = cls;
  tr.appendChild(td);
  return td;
}

function row(a) {
  const tr = document.createElement("tr");

  const date = cell(tr, "num");
  date.textContent = fmtDate(a.applied_at);
  if (a.events && a.events.length > 1) {
    date.title = a.events.map((e) => `${fmtDate(e.at)}  ${STATUS_LABEL[e.status] || e.status}`)
      .join("\n");
  }

  const who = cell(tr);
  const co = document.createElement("div");
  co.className = "co";
  if (a.url) {
    const link = document.createElement("a");
    link.href = a.url; link.target = "_blank"; link.rel = "noopener";
    link.textContent = a.company;
    co.appendChild(link);
  } else {
    co.textContent = a.company;
  }
  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = [a.title, a.location].filter(Boolean).join(" · ");
  who.append(co, sub);

  cell(tr, "num").textContent = a.experience || "—";
  const skills = cell(tr, "sub");
  skills.textContent = a.skills || "—";

  const st = cell(tr);
  st.appendChild(statusSelect(a.status, async (status, sel) => {
    try {
      await api(`/api/applications/${a.id}`, "PATCH", { status });
      await load();
    } catch (e) {
      sel.value = a.status;
      alert(`Not saved: ${e.message}`);
    }
  }));

  // Who sent it: the AI engine (with the resume it chose and a screenshot of
  // the submitted page), the engine with a step done by you, or you.
  const by = cell(tr);
  if (a.applied_by) {
    by.innerHTML = `<div class="by ai">${a.applied_by === "ai_assisted" ? "AI engine + you" : "AI engine"}</div>`;
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = a.resume || "";
    if (a.proof) {
      const link = document.createElement("a");
      link.href = `/api/apply/proof/${a.id}`; link.target = "_blank"; link.textContent = " · screenshot";
      sub.appendChild(link);
    }
    by.appendChild(sub);
  } else if (["queued", "applying", "needs_you"].includes(a.status)) {
    by.innerHTML = `<a class="by" href="/apply">AI engine (queued)</a>`;
  } else {
    by.innerHTML = `<span class="by muted">${APPLIED.has(a.status) ? "You" : "—"}</span>`;
  }

  cell(tr, "num").textContent =
    a.score !== null && a.score !== undefined ? `${a.score}/10` : "—";

  const notesTd = cell(tr);
  const notes = document.createElement("input");
  notes.className = "notes"; notes.value = a.notes || ""; notes.placeholder = "add a note";
  notes.onchange = async () => {
    try {
      await api(`/api/applications/${a.id}`, "PATCH", { status: a.status, notes: notes.value });
      a.notes = notes.value;
    } catch (e) {
      alert(`Note not saved: ${e.message}`);
    }
  };
  notesTd.appendChild(notes);

  const del = document.createElement("button");
  del.className = "del"; del.title = "Remove from history"; del.textContent = "✕";
  del.onclick = async () => {
    if (!confirm(`Remove ${a.company} — ${a.title || "this application"} from your history?`)) return;
    await api(`/api/applications/${a.id}`, "DELETE");
    await load();
  };
  cell(tr).appendChild(del);
  return tr;
}

function downloadCsv() {
  const cols = ["applied_at", "company", "title", "location", "status", "applied_by", "resume",
                "experience", "skills", "score", "url", "notes"];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.join(",")];
  for (const a of apps) {
    lines.push(cols.map((c) => esc(c === "applied_at" && a[c]
      ? new Date(a[c] * 1000).toISOString().slice(0, 10)
      : c === "applied_by" ? ({ ai_engine: "AI engine", ai_assisted: "AI engine + you" }[a[c]] ||
                              (APPLIED.has(a.status) ? "You" : ""))
      : a[c])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "job-apply-history.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

function setupAddForm() {
  const sel = $("addstatus");
  for (const [value, label] of Object.entries(STATUS_LABEL)) {
    const o = document.createElement("option");
    o.value = value; o.textContent = label;
    sel.appendChild(o);
  }
  sel.value = "applied";
  $("addform").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api("/api/applications", "POST", Object.fromEntries(f.entries()));
      e.target.reset(); sel.value = "applied";
      await load();
    } catch (err) {
      alert(`Not added: ${err.message}`);
    }
  };
}

$("hq").addEventListener("input", render);
$("csv").onclick = downloadCsv;
setupAddForm();
load();
