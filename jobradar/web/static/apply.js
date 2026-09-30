const $ = (id) => document.getElementById(id);

const BOARD = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", workday: "Workday",
                generic: "Other site" };
const WAIT_TITLE = {
  verify_email: "Verify your email",
  code: "Enter the code",
  captcha: "Solve the CAPTCHA",
  question: "The form asks you",
  manual: "Finish this step in Chrome",
};
const WAIT_BUTTON = {
  verify_email: "I've verified my email",
  code: "Continue",
  captcha: "Solved - continue",
  question: "Save answer & continue",
  manual: "I did it - learn this & continue",
};

let lastWaitKey = "";
let screenAt = 0;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

function renderCurrent(eng) {
  const box = $("current");
  const c = eng.current;
  if (!c) {
    box.innerHTML = eng.running
      ? `<div class="muted">${eng.phase === "idle" ? "Queue empty - waiting for jobs." : escapeHtml(eng.phase)}</div>`
      : `<div class="muted">The engine is stopped. Add jobs with “Add to apply queue” on the jobs page, then press Start.</div>`;
    return;
  }
  box.innerHTML = `
    <div class="kicker">Applying now · ${ago(c.started_at)}</div>
    <div class="ap-title"><a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a></div>
    <div class="ap-meta">${escapeHtml(c.company)} · ${escapeHtml(BOARD[c.board] || c.board)}
      ${c.resume ? ` · resume <b>${escapeHtml(c.resume)}</b>` : ""}</div>
    <div class="ap-stage"><span class="ap-dot ${eng.waiting ? "wait" : "go"}"></span>${escapeHtml(c.stage || "")}</div>`;
}

// While you finish a step in Chrome, what the engine has seen you do so far.
// Saved as a recipe for this page when you press the button.
function renderLearning(w) {
  const box = $("w-learn");
  box.hidden = !(w && w.kind === "manual");
  if (box.hidden) return;
  const steps = w.learning || [];
  $("w-steps").innerHTML = steps.length
    ? steps.map((s) => `<li>${escapeHtml(s)}</li>`).join("")
    : `<li class="muted">do the step in the Chrome window - clicks and answers show up here</li>`;
}

function renderWait(w) {
  const box = $("wait");
  renderLearning(w);
  if (!w) { box.hidden = true; lastWaitKey = ""; return; }
  const key = `${w.kind}|${w.message}|${w.since}`;
  box.hidden = false;
  if (key === lastWaitKey) return;          // keep what you are typing
  lastWaitKey = key;
  $("w-kind").textContent = WAIT_TITLE[w.kind] || "Needs you";
  $("w-msg").textContent = w.message;
  $("w-go").textContent = WAIT_BUTTON[w.kind] || "Continue";
  const input = $("w-input");
  input.hidden = !w.input;
  input.value = "";
  input.placeholder = w.kind === "code" ? "the code from the email (or type it in Chrome and leave this empty)"
                                        : "type your answer - it is saved for next time";
  const opts = $("w-opts");
  opts.textContent = "";
  for (const o of (w.options || []).slice(0, 40)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ap-opt"; b.textContent = o;
    b.onclick = () => { input.value = o; send(); };
    opts.appendChild(b);
  }
  if (w.input) setTimeout(() => input.focus(), 50);
  document.title = "● Needs you - Apply Status";
}

function renderLog(log) {
  const ol = $("log");
  ol.innerHTML = log.slice(-60).reverse().map((e) => {
    const t = new Date(e.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const cls = e.msg.startsWith("✓") ? "ok" : e.msg.startsWith("✗") ? "bad"
      : e.msg.startsWith("—") ? "stage" : e.msg.startsWith("waiting") ? "wait" : "";
    return `<li class="${cls}"><span class="t">${t}</span>${escapeHtml(e.msg)}</li>`;
  }).join("") || `<li class="muted">no steps yet</li>`;
}

const Q_LABEL = { queued: "queued", applying: "applying now", needs_you: "needs you" };

function renderQueue(queue) {
  const ul = $("queue");
  if (!queue.length) { ul.innerHTML = `<li class="muted">empty - add jobs from the jobs page</li>`; return; }
  ul.textContent = "";
  queue.forEach((q, i) => {
    const li = document.createElement("li");
    li.className = "ap-q " + q.status;
    li.innerHTML = `<span class="ap-n">${q.status === "queued" ? i + 1 : "▶"}</span>
      <div class="ap-qbody"><div class="co">${escapeHtml(q.company)}</div>
        <div class="sub">${escapeHtml(q.title)}</div>
        <div class="sub">${escapeHtml(BOARD[q.board] || q.board || "other site")}
          ${q.score !== null && q.score !== undefined ? ` · ${q.score}/10` : ""}
          · <span class="pill ${q.status}">${Q_LABEL[q.status] || q.status}</span></div></div>`;
    if (q.status === "queued") {
      const x = document.createElement("button");
      x.className = "del"; x.title = "Remove from the queue"; x.textContent = "✕";
      x.onclick = async () => {
        await fetch(`/api/apply/queue/${q.id}`, { method: "DELETE" });
        refresh();
      };
      li.appendChild(x);
    }
    ul.appendChild(li);
  });
}

function renderDone(done) {
  const ul = $("done");
  if (!done.length) { ul.innerHTML = `<li class="muted">nothing yet</li>`; return; }
  ul.innerHTML = done.map((d) => {
    const ok = d.applied_by && d.status !== "failed";
    const how = d.applied_by === "ai_assisted" ? "applied (AI + you)" : ok ? "applied by AI engine" : "failed";
    return `<li class="ap-q ${ok ? "ok" : "failed"}"><span class="ap-n">${ok ? "✓" : "✗"}</span>
      <div class="ap-qbody"><div class="co">${escapeHtml(d.company)}</div>
        <div class="sub">${escapeHtml(d.title)}</div>
        <div class="sub"><span class="pill ${ok ? "applied" : "rejected"}">${how}</span>
          ${d.resume ? ` · ${escapeHtml(d.resume)}` : ""}
          ${d.proof ? ` · <a href="/api/apply/proof/${d.id}" target="_blank">screenshot</a>` : ""}</div>
        ${!ok && d.notes ? `<div class="sub bad">${escapeHtml(d.notes)}</div>` : ""}</div></li>`;
  }).join("");
}

function renderScreen(eng) {
  const img = $("screen");
  if (eng.screen_at && eng.screen_at !== screenAt) {
    screenAt = eng.screen_at;
    img.src = `/api/apply/screen.jpg?t=${screenAt}`;
    img.hidden = false;
    $("noscreen").hidden = true;
  }
}

async function refresh() {
  let d;
  try {
    d = await fetch("/api/apply/state").then((r) => r.json());
  } catch {
    return;
  }
  const eng = d.engine;
  $("a-queue").textContent = d.queue.length;
  $("a-done").textContent = eng.done_this_run || 0;
  $("a-needs").textContent = eng.waiting ? 1 : 0;
  $("a-phase").textContent = eng.running ? (eng.waiting ? "waiting for you" : eng.phase) : "stopped";
  $("start").hidden = eng.running;
  $("stop").hidden = !eng.running;
  $("stop").textContent = eng.phase === "stopping" ? "Stopping…" : "Stop after this job";
  $("error").hidden = !eng.error;
  $("error").textContent = eng.error || "";
  if (!eng.waiting) document.title = "Apply Status";
  renderCurrent(eng);
  renderWait(eng.waiting);
  renderLog(eng.log || []);
  renderQueue(d.queue);
  renderDone(d.done);
  renderScreen(eng);
}

async function send() {
  const input = $("w-input");
  const answer = input.hidden ? null : input.value.trim();
  $("w-go").disabled = true;
  try {
    await post("/api/apply/resume", { answer });
  } finally {
    $("w-go").disabled = false;
    lastWaitKey = "";
    setTimeout(refresh, 300);
  }
}

$("start").onclick = async () => {
  $("start").disabled = true;
  try { await post("/api/apply/start"); } finally { $("start").disabled = false; refresh(); }
};
$("stop").onclick = async () => { await post("/api/apply/stop"); refresh(); };
$("w-go").onclick = send;
$("w-skip").onclick = async () => {
  if (!confirm("Skip this job? It leaves the queue.")) return;
  await post("/api/apply/skip");
  refresh();
};
$("w-input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

refresh();
setInterval(refresh, 1000);
