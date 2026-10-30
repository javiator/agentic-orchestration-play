// UI driver: submit the query as multipart form data (including feature
// toggles + session id), then consume the SSE stream and update the page
// live: per-model panels, tool-call research traces, rebuttal round, judge
// verdicts, recalled memories.
//
// API URLs are relative ("api/...") so they resolve under this exercise's
// mount point (/consensus/), wherever the root app puts it.
// $, esc, mdLite, show, hide come from /static/util.js.

let selectedFiles = [];
let sessionId = "";       // set after the first run; sent back for follow-ups
let sessionTurns = [];    // [{query, merged}] rendered in the history card

// Show configured models before the first query so the page explains itself.
fetch("api/models").then((r) => r.json()).then(({ models, judge }) => {
  $("model-count").textContent = String(models.length);
  $("judge-label").textContent = `Judge: ${judge}`;
});

$("files").addEventListener("change", (e) => {
  selectedFiles = Array.from(e.target.files);
  $("file-list").innerHTML = selectedFiles
    .map((f) => `<li>${esc(f.name)} (${(f.size / 1024).toFixed(1)} KB)</li>`)
    .join("");
});

$("submit").addEventListener("click", runQuery);
$("new-session").addEventListener("click", resetSession);

function resetSession() {
  sessionId = "";
  sessionTurns = [];
  hide("session-banner"); hide("history-card");
  $("query-label").textContent = "Research question";
  $("query").value = "";
}

async function runQuery() {
  const query = $("query").value.trim();
  if (!query) { $("query").focus(); return; }

  $("submit").disabled = true;
  ["merged-card", "disagreements-card", "error-card", "rebuttal-card", "memory-card"]
    .forEach(hide);
  hide("timing-badge");

  const form = new FormData();
  form.append("query", query);
  form.append("context_text", $("context").value);
  form.append("use_tools", $("opt-tools").checked);
  form.append("rebuttal", $("opt-rebuttal").checked);
  form.append("use_memory", $("opt-memory").checked);
  form.append("session_id", sessionId);
  for (const f of selectedFiles) form.append("files", f);

  let job;
  try {
    const res = await fetch("api/query", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    job = await res.json();
  } catch (err) {
    showError(`Failed to submit query: ${err.message}`);
    $("submit").disabled = false;
    return;
  }

  sessionId = job.session_id;
  renderSessionBanner(job.turn);
  renderIncluded(query, $("context").value, job.attached_files, job.options);
  renderAgentPanels(job.models);
  show("agents-section");

  // SSE: one event per orchestration milestone; close on done/error.
  const es = new EventSource(
    `api/stream/${job.job_id}?session_id=${sessionId}&query=${encodeURIComponent(query)}`
  );
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "memory_recalled") renderMemories(ev.items);
    else if (ev.type === "tool_call") addTraceEntry(ev);
    else if (ev.type === "agent_finished") updateAgentPanel(ev);
    else if (ev.type === "agent_started" && ev.phase === "rebuttal") setPanelRunning(ev.index, "revising…");
    else if (ev.type === "fanout_complete") showTiming(ev.timing);
    else if (ev.type === "rebuttal_started") showRebuttal(ev.disagreement_count);
    else if (ev.type === "judge_started") setJudgeRunning(ev.model, ev.round);
    else if (ev.type === "done") { renderDone(ev, query); finish(es); }
    else if (ev.type === "error") { showError(ev.message); if (ev.results) ev.results.forEach(updateAgentPanel); finish(es); }
  };
  es.onerror = () => { showError("Connection to server lost."); finish(es); };
}

function finish(es) { es.close(); $("submit").disabled = false; }

// ── Session (multi-turn) ────────────────────────────────────────────────────

function renderSessionBanner(turn) {
  $("session-label").textContent = `Session turn ${turn} — follow-up questions will see earlier answers`;
  show("session-banner");
  $("query-label").textContent = turn > 1 ? "Follow-up question" : "Research question";
  if (sessionTurns.length) {
    $("history-body").innerHTML = sessionTurns
      .map(
        (t, i) => `<details class="turn"><summary>Turn ${i + 1}: ${esc(t.query)}</summary>
          <div class="markdown">${mdLite(t.merged)}</div></details>`
      )
      .join("");
    show("history-card");
  }
}

// ── Long-term memory ────────────────────────────────────────────────────────

function renderMemories(items) {
  if (!items || items.length === 0) {
    $("memory-body").innerHTML = `<p class="muted">No related past research found.</p>`;
  } else {
    $("memory-body").innerHTML = items
      .map(
        (m) => `<div class="memory-item">
          <span class="tag">${esc(m.date)}</span> <strong>${esc(m.query)}</strong>
          <div class="muted">${esc(m.summary)}</div>
          ${m.disagreement_topics.length
            ? `<div class="muted">Models disagreed on: ${esc(m.disagreement_topics.join(", "))}</div>`
            : ""}
        </div>`
      )
      .join("");
  }
  show("memory-card");
}

// ── Run setup ───────────────────────────────────────────────────────────────

function renderIncluded(query, context, files, options) {
  let html = `<p class="included-item"><span class="tag">query</span>${esc(query)}</p>`;
  if (context.trim())
    html += `<p class="included-item"><span class="tag">context</span>${esc(context.trim())}</p>`;
  for (const f of files)
    html += `<p class="included-item"><span class="tag">file</span>${esc(f.name)} — ${f.chars} chars parsed</p>`;
  const on = Object.entries(options).filter(([, v]) => v).map(([k]) => k.replace("use_", ""));
  html += `<p class="included-item"><span class="tag">features</span>${on.length ? esc(on.join(", ")) : "none (base pipeline)"}</p>`;
  $("included-body").innerHTML = html;
  show("included-card");
}

function renderAgentPanels(models) {
  $("agents-grid").innerHTML = models
    .map(
      (m, i) => `
      <div class="agent" id="agent-${i}">
        <h3><span class="status-dot running"></span>${esc(m)}<span class="latency"></span></h3>
        <div class="trace hidden" id="trace-${i}"></div>
        <div class="body spinner-note">Researching&hellip;</div>
      </div>`
    )
    .join("");
}

// ── Tool-use research trace ─────────────────────────────────────────────────

function addTraceEntry(ev) {
  const trace = $(`trace-${ev.index}`);
  if (!trace) return;
  show(`trace-${ev.index}`);
  let args = ev.args;
  try { args = Object.values(JSON.parse(ev.args)).join(" "); } catch {}
  trace.insertAdjacentHTML(
    "beforeend",
    `<div class="trace-entry">&#128295; <code>${esc(ev.tool)}</code> ${esc(args)}</div>`
  );
}

// ── Agent panels ────────────────────────────────────────────────────────────

function setPanelRunning(index, note) {
  const panel = $(`agent-${index}`);
  if (!panel) return;
  const dot = panel.querySelector(".status-dot");
  dot.classList.remove("ok", "fail");
  dot.classList.add("running");
  const body = panel.querySelector(".body");
  body.classList.add("spinner-note");
  body.textContent = note;
}

function updateAgentPanel(r) {
  const panel = $(`agent-${r.index}`);
  if (!panel) return;
  const dot = panel.querySelector(".status-dot");
  dot.classList.remove("running");
  dot.classList.add(r.ok ? "ok" : "fail");
  panel.querySelector(".latency").textContent = `${r.latency}s`;
  const body = panel.querySelector(".body");
  body.classList.remove("spinner-note");
  if (r.ok) {
    body.textContent = r.answer;
    if (r.phase === "rebuttal") {
      panel.querySelector("h3").insertAdjacentHTML(
        "beforeend", `<span class="revised-badge">revised</span>`
      );
    }
  } else {
    panel.classList.add("failed");
    body.textContent = `Failed: ${r.error}`;
  }
}

function showTiming(t) {
  const b = $("timing-badge");
  b.textContent = `parallel: ${t.parallel_wall_seconds}s · sequential would be ~${t.sequential_estimate_seconds}s · saved ${t.seconds_saved}s`;
  show("timing-badge");
}

// ── Rebuttal round ──────────────────────────────────────────────────────────

function showRebuttal(count) {
  $("rebuttal-body").innerHTML =
    `<p>Judge found <strong>${count}</strong> disagreement${count === 1 ? "" : "s"} — ` +
    `agents are reviewing each other's positions and revising&hellip;</p>`;
  show("rebuttal-card");
}

// ── Judge / verdict ─────────────────────────────────────────────────────────

function setJudgeRunning(model, round) {
  const label = round === 2 ? `re-judging revised answers with ${model}…` : `judging with ${model}…`;
  $("judge-byline").textContent = `— ${label}`;
  $("merged-body").innerHTML = `<span class="spinner-note">Judge is comparing and merging answers&hellip;</span>`;
  show("merged-card");
}

function renderDone(ev, query) {
  const v = ev.verdict;
  const roundNote = v.round === 2 ? " after rebuttal round" : "";
  $("judge-byline").textContent =
    `— synthesized by ${v.judge_model} in ${v.judge_latency}s${roundNote}`;
  $("agreement-summary").textContent = v.agreement_summary || "";
  $("merged-body").innerHTML = mdLite(v.merged_answer || "");
  if (v.omissions) { $("omissions").textContent = `Omissions: ${v.omissions}`; show("omissions"); }
  else hide("omissions");
  show("merged-card");

  // With a rebuttal round: summarize what changed between round 1 and 2.
  if (ev.initial_verdict) {
    const before = ev.initial_verdict.disagreements.length;
    const after = (v.disagreements || []).length;
    $("rebuttal-body").innerHTML =
      `<p>Round 1: <strong>${before}</strong> disagreement${before === 1 ? "" : "s"} → ` +
      `after rebuttal: <strong>${after}</strong> remaining.</p>` +
      `<details><summary>Round-1 disagreements (before rebuttal)</summary>
        ${disagreementHtml(ev.initial_verdict.disagreements)}</details>`;
    show("rebuttal-card");
  }

  $("disagreements-title").innerHTML = v.round === 2
    ? "&#9888; Disagreements remaining after rebuttal"
    : "&#9888; Disagreements between models";
  const d = v.disagreements || [];
  $("disagreements-body").innerHTML = d.length === 0
    ? `<p class="no-disagreements">No substantive disagreements — the models agreed.</p>`
    : disagreementHtml(d, true);
  show("disagreements-card");

  sessionTurns.push({ query, merged: v.merged_answer || "" });
  $("query").value = "";
}

function disagreementHtml(items, openFirst = false) {
  return items
    .map(
      (item, i) => `
      <details class="disagreement" ${openFirst && i === 0 ? "open" : ""}>
        <summary>${esc(item.topic)}</summary>
        ${(item.positions || [])
          .map(
            (p) => `<div class="position">
              <div class="model-name">${esc(p.model)}</div>
              <div class="claim">${esc(p.claim)}</div>
            </div>`
          )
          .join("")}
      </details>`
    )
    .join("");
}

function showError(message) {
  $("error-card").textContent = message;
  show("error-card");
}
