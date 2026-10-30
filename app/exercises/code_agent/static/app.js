// Code-agent UI: start a run, then render each step (the model's note, the
// code it ran, and the output or traceback it got back) as a timeline.
// $, esc, mdLite, show, hide come from /static/util.js.


fetch("api/info").then((r) => r.json()).then((info) => {
  $("setup").innerHTML = [
    ["model", info.model],
    ["step budget", info.max_steps],
    ["per-run timeout", `${info.timeout}s`],
    ["memory", `${info.memory_mb} MB`],
  ].map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  $("files-summary").textContent = `Data files (${info.files.length})`;
  $("files").innerHTML = info.files
    .map((f) => `<p class="small"><code>${esc(f.name)}</code> — ${f.rows} lines</p>
      <pre class="out">${esc(f.preview)}\n…</pre>`).join("");
  $("samples").innerHTML = info.samples.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("");
  $("limits").textContent = "Each run is a fresh Python process; files persist, variables don't.";
  if (info.mock) show("mock-badge");
});

$("samples").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-q]");
  if (b) $("question").value = b.dataset.q;
});
$("run").addEventListener("click", startRun);

async function startRun() {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); return; }
  $("run").disabled = true;
  ["final-card", "error-card"].forEach(hide);
  $("timeline").innerHTML = `<p class="spinner-note" id="thinking">Model is thinking&hellip;</p>`;
  $("stats").textContent = "";
  show("timeline-section");

  let job;
  try {
    const form = new FormData();
    form.append("question", question);
    const res = await fetch("api/run", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    job = await res.json();
  } catch (err) {
    showError(`Failed to start: ${err.message}`);
    $("run").disabled = false;
    return;
  }

  const es = new EventSource(`api/stream/${job.job_id}`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "agent_note") addNote(ev);
    else if (ev.type === "code_run") addRun(ev);
    else if (ev.type === "done") { renderDone(ev); finish(es); }
    else if (ev.type === "error") { showError(ev.message); finish(es); }
  };
  es.onerror = () => { showError("Connection to server lost."); finish(es); };
}

function finish(es) { es.close(); $("run").disabled = false; $("thinking")?.remove(); }

// The note arrives just before its code run; park it until the run shows up.
let pendingNote = null;
function addNote(ev) { pendingNote = ev.text; }

function addRun(ev) {
  $("thinking")?.remove();
  const card = document.createElement("div");
  card.className = "card step";
  let badge, output = "";
  if (ev.invalid || ev.repeated) {
    badge = `<span class="badge warn">${ev.repeated ? "repeat blocked" : "invalid call"}</span>`;
    output = `<pre class="out">${esc(ev.message)}</pre>`;
  } else {
    badge = ev.timed_out ? `<span class="badge bad">timed out</span>`
      : `<span class="badge ${ev.exit_code === 0 ? "ok" : "bad"}">exit ${ev.exit_code}</span>`;
    if (ev.stdout) output += `<pre class="out">${esc(ev.stdout)}</pre>`;
    if (ev.stderr) output += `<pre class="out err">${esc(ev.stderr)}</pre>`;
    if (!output) output = `<pre class="out muted">(no output)</pre>`;
  }
  card.innerHTML = `
    <div class="step-head"><strong>Step ${ev.step}</strong> · run_python ${badge}
      <span class="meta">${ev.duration !== undefined ? `${ev.duration}s` : ""}</span></div>
    ${pendingNote ? `<div class="note">“${esc(pendingNote)}”</div>` : ""}
    <pre class="code">${esc(ev.code)}</pre>${output}`;
  pendingNote = null;
  $("timeline").append(card);
  $("timeline").insertAdjacentHTML("beforeend", `<p class="spinner-note" id="thinking">Model is reading the output&hellip;</p>`);
}

function renderDone(ev) {
  $("thinking")?.remove();
  const fixed = ev.failed_runs ? ` · ${ev.failed_runs} failed run${ev.failed_runs === 1 ? "" : "s"} along the way` : "";
  $("stats").textContent =
    `${ev.runs} code run${ev.runs === 1 ? "" : "s"}${fixed} · ${ev.latency}s · ${ev.calls} model calls · ~${ev.tokens} tokens`;
  $("final-byline").textContent = ev.finished_by === "step_limit" ? "— step budget ran out" : "";
  $("final-body").innerHTML = mdLite(ev.answer || "(no answer)");
  show("final-card");
}

function showError(message) {
  $("error-card").textContent = message;
  show("error-card");
}
