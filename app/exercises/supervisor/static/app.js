// Supervisor UI: start a run, then render the SSE event stream as a timeline.
// Each supervisor turn is a block; the specialists it delegated to in that
// turn sit side by side inside it (side by side = ran in parallel).
// $, esc, mdLite, show, hide come from /static/util.js.

let currentTurn = null;  // DOM node of the latest turn block

fetch("api/info").then((r) => r.json()).then((info) => {
  $("roster").innerHTML =
    `<div class="member supervisor"><strong>supervisor</strong><span class="model">${esc(info.supervisor)}</span>
       <div class="muted">Decides who acts next and when to finish.</div></div>` +
    info.specialists
      .map(
        (s) => `<div class="member"><strong>${esc(s.name)}</strong><span class="model">${esc(s.model)}</span>
          ${s.tools ? `<span class="chip">web tools</span>` : ""}
          <div class="muted">${esc(s.role)}</div></div>`
      )
      .join("");
  $("limits").textContent = `max ${info.max_turns} supervisor turns`;
  if (info.mock) show("mock-badge");
});

$("run").addEventListener("click", startRun);

async function startRun() {
  const goal = $("goal").value.trim();
  if (!goal) { $("goal").focus(); return; }
  $("run").disabled = true;
  ["final-card", "error-card"].forEach(hide);
  $("timeline").innerHTML = "";
  $("stats").textContent = "";
  currentTurn = null;

  let job;
  try {
    const form = new FormData();
    form.append("goal", goal);
    const res = await fetch("api/run", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    job = await res.json();
  } catch (err) {
    showError(`Failed to start: ${err.message}`);
    $("run").disabled = false;
    return;
  }
  show("timeline-section");

  const es = new EventSource(`api/stream/${job.job_id}`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "supervisor_thinking") addTurn(ev);
    else if (ev.type === "supervisor_note") setNote(ev.text);
    else if (ev.type === "delegated") addStep(ev);
    else if (ev.type === "tool_call") addTrace(ev);
    else if (ev.type === "specialist_finished") finishStep(ev);
    else if (ev.type === "guard") addGuard(ev.message);
    else if (ev.type === "done") { renderDone(ev); finish(es); }
    else if (ev.type === "error") { showError(ev.message); finish(es); }
  };
  es.onerror = () => { showError("Connection to server lost."); finish(es); };
}

function finish(es) {
  es.close();
  $("run").disabled = false;
  if (currentTurn) currentTurn.querySelector(".turn-head .status-dot").classList.remove("running");
}

// ── Timeline ────────────────────────────────────────────────────────────────

function addTurn(ev) {
  if (currentTurn) currentTurn.querySelector(".turn-head .status-dot").classList.remove("running");
  currentTurn = document.createElement("div");
  currentTurn.className = "turn card";
  currentTurn.innerHTML = `
    <div class="turn-head"><span class="status-dot running"></span>
      <strong>Turn ${ev.turn}</strong> · <span class="turn-status">supervisor
      ${ev.last ? "deciding — final turn, must finish" : "deciding…"}</span></div>
    <div class="note muted hidden"></div>
    <div class="steps"></div>`;
  $("timeline").append(currentTurn);
}

function setNote(text) {
  if (!currentTurn) return;
  const note = currentTurn.querySelector(".note");
  note.textContent = `“${text}”`;
  note.classList.remove("hidden");
}

function addStep(ev) {
  const card = document.createElement("div");
  card.className = "step";
  card.id = `step-${ev.step}`;
  card.innerHTML = `
    <h3><span class="status-dot running"></span>Step ${ev.step} · ${esc(ev.specialist)}
      <span class="model">${esc(ev.model)}</span><span class="latency"></span></h3>
    <div class="task"><span class="tag">task</span>${esc(ev.task)}</div>
    <div class="trace hidden"></div>
    <div class="body spinner-note">Working&hellip;</div>`;
  card.dataset.specialist = ev.specialist;
  currentTurn.querySelector(".steps").append(card);
  const who = [...currentTurn.querySelectorAll(".step")].map((s) => s.dataset.specialist);
  setTurnStatus(`supervisor delegated to ${who.join(" + ")}`);
}

function setTurnStatus(text) {
  if (currentTurn) currentTurn.querySelector(".turn-status").textContent = text;
}

function addTrace(ev) {
  const card = $(`step-${ev.index}`);
  if (!card) return;
  const trace = card.querySelector(".trace");
  trace.classList.remove("hidden");
  let args = ev.args;
  try { args = Object.values(JSON.parse(ev.args)).join(" "); } catch {}
  trace.insertAdjacentHTML(
    "beforeend",
    `<div class="trace-entry">&#128295; <code>${esc(ev.tool)}</code> ${esc(args)}</div>`
  );
}

function finishStep(ev) {
  const card = $(`step-${ev.step}`);
  if (!card) return;
  const dot = card.querySelector(".status-dot");
  dot.classList.remove("running");
  dot.classList.add(ev.ok ? "ok" : "fail");
  card.querySelector(".latency").textContent = `${ev.latency}s`;
  const body = card.querySelector(".body");
  body.classList.remove("spinner-note");
  if (ev.ok) {
    body.innerHTML = `<details><summary>Output (${ev.output.length} chars)</summary>
      <div class="markdown">${mdLite(ev.output)}</div></details>`;
  } else {
    card.classList.add("failed");
    body.textContent = `Failed: ${ev.error}`;
  }
}

function addGuard(message) {
  const el = document.createElement("div");
  el.className = "guard";
  el.textContent = `⚠ Guard: ${message}`;
  if (!currentTurn) { $("timeline").append(el); return; }
  currentTurn.querySelector(".steps").append(el);
  if (!currentTurn.querySelector(".step")) setTurnStatus("supervisor's call was refused by a guard");
}

function renderDone(ev) {
  const how = { finish: "supervisor called finish", text: "supervisor answered directly",
                turn_limit: "turn limit reached — latest work used" }[ev.finished_by] || ev.finished_by;
  $("final-byline").textContent = `— ${how}`;
  if (ev.finished_by !== "turn_limit") setTurnStatus(`supervisor ${how.replace("supervisor ", "")}`);
  $("final-body").innerHTML = mdLite(ev.final_answer || "");
  $("stats").textContent =
    `${ev.turns} turn${ev.turns === 1 ? "" : "s"} · ${ev.delegations} delegation${ev.delegations === 1 ? "" : "s"} · ${ev.elapsed}s`;
  show("final-card");
}

function showError(message) {
  $("error-card").textContent = message;
  show("error-card");
}
