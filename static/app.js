/* Preflight Companion — local study desk.
   No framework, no build step, no third-party scripts. All model output is
   escaped before it reaches the DOM. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[ch]));

const STATUS_LABEL = {
  addressed: "addressed",
  partial: "partial",
  missing: "missing",
  contradicted: "contradicted",
  clear: "clear",
  vague: "vague",
  active: "active",
  draft: "draft",
  submitted: "submitted",
  graded: "graded",
  overdue: "overdue",
  today: "due today",
  soon: "due soon",
  week: "this week",
  later: "later",
  none: "no deadline",
};

const state = {
  status: null,
  stats: null,
  courses: [],
  assignments: [],
  checkins: [],
  memory: [],
  network: [],
  current: null,
  tab: "report",
  busy: false,
  stage: "",
  startedAt: 0,
  timer: null,
  chatBusy: false,
  viewingAudit: null,
  feedbackOpen: "",
};

/* ── api and small helpers ─────────────────────────────────────────── */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status}).`);
  }
  return data;
}

function fmtDate(value) {
  if (!value) return "not set";
  const text = String(value).replace("T", " ");
  return text.length > 16 ? text.slice(0, 16) : text;
}

function fmtTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function deadlineLabel(assignment) {
  if (!assignment) return "No deadline";
  const ds = assignment.deadline_state;
  if (ds && ds.label) return ds.label;
  return assignment.deadline ? fmtDate(assignment.deadline) : "No deadline";
}

function deadlineClass(assignment) {
  return (assignment && assignment.deadline_state && assignment.deadline_state.state) || "none";
}

function inputDeadline(value) {
  if (!value) return "";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text + "T23:59";
  return text.slice(0, 16);
}

function toast(message, kind = "") {
  const stack = $("#toastStack");
  if (!stack) return;
  const node = document.createElement("div");
  node.className = "toast" + (kind === "error" ? " error" : "");
  const label = document.createElement("b");
  label.textContent = kind === "error" ? "Could not complete" : "Preflight";
  const body = document.createElement("span");
  body.textContent = message;
  node.append(label, body);
  stack.appendChild(node);
  window.setTimeout(() => node.remove(), 6000);
}

function startTimer() {
  state.startedAt = Date.now();
  window.clearInterval(state.timer);
  state.timer = window.setInterval(() => {
    const node = $("#elapsed");
    if (node) node.textContent = ((Date.now() - state.startedAt) / 1000).toFixed(1) + "s";
  }, 100);
}

function stopTimer() {
  window.clearInterval(state.timer);
  state.timer = null;
}

function scrollChat() {
  const log = $("#chatLog");
  if (log) log.scrollTop = log.scrollHeight;
}

function statusChip(value, prefix = "") {
  const key = String(value || "unknown");
  return `<span class="status-chip ${esc(key)}">${esc(prefix + (STATUS_LABEL[key] || key))}</span>`;
}

function kindLabel(kind) {
  return (STATUS_LABEL[kind] || kind || "process").replace("-", " ");
}

/* ── bootstrap ─────────────────────────────────────────────────────── */

async function init() {
  bindGlobal();
  try {
    await refreshBootstrap();
  } catch (error) {
    $("#view").innerHTML = `<div class="empty-state"><div class="empty-inner">
      <div class="empty-mark">!</div>
      <h2>The local server is not answering.</h2>
      <p>${esc(error.message)}</p>
      <button class="button primary" onclick="location.reload()">Try again</button>
    </div></div>`;
    return;
  }

  const match = location.hash.match(/^#a\/([a-f0-9]+)$/);
  if (match) {
    try { await openAssignment(match[1]); } catch (_) { renderDashboard(); }
  } else {
    renderDashboard();
  }

  window.setInterval(refreshStatusOnly, 30000);
}

async function refreshBootstrap() {
  const data = await api("/api/bootstrap");
  state.status = data.status;
  state.stats = data.stats;
  state.courses = data.courses || [];
  state.assignments = data.assignments || [];
  state.checkins = data.checkins || [];
  state.memory = data.memory || [];
  state.network = data.network || [];
  renderStatus();
  renderSidebar();
}

async function refreshStatusOnly() {
  try {
    const data = await api("/api/status");
    state.status = data;
    renderStatus();
  } catch (_) { /* the app still works from its last known state */ }
}

function renderStatus() {
  const ready = Boolean(state.status && state.status.ready);
  const dot = $("#statusDot");
  const label = $("#modelLabel");
  if (dot) {
    dot.className = "status-dot " + (ready ? "ready" : "offline");
  }
  if (label) {
    label.textContent = ready
      ? (state.status.model || "local model ready")
      : "local model offline";
    label.title = ready
      ? "Inference runs on this machine"
      : "The report still works; the deterministic plan is used";
  }
}

/* ── sidebar and dashboard ─────────────────────────────────────────── */

function renderSidebar() {
  const list = $("#assignmentList");
  if (!list) return;
  if (!state.assignments.length) {
    list.innerHTML = `<div class="rail-empty">No assignments yet. Add one to begin.</div>`;
  } else {
    list.innerHTML = state.assignments.map((assignment) => {
      const active = state.current && state.current.id === assignment.id;
      const dl = deadlineLabel(assignment);
      const cls = deadlineClass(assignment);
      return `<button class="assignment-item ${active ? "is-active" : ""}" data-id="${esc(assignment.id)}" type="button">
        <span class="item-course">
          <span>${esc(assignment.course_code || "General")}</span>
          <span>${esc(dl)}</span>
        </span>
        <span class="item-title">${esc(assignment.title)}</span>
        <span class="item-meta">
          <span>${esc(assignment.status)}</span>
          <span class="dot-sep"></span>
          <span>${assignment.submission_count || 0} submission${(assignment.submission_count || 0) === 1 ? "" : "s"}</span>
          ${assignment.deadline ? `<span class="dot-sep"></span><span>${esc(dl)}</span>` : ""}
        </span>
      </button>`;
    }).join("");
  }
  $$(".assignment-item", list).forEach((node) => {
    node.addEventListener("click", () => openAssignment(node.dataset.id));
  });

  const first = state.checkins[0];
  const strip = $("#deadlineStrip");
  if (strip) {
    if (first) {
      strip.hidden = false;
      strip.innerHTML = `<b>${esc(first.headline)}</b><span>${esc(first.course || "General")} · ${esc(first.title || "")}</span>`;
      strip.style.cursor = "pointer";
      strip.onclick = () => openAssignment(first.assignment_id);
    } else {
      strip.hidden = true;
      strip.onclick = null;
    }
  }
}

function renderDashboard() {
  state.viewingAudit = null;
  state.current = null;
  document.title = "Preflight Companion — check before you build";
  if (location.hash) history.replaceState(null, "", location.pathname);
  renderSidebar();

  const active = state.assignments.filter((a) => a.status === "active" || a.status === "draft").length;
  const first = state.checkins[0];
  const cards = state.assignments.map((assignment) => `
    <button class="assignment-card" data-id="${esc(assignment.id)}" type="button">
      <div class="card-top">
        <span class="course-code">${esc(assignment.course_code || "General")}</span>
        <span class="status-pill ${esc(assignment.status)}">${esc(assignment.status)}</span>
      </div>
      <h3>${esc(assignment.title)}</h3>
      <div class="card-meta">
        <span>${esc(deadlineLabel(assignment))}</span>
        <span>·</span>
        <span>${assignment.audit_count || 0} preflight${(assignment.audit_count || 0) === 1 ? "" : "s"}</span>
        ${assignment.submission_count ? `<span>·</span><span>${assignment.submission_count} version${assignment.submission_count === 1 ? "" : "s"}</span>` : ""}
      </div>
      <div class="card-foot">
        <div>
          <span class="next-label">Next</span>
          <span class="next-value">${assignment.deadline ? esc(deadlineLabel(assignment)) : "Open the desk and run a preflight."}</span>
        </div>
        <span class="card-arrow">→</span>
      </div>
    </button>`).join("");

  $("#view").innerHTML = `<section class="dashboard">
    <header class="dash-head">
      <div>
        <p class="eyebrow"><span>02</span> Preflight desk</p>
        <h1>Check before<br><em>you build.</em></h1>
        <p>Audit the brief, check the draft against the evidence, and leave with a short plan. The coach remembers what you keep missing.</p>
      </div>
      <div class="dash-aside">
        <span class="big-number">${active}</span>
        <span class="small-label">active assignment${active === 1 ? "" : "s"}</span>
      </div>
    </header>
    ${first ? renderCheckinBanner(first) : ""}
    ${state.assignments.length
      ? `<div class="assignment-grid">${cards}</div>`
      : `<div class="empty-state"><div class="empty-inner">
          <div class="empty-mark">01</div>
          <h2>Nothing on the desk yet.</h2>
          <p>Add an assignment brief, or load the curated demo to see the audit, the memory, and the coach in one flow.</p>
          <button class="button primary" id="emptyNew" type="button">Add an assignment</button>
          <button class="button ghost" id="emptyDemo" type="button">Load the demo</button>
        </div></div>`}
  </section>`;

  $$(".assignment-card", $("#view")).forEach((node) => {
    node.addEventListener("click", () => openAssignment(node.dataset.id));
  });
  const emptyNew = $("#emptyNew");
  if (emptyNew) emptyNew.onclick = openModal;
  const emptyDemo = $("#emptyDemo");
  if (emptyDemo) emptyDemo.onclick = loadDemo;
  const go = $("#checkinGo");
  if (go) go.onclick = () => openAssignment(first.assignment_id);
}

function renderCheckinBanner(checkin) {
  if (!checkin) return "";
  const short = checkin.state === "overdue" ? "!" : checkin.state === "today" ? "24h" : checkin.state === "soon" ? "48h" : "WK";
  return `<div class="checkin-banner ${esc(checkin.state)}">
    <span class="checkin-icon">${esc(short)}</span>
    <div>
      <b>${esc(checkin.headline)}</b>
      <span>${esc(checkin.title || "")}${checkin.course ? " · " + esc(checkin.course) : ""} — ${esc(checkin.detail)}</span>
    </div>
    <button class="checkin-go" id="checkinGo" type="button">Open →</button>
  </div>`;
}

/* ── assignment view ───────────────────────────────────────────────── */

async function openAssignment(id) {
  if (!id) return;
  const data = await api(`/api/assignments/${id}`);
  state.current = data;
  state.viewingAudit = null;
  state.tab = "report";
  state.busy = false;
  state.stage = "";
  location.hash = `#a/${id}`;
  renderAssignment();
}

function renderAssignment() {
  const current = state.current;
  if (!current) { renderDashboard(); return; }
  state.viewingAudit = state.viewingAudit || null;
  document.title = `${current.title} — Preflight Companion`;
  renderSidebar();

  const audit = state.viewingAudit || current.latest_audit;
  const reqCount = ((audit && audit.report && audit.report.requirements) || []).length;
  const subCount = (current.submissions || []).length;
  const msgCount = (current.messages || []).length;
  const memCount = (current.memory || []).length;

  $("#view").innerHTML = `<section class="assignment-view">
    <header class="assignment-head">
      <div class="assignment-kicker">
        <span>${esc(current.course_code || "General")}</span>
        <span class="slash">/</span>
        <span>${esc(current.status)}</span>
        <span class="slash">/</span>
        <span>${esc(deadlineLabel(current))}</span>
        ${current.brief_source ? `<span class="slash">/</span><span>${esc(current.brief_source)}</span>` : ""}
      </div>
      <h1 class="assignment-title">${esc(current.title)}</h1>
      <div class="assignment-sub">
        <span>${esc(current.course_title || "Engineering")}</span>
        <span class="sep"></span>
        <span>Deadline ${esc(current.deadline ? fmtDate(current.deadline) : "not set")}</span>
        <span class="sep"></span>
        <span>${reqCount} requirement${reqCount === 1 ? "" : "s"}</span>
        <span class="sep"></span>
        <span>${subCount} submission${subCount === 1 ? "" : "s"}</span>
      </div>
      <div class="assignment-actions">
        <button class="button primary" id="runButton" type="button" ${state.busy ? "disabled" : ""}>${state.busy ? "Running preflight…" : "Run preflight"}</button>
        <button class="button" id="saveButton" type="button">Save draft</button>
        <button class="button" id="submitButton" type="button" ${current.status === "submitted" ? "disabled" : ""}>Mark submitted</button>
        <a class="button ghost" href="/api/assignments/${esc(current.id)}/export.md">Export .md</a>
        <a class="button ghost" href="/api/assignments/${esc(current.id)}/export.json">Export .json</a>
        <button class="button danger" id="deleteButton" type="button">Forget</button>
      </div>
    </header>

    ${current.checkin ? renderCheckinBanner(current.checkin) : ""}

    <div class="workspace">
      <section class="panel editor-card">
        <div class="panel-head">
          <div class="panel-title"><span class="num">A</span> Brief &amp; draft</div>
          <span class="panel-note">runs here</span>
        </div>
        <div class="editor-body">
          <div class="editor-field">
            <div class="field-label"><label for="briefInput">Assignment brief</label><span class="label-tools"><button class="link-button" id="briefFileButton" type="button">Load .md/.txt</button><span class="char-count" id="briefCount">${(current.brief_text || "").length}</span></span></div>
            <textarea class="brief" id="briefInput" spellcheck="false" placeholder="Paste the assignment brief here…">${esc(current.brief_text || "")}</textarea>
            <input id="briefFileInput" type="file" accept=".txt,.md,.markdown,.py,.csv,.json,text/plain,text/markdown" hidden>
            <span class="hint">Every requirement is quoted from this text. The model cannot add one.</span>
          </div>
          <div class="editor-field">
            <div class="field-label"><label for="draftInput">Draft <span class="optional">optional</span></label><span class="label-tools"><button class="link-button" id="draftFileButton" type="button">Load .md/.txt</button><span class="char-count" id="draftCount">${(current.draft_text || "").length}</span></span></div>
            <textarea class="draft" id="draftInput" spellcheck="false" placeholder="Paste a draft to check coverage against the brief…">${esc(current.draft_text || "")}</textarea>
            <input id="draftFileInput" type="file" accept=".txt,.md,.markdown,.py,.csv,.json,text/plain,text/markdown" hidden>
            <span class="hint">Coverage says whether evidence was found, not whether you will pass.</span>
          </div>
          <label class="editor-field">
            <span class="field-label"><span>Deadline</span><span class="char-count">local</span></span>
            <input id="deadlineInput" type="datetime-local" value="${esc(inputDeadline(current.deadline))}">
          </label>
          <div class="editor-actions">
            <div class="stage-line" aria-live="polite" id="stageLine">
              ${state.busy
                ? `<span class="pulse"></span><span id="stageText">${esc(state.stage || "Working…")}</span><span id="elapsed">0.0s</span>`
                : `<span>${audit ? `Last run ${esc(fmtTime(audit.created_at))} · ${esc(((audit.duration_ms || 0) / 1000).toFixed(1))}s` : "No preflight yet."}</span>`}
            </div>
            <button class="button primary" id="runButtonBottom" type="button" ${state.busy ? "disabled" : ""}>${state.busy ? "Working…" : "Run preflight"}</button>
          </div>
        </div>
        ${audit ? `<div class="audit-meta">
          <span>audit <b>${esc(audit.id.slice(0, 8))}</b></span>
          <span>${audit.model_used ? "model plan" : "deterministic plan"}</span>
          <span>${esc(((audit.duration_ms || 0) / 1000).toFixed(1))}s</span>
          <span>${esc(fmtTime(audit.created_at))}</span>
        </div>` : ""}
      </section>

      <section class="panel report-panel">
        <div class="panel-head">
          <div class="panel-title"><span class="num">B</span> ${state.viewingAudit ? "Past report" : "Preflight report"}</div>
          <span class="panel-note">${state.viewingAudit ? "read-only snapshot" : "evidence first"}</span>
        </div>
        <div class="tabs" role="tablist" aria-label="Assignment views">
          <button class="tab ${state.tab === "report" ? "is-active" : ""}" data-tab="report" type="button" role="tab" aria-selected="${state.tab === "report"}">Report <span class="tab-count">${reqCount}</span></button>
          <button class="tab ${state.tab === "coach" ? "is-active" : ""}" data-tab="coach" type="button" role="tab" aria-selected="${state.tab === "coach"}">Coach <span class="tab-count">${msgCount}</span></button>
          <button class="tab ${state.tab === "history" ? "is-active" : ""}" data-tab="history" type="button" role="tab" aria-selected="${state.tab === "history"}">History <span class="tab-count">${subCount}</span></button>
          <button class="tab ${state.tab === "memory" ? "is-active" : ""}" data-tab="memory" type="button" role="tab" aria-selected="${state.tab === "memory"}">Memory <span class="tab-count">${memCount}</span></button>
        </div>
        <div id="tabBody">${renderTabBody()}</div>
      </section>
    </div>
  </section>`;

  bindAssignmentEvents();
  if (state.tab === "coach") scrollChat();
  if (state.busy) startTimerIfNeeded();
}

function startTimerIfNeeded() {
  const node = $("#elapsed");
  if (node && !state.timer) startTimer();
}

function renderTabBody() {
  if (state.tab === "coach") return renderCoach();
  if (state.tab === "history") return renderHistory();
  if (state.tab === "memory") return renderMemory();
  return renderReport();
}

/* ── report ────────────────────────────────────────────────────────── */

function renderReport() {
  const current = state.current;
  const audit = state.viewingAudit || (current && current.latest_audit);
  if (!current) return "";
  if (!audit) {
    return `<div class="report-body">
      <div class="empty-state" style="min-height:300px"><div class="empty-inner">
        <div class="empty-mark">A</div>
        <h2>No preflight yet.</h2>
        <p>Paste the brief and run a preflight. The evidence table appears before the model writes the plan.</p>
        <button class="button primary" id="runFromReport" type="button">Run preflight</button>
      </div></div>
    </div>`;
  }
  const report = audit.report || {};
  const coverage = report.coverage || {};
  const plan = audit.plan || {};
  const requirements = report.requirements || [];
  const items = coverage.items || [];
  const hasDraft = Boolean(coverage.has_draft);
  const counts = coverage.counts || {};
  const pct = coverage.coverage_pct || 0;
  const reqById = {};
  requirements.forEach((req) => { reqById[req.id] = req; });

  const reqRows = (items.length ? items : requirements.map((req) => ({
    id: req.id, text: req.text, kind: req.kind, status: "missing",
    requirement_status: req.status, note: "Add a draft to check this requirement.",
  }))).map((item) => {
    const req = reqById[item.id] || {};
    const quality = req.status || item.requirement_status || "clear";
    const draftStatus = hasDraft ? item.status : null;
    return `<div class="requirement">
      <div class="req-id">${esc(item.id || req.id || "R")}</div>
      <div class="req-main">
        <div class="req-head">
          <span class="req-kind">${esc(kindLabel(req.kind || item.kind))}</span>
          ${statusChip(quality)}
          ${draftStatus ? statusChip(draftStatus, "draft: ") : `<span class="status-chip unknown">brief only</span>`}
        </div>
        <div class="req-text">${esc(item.text || req.text || "")}</div>
        <div class="req-quote">${esc(req.source_ref || item.source_ref || "source unknown")} — “${esc(req.evidence || item.evidence || "")}”</div>
        ${hasDraft ? `<div class="req-note">${esc(item.note || "")}</div>` : ""}
      </div>
    </div>`;
  }).join("");

  const gaps = (coverage.gaps || []).map((gap) => `
    <div class="gap-item ${esc(gap.status)}">
      <b>${esc(gap.id)} · ${esc(gap.status)}</b> ${esc(gap.text || "")}
      <div style="color:var(--muted);margin-top:2px">${esc(gap.note || "")}</div>
    </div>`).join("");

  const contradictions = (report.contradictions || []).map((item) => `
    <div class="ambiguity-item"><b>conflict</b> ${esc(item.text)}<span>${esc(item.a_ref)} / ${esc(item.b_ref)}</span></div>
  `).join("");

  const ambiguities = (report.ambiguities || []).map((item) => `
    <div class="ambiguity-item"><b>${esc(item.term)}</b> ${esc(item.suggestion)}<span>${esc(item.source_ref)} — “${esc(item.text)}”</span></div>
  `).join("");

  const actions = (plan.actions || []).map((action, index) => `
    <div class="plan-action">
      <div class="plan-num">0${index + 1}</div>
      <div>
        <div class="plan-text">${esc(action.text)}</div>
        ${action.why ? `<div class="plan-why">${esc(action.why)}</div>` : ""}
      </div>
      <div class="plan-min">${esc(action.minutes || 15)} min</div>
    </div>`).join("");

  const memory = (current.memory || []).slice(0, 2);
  const memoryHtml = memory.length ? `<div class="memory-note"><b>Coach remembers</b>${memory.map((item) => esc(item.text)).join(" ")}</div>` : "";

  const viewing = state.viewingAudit ? `<div class="memory-note" style="border-style:solid"><b>Past snapshot</b>
    You are reading the report from ${esc(fmtTime(state.viewingAudit.created_at))}.
    <button class="link-button" id="backToLatest" type="button" style="margin-left:8px">Back to latest</button></div>` : "";

  return `<div class="report-body">
    ${viewing}
    <div class="report-summary">
      <div>
        <h3>${hasDraft ? `Assignment checklist: evidence found for ${pct}% of requirements.` : `${requirements.length} requirements extracted.`}</h3>
        <p>${hasDraft
          ? `${counts.addressed || 0} addressed · ${counts.partial || 0} partial · ${counts.missing || 0} missing · ${counts.contradicted || 0} contradicted. This is evidence from your draft, not a grade or an engineering correctness/safety check.`
          : "Add a draft to check which requirements have evidence. This is not a pass prediction."}</p>
      </div>
      <div class="coverage-number">
        <b>${hasDraft ? pct + "%" : "—"}</b>
        <span>${hasDraft ? "evidence found" : "brief only"}</span>
      </div>
    </div>
    <div class="coverage-bar"><div class="coverage-fill" style="width:${hasDraft ? pct : 0}%"></div></div>
    <div class="coverage-legend">
      <span><i style="background:var(--up)"></i>addressed</span>
      <span><i style="background:var(--amber)"></i>partial</span>
      <span><i style="background:var(--signal)"></i>missing</span>
      <span><i style="background:var(--signal-dark)"></i>contradicted</span>
    </div>

    <div class="section-label"><span>Requirements</span><span class="count">${requirements.length}</span></div>
    <div class="requirement-list">${reqRows || `<div class="rail-note">No requirement sentences were found. Add a more specific brief.</div>`}</div>

    ${gaps ? `<div class="section-label"><span>Draft gaps</span><span class="count">${(coverage.gaps || []).length}</span></div><div class="gap-list">${gaps}</div>` : ""}
    ${contradictions ? `<div class="section-label"><span>Conflicts</span><span class="count">${(report.contradictions || []).length}</span></div>${contradictions}` : ""}
    ${ambiguities ? `<div class="section-label"><span>Ambiguities</span><span class="count">${(report.ambiguities || []).length}</span></div>${ambiguities}` : ""}

    <div class="plan-card">
      <div class="plan-head">
        <div class="panel-title"><span class="num">C</span> Fastest safe path</div>
        <span class="panel-note">${esc(plan.source || "deterministic")}${plan.estimated_minutes ? ` · ${plan.estimated_minutes} min` : ""}</span>
      </div>
      <div class="plan-body">
        ${actions || `<div class="rail-note">The plan appears after the evidence table. Run a preflight.</div>`}
        ${plan.risk ? `<div class="risk-line"><b>Coach's read</b><br>${esc(plan.risk)}</div>` : ""}
        ${plan.note ? `<div class="memory-note">${esc(plan.note)}</div>` : ""}
      </div>
    </div>
    ${memoryHtml}
  </div>`;
}

/* ── coach chat ────────────────────────────────────────────────────── */

function renderCoach() {
  const current = state.current;
  const messages = (current && current.messages) || [];
  const log = messages.length ? messages.map((message) => `
    <div class="chat-msg ${message.role === "user" ? "user" : "coach"}">
      ${esc(message.content)}
      <span class="msg-meta">${message.role === "user" ? "you" : "coach"} · ${esc(fmtTime(message.created_at))}</span>
    </div>`).join("") : `<div class="chat-empty">
      <b>The coach is listening.</b>
      Ask about the deadline, the next step, or a finding in the report. It will not write the assignment or solve the problem.
    </div>`;

  return `<div class="chat-body">
    <div class="chat-log" id="chatLog">
      ${log}
      ${state.chatBusy ? `<div class="chat-typing"><span class="pulse"></span>the local coach is thinking…</div>` : ""}
    </div>
    <div class="chat-form">
      <div class="quick-chips">
        <button class="quick-chip" data-question="What should I do first?" type="button">What first?</button>
        <button class="quick-chip" data-question="What is missing from the draft?" type="button">What is missing?</button>
        <button class="quick-chip" data-question="Am I ready to submit?" type="button">Ready to submit?</button>
        <button class="quick-chip" data-question="What is the deadline risk?" type="button">Deadline risk?</button>
      </div>
      <form class="chat-row" id="chatForm">
        <textarea id="chatInput" rows="1" maxlength="1200" placeholder="Ask the coach about this assignment…" aria-label="Message the coach"></textarea>
        <button class="send-button" type="submit" aria-label="Send message" ${state.chatBusy ? "disabled" : ""}>→</button>
      </form>
      <div class="chat-foot">
        <span>local model · answer grounded in your report · evidence, not a grade</span>
        <button class="link-button" id="clearChat" type="button">Clear chat</button>
      </div>
    </div>
  </div>`;
}

async function sendChat(text) {
  const current = state.current;
  if (!current || !text.trim() || state.chatBusy) return;
  state.chatBusy = true;
  current.messages = current.messages || [];
  current.messages.push({
    id: "pending-" + Date.now(), role: "user", content: text.trim(),
    created_at: new Date().toISOString(),
  });
  state.tab = "coach";
  renderAssignment();
  scrollChat();
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ assignment_id: current.id, message: text.trim() }),
    });
    current.messages[current.messages.length - 1] = data.user_message || {
      ...current.messages[current.messages.length - 1],
      id: "sent-" + Date.now(),
    };
    current.messages.push(data.coach || {
      id: "reply-" + Date.now(), role: "coach", content: data.reply,
      created_at: new Date().toISOString(),
    });
    state.chatBusy = false;
    renderAssignment();
    scrollChat();
    refreshBootstrap().catch(() => {});
  } catch (error) {
    state.chatBusy = false;
    toast(error.message, "error");
    renderAssignment();
  }
}

/* ── history and diffs ─────────────────────────────────────────────── */

const RANK = { missing: 0, partial: 1, contradicted: 1, addressed: 2 };

function diffCoverage(previous, current) {
  const prev = new Map((previous || []).map((item) => [String(item.text || "").toLowerCase(), item]));
  let improved = 0, regressed = 0;
  (current || []).forEach((item) => {
    const old = prev.get(String(item.text || "").toLowerCase());
    if (!old) return;
    const a = RANK[old.status] ?? 0;
    const b = RANK[item.status] ?? 0;
    if (b > a) improved += 1;
    if (b < a) regressed += 1;
  });
  return { improved, regressed };
}

function renderHistory() {
  const current = state.current;
  if (!current) return "";
  const audits = current.audits || [];
  const submissions = current.submissions || [];

  const auditRows = audits.map((audit) => {
    const actions = ((audit.plan && audit.plan.actions) || []).length;
    return `<div class="history-item">
      <div>
        <h4>${esc(fmtTime(audit.created_at))}</h4>
        <div class="history-meta">${audit.model_used ? "model plan" : "deterministic plan"} · ${esc(((audit.duration_ms || 0) / 1000).toFixed(1))}s · ${actions} actions</div>
        <div class="history-note">${esc(((audit.report && audit.report.coverage && audit.report.coverage.counts) ? `${audit.report.coverage.counts.addressed || 0} addressed / ${audit.report.coverage.counts.missing || 0} missing` : "brief only"))}</div>
      </div>
      <div class="history-actions">
        <button class="button small" data-audit="${esc(audit.id)}" type="button">View</button>
      </div>
    </div>`;
  }).join("");

  const subRows = submissions.map((submission, index) => {
    const older = submissions[index + 1];
    const delta = older ? diffCoverage(older.coverage, submission.coverage) : null;
    const feedback = submission.feedback || [];
    return `<div class="history-item">
      <div>
        <h4>Version ${submission.version} <span class="status-pill submitted">submitted</span></h4>
        <div class="history-meta">${esc(fmtTime(submission.submitted_at))} · ${(submission.gaps || []).length} gap(s)</div>
        ${submission.notes ? `<div class="history-note">${esc(submission.notes)}</div>` : ""}
        ${delta ? `<div class="diff-row">
          <span class="diff-chip up">+${delta.improved} improved</span>
          <span class="diff-chip down">−${delta.regressed} regressed</span>
        </div>` : ""}
        ${feedback.map((item) => `<div class="history-note">Feedback: ${esc(item.grade || "no grade")} — ${esc(item.comments || "")}</div>`).join("")}
        ${state.feedbackOpen === submission.id ? `
          <form class="feedback-form" data-submission="${esc(submission.id)}" style="margin-top:8px">
            <input name="grade" placeholder="Grade (optional)" maxlength="40">
            <textarea name="comments" rows="2" placeholder="Instructor feedback…" maxlength="2000"></textarea>
            <div class="diff-row"><button class="button small primary" type="submit">Save feedback</button></div>
          </form>` : ""}
      </div>
      <div class="history-actions">
        <button class="button small" data-feedback="${esc(submission.id)}" type="button">${state.feedbackOpen === submission.id ? "Close" : "Feedback"}</button>
      </div>
    </div>`;
  }).join("");

  return `<div class="report-body">
    <div class="section-label"><span>Preflight runs</span><span class="count">${audits.length}</span></div>
    <div class="history-list">${auditRows || `<div class="rail-note">No runs yet.</div>`}</div>
    <div class="section-label"><span>Submission versions</span><span class="count">${submissions.length}</span></div>
    <div class="history-list">${subRows || `<div class="rail-note">Mark the assignment submitted to start a version history. The companion uses this to find recurring gaps.</div>`}</div>
    <div class="memory-note"><b>How memory works</b>A pattern appears only after it shows up in at least two submissions. Nothing is sent off this machine.</div>
  </div>`;
}

/* ── memory and settings ───────────────────────────────────────────── */

function renderMemory() {
  const current = state.current;
  const memory = (current && current.memory) || [];
  const rows = memory.map((item) => `
    <div class="memory-item">
      <div>
        <div class="mem-kind">${esc(item.kind)}</div>
        <div class="mem-text">${esc(item.text)}</div>
        <div class="mem-meta">confidence ${Math.round((item.confidence || 0) * 100)}% · ${esc(fmtTime(item.created_at))}</div>
      </div>
      <button data-memory="${esc(item.id)}" type="button" title="Delete this memory" aria-label="Delete this memory">×</button>
    </div>`).join("");

  const ledger = (state.network || []).map((row) => `
    <div class="ledger-row"><b>${esc(row.status)}</b><span>${esc(row.url)}</span><span>${esc(fmtTime(row.created_at))}</span></div>
  `).join("");

  return `<div class="report-body">
    <div class="section-label"><span>Companion memory</span><span class="count">${memory.length}</span></div>
    <div class="memory-list">${rows || `<div class="rail-note">No memories for this assignment yet. They are created after submissions.</div>`}</div>
    <div class="memory-note"><b>What is stored</b>Recurring gaps, deadline facts, and short coach notes. No raw chat transcript leaves the database, and every item here can be deleted.</div>

    <div class="section-label"><span>Network ledger</span><span class="count">${(state.network || []).length}</span></div>
    <div class="ledger">${ledger || `<div class="ledger-empty">No outbound requests. Local-only mode is on.</div>`}</div>

    <div class="section-label"><span>Export &amp; privacy</span></div>
    <div class="export-row">
      <a class="button small" href="/api/assignments/${esc(current.id)}/export.md">Download report .md</a>
      <a class="button small" href="/api/assignments/${esc(current.id)}/export.json">Download data .json</a>
    </div>
    <div class="settings-row">
      <div><b>Web reference lookup</b><span>Disabled in this build. No brief, draft, or memory is sent anywhere.</span></div>
      <span class="toggle" aria-label="Web lookup is off"></span>
    </div>
    <div class="settings-row">
      <div><b>Local model</b><span>${esc((state.status && state.status.model) || "checking")} · llama.cpp · one short call per audit</span></div>
      <span class="status-chip ${state.status && state.status.ready ? "addressed" : "missing"}">${state.status && state.status.ready ? "ready" : "offline"}</span>
    </div>
    <div class="settings-row">
      <div><b>Forget this assignment</b><span>Deletes the assignment, its reports, submissions, chat, and memory.</span></div>
      <button class="button small danger" id="forgetFromMemory" type="button">Delete</button>
    </div>
  </div>`;
}

/* ── assignment event wiring ───────────────────────────────────────── */

function bindAssignmentEvents() {
  const run = $("#runButton");
  if (run) run.onclick = () => runPreflight();
  const runBottom = $("#runButtonBottom");
  if (runBottom) runBottom.onclick = () => runPreflight();
  const runFromReport = $("#runFromReport");
  if (runFromReport) runFromReport.onclick = () => runPreflight();

  const save = $("#saveButton");
  if (save) save.onclick = saveEditor;
  const submit = $("#submitButton");
  if (submit) submit.onclick = submitAssignment;
  const del = $("#deleteButton");
  if (del) del.onclick = deleteCurrent;
  const forget = $("#forgetFromMemory");
  if (forget) forget.onclick = deleteCurrent;

  $$(".tab").forEach((tab) => {
    tab.onclick = () => {
      state.tab = tab.dataset.tab;
      state.feedbackOpen = "";
      renderAssignment();
    };
  });

  const back = $("#backToLatest");
  if (back) back.onclick = () => {
    state.viewingAudit = null;
    state.tab = "report";
    renderAssignment();
  };

  $$("[data-audit]").forEach((button) => {
    button.onclick = () => {
      const audit = (state.current.audits || []).find((item) => item.id === button.dataset.audit);
      if (audit) {
        state.viewingAudit = audit;
        state.tab = "report";
        renderAssignment();
      }
    };
  });

  $$("[data-feedback]").forEach((button) => {
    button.onclick = () => {
      state.feedbackOpen = state.feedbackOpen === button.dataset.feedback ? "" : button.dataset.feedback;
      state.tab = "history";
      renderAssignment();
    };
  });

  $$(".feedback-form").forEach((form) => {
    form.onsubmit = async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      try {
        await api("/api/feedback", {
          method: "POST",
          body: JSON.stringify({
            submission_id: form.dataset.submission,
            grade: data.get("grade") || "",
            comments: data.get("comments") || "",
          }),
        });
        toast("Feedback saved locally.");
        state.feedbackOpen = "";
        await openAssignment(state.current.id);
        state.tab = "history";
        renderAssignment();
      } catch (error) { toast(error.message, "error"); }
    };
  });

  $$("[data-memory]").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/memory/delete", {
          method: "POST",
          body: JSON.stringify({ id: button.dataset.memory }),
        });
        toast("Memory deleted.");
        await openAssignment(state.current.id);
        state.tab = "memory";
        renderAssignment();
        refreshBootstrap().catch(() => {});
      } catch (error) { toast(error.message, "error"); }
    };
  });

  const chatForm = $("#chatForm");
  if (chatForm) {
    chatForm.onsubmit = (event) => {
      event.preventDefault();
      const input = $("#chatInput");
      const text = input ? input.value : "";
      if (text && text.trim()) sendChat(text);
    };
  }
  $$("[data-question]").forEach((button) => {
    button.onclick = () => sendChat(button.dataset.question);
  });
  const clearChat = $("#clearChat");
  if (clearChat) {
    clearChat.onclick = async () => {
      if (!confirm("Clear this assignment's coach chat? The reports and memory stay.")) return;
      try {
        await api("/api/messages/clear", {
          method: "POST",
          body: JSON.stringify({ assignment_id: state.current.id }),
        });
        state.current.messages = [];
        renderAssignment();
        toast("Chat cleared.");
      } catch (error) { toast(error.message, "error"); }
    };
  }

  const brief = $("#briefInput");
  const draft = $("#draftInput");
  const briefCount = $("#briefCount");
  const draftCount = $("#draftCount");
  if (brief && briefCount) brief.oninput = () => { briefCount.textContent = brief.value.length; };
  if (draft && draftCount) draft.oninput = () => { draftCount.textContent = draft.value.length; };

  bindTextFile("#briefFileButton", "#briefFileInput", "#briefInput", "#briefCount");
  bindTextFile("#draftFileButton", "#draftFileInput", "#draftInput", "#draftCount");
}

function bindTextFile(buttonSelector, inputSelector, textareaSelector, countSelector) {
  const button = $(buttonSelector);
  const input = $(inputSelector);
  const area = $(textareaSelector);
  const count = $(countSelector);
  if (!button || !input || !area) return;
  button.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    input.click();
  };
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > 200_000) {
      toast("That file is too large. Keep text files under 200 KB.", "error");
      input.value = "";
      return;
    }
    try {
      const text = await file.text();
      area.value = text;
      if (count) count.textContent = String(text.length);
      toast(`Loaded ${file.name} locally. It stays in this browser tab.`);
    } catch (_) {
      toast("Could not read that file.", "error");
    }
    input.value = "";
  };
}

async function saveEditor() {
  const current = state.current;
  if (!current) return;
  const brief = $("#briefInput");
  const draft = $("#draftInput");
  const deadline = $("#deadlineInput");
  try {
    await api(`/api/assignments/${current.id}/update`, {
      method: "POST",
      body: JSON.stringify({
        brief: brief ? brief.value : current.brief_text,
        draft: draft ? draft.value : current.draft_text,
        deadline: deadline ? deadline.value : current.deadline,
      }),
    });
    toast("Draft saved locally.");
    await openAssignment(current.id);
    refreshBootstrap().catch(() => {});
  } catch (error) { toast(error.message, "error"); }
}

/* ── preflight stream ──────────────────────────────────────────────── */

async function runPreflight() {
  if (state.busy || !state.current) return;
  const current = state.current;
  const brief = $("#briefInput");
  const draft = $("#draftInput");
  const deadline = $("#deadlineInput");
  if (!brief || !brief.value.trim()) {
    toast("Add the assignment brief first.", "error");
    return;
  }
  current.brief_text = brief.value;
  current.draft_text = draft ? draft.value : "";
  current.deadline = deadline ? deadline.value : current.deadline;

  state.busy = true;
  state.stage = "Reading the brief on this machine.";
  state.viewingAudit = null;
  state.tab = "report";
  renderAssignment();
  startTimer();

  try {
    const response = await fetch("/api/preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        assignment_id: current.id,
        brief: current.brief_text,
        draft: current.draft_text,
        deadline: current.deadline,
      }),
    });
    if (!response.ok) {
      let message = `Preflight failed (${response.status}).`;
      try { const data = await response.json(); message = data.error || message; } catch (_) {}
      throw new Error(message);
    }
    await readNDJSON(response, (event) => {
      if (event.type === "stage") {
        state.stage = event.message || "Working…";
        const node = $("#stageText");
        if (node) node.textContent = state.stage;
        return;
      }
      if (event.type === "report") {
        state.current.latest_audit = {
          report: event.report,
          plan: {},
          model_used: false,
          duration_ms: 0,
          created_at: new Date().toISOString(),
          id: "streaming",
          partial: true,
        };
        state.tab = "report";
        renderAssignment();
        return;
      }
      if (event.type === "plan") {
        if (state.current.latest_audit) {
          state.current.latest_audit.plan = event.plan;
        }
        renderAssignment();
        return;
      }
      if (event.type === "done") {
        state.busy = false;
        state.stage = "";
        stopTimer();
        const seconds = (event.duration_ms / 1000).toFixed(1);
        toast(`Report ready in ${seconds}s. ${event.model_used ? "The local coach added the plan." : "Deterministic plan used."}`);
        return;
      }
      if (event.type === "error") {
        throw new Error(event.message || "The preflight failed.");
      }
    });
  } catch (error) {
    state.busy = false;
    state.stage = "";
    stopTimer();
    toast(error.message, "error");
  }

  if (!state.busy && state.current) {
    try {
      await openAssignment(state.current.id);
      state.tab = "report";
      renderAssignment();
      await refreshBootstrap();
    } catch (_) { /* keep the streamed report visible */ }
  }
}

async function readNDJSON(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

/* ── submit and delete ─────────────────────────────────────────────── */

async function submitAssignment() {
  const current = state.current;
  if (!current) return;
  if (!current.latest_audit) {
    toast("Run a preflight before marking this submitted.", "error");
    return;
  }
  if (!confirm(`Record version ${(current.submissions || []).length + 1} of “${current.title}” as submitted? This saves the current draft snapshot locally.`)) return;
  try {
    const data = await api("/api/submit", {
      method: "POST",
      body: JSON.stringify({ assignment_id: current.id }),
    });
    const patterns = (data.patterns || []).map((item) => item.text).join(" ");
    toast(patterns ? `Submission saved. ${patterns}` : "Submission saved. The companion will look for recurring gaps.");
    await openAssignment(current.id);
    state.tab = (data.patterns || []).length ? "memory" : "history";
    renderAssignment();
    await refreshBootstrap();
  } catch (error) { toast(error.message, "error"); }
}

async function deleteCurrent() {
  const current = state.current;
  if (!current) return;
  if (!confirm(`Forget “${current.title}” completely? This deletes the brief, reports, submissions, chat, and memory.`)) return;
  if (!confirm("This cannot be undone. Delete the local copy?")) return;
  try {
    await api(`/api/assignments/${current.id}/delete`, { method: "POST", body: "{}" });
    state.current = null;
    await refreshBootstrap();
    renderDashboard();
    toast("Assignment deleted.");
  } catch (error) { toast(error.message, "error"); }
}

/* ── modal, demo, global wiring ────────────────────────────────────── */

function openModal() {
  const backdrop = $("#modalBackdrop");
  if (!backdrop) return;
  backdrop.hidden = false;
  const title = $("#assignmentTitle");
  if (title) window.setTimeout(() => title.focus(), 30);
}

function closeModal() {
  const backdrop = $("#modalBackdrop");
  if (backdrop) backdrop.hidden = true;
  const error = $("#assignmentError");
  if (error) error.hidden = true;
}

async function loadDemo() {
  try {
    const data = await api("/api/demo", { method: "POST", body: "{}" });
    await refreshBootstrap();
    if (data.seeded) {
      toast("Demo loaded. Open the active assignment and run a preflight.");
    } else {
      toast("The desk already has assignments; demo not added.");
    }
    const active = state.assignments.find((assignment) => assignment.status === "active");
    if (active) await openAssignment(active.id);
    else if (state.assignments[0]) await openAssignment(state.assignments[0].id);
    else renderDashboard();
  } catch (error) { toast(error.message, "error"); }
}

function bindGlobal() {
  const newButton = $("#newAssignmentButton");
  if (newButton) newButton.onclick = openModal;
  const demo = $("#demoButton");
  if (demo) demo.onclick = loadDemo;
  const memory = $("#memoryButton");
  if (memory) memory.onclick = () => {
    if (!state.current) { toast("Open an assignment first."); return; }
    state.tab = "memory";
    renderAssignment();
  };
  const close = $("#modalClose");
  if (close) close.onclick = closeModal;
  const cancel = $("#modalCancel");
  if (cancel) cancel.onclick = closeModal;
  const backdrop = $("#modalBackdrop");
  if (backdrop) {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) closeModal();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  const form = $("#assignmentForm");
  if (form) {
    form.onsubmit = async (event) => {
      event.preventDefault();
      const error = $("#assignmentError");
      if (error) error.hidden = true;
      const title = $("#assignmentTitle").value.trim();
      const brief = $("#assignmentBrief").value.trim();
      if (!title || !brief) {
        if (error) { error.textContent = "A title and a brief are required."; error.hidden = false; }
        return;
      }
      const submit = form.querySelector('[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const data = await api("/api/assignments", {
          method: "POST",
          body: JSON.stringify({
            title,
            brief,
            deadline: $("#assignmentDeadline").value,
            course_code: $("#courseCode").value,
            course_title: $("#courseTitle").value,
            brief_source: "pasted",
          }),
        });
        closeModal();
        form.reset();
        await refreshBootstrap();
        await openAssignment(data.assignment.id);
        await runPreflight();
      } catch (err) {
        if (error) { error.textContent = err.message; error.hidden = false; }
      } finally {
        if (submit) submit.disabled = false;
      }
    };
  }
}

window.addEventListener("hashchange", () => {
  const match = location.hash.match(/^#a\/([a-f0-9]+)$/);
  if (match) {
    if (!state.current || state.current.id !== match[1]) {
      openAssignment(match[1]).catch(() => renderDashboard());
    }
  } else if (state.current) {
    renderDashboard();
  }
});

document.addEventListener("DOMContentLoaded", init);
