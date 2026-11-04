// Router UI: show the routes, run one request (routed vs always-strong), or
// route every sample to see the traffic mix. Helpers come from /static/util.js.

const ROUTE_CLASS = { simple: "ok", reasoning: "info", research: "warn", decline: "bad" };
const routeBadge = (r) => `<span class="badge ${ROUTE_CLASS[r] || ""}">${esc(r)}</span>`;

fetch("api/info").then((r) => r.json()).then((info) => {
  $("routes").innerHTML = info.routes.map((r) => `
    <div class="route" data-route="${esc(r.name)}">${routeBadge(r.name)}
      <div><strong>${esc(r.handler)}</strong> <span class="model">${esc(r.model)}</span></div>
      <div class="muted small">${esc(r.when)}</div></div>`).join("");
  $("router-info").textContent =
    `Router model: ${info.router_model}. Decisions below ${info.min_confidence} confidence escalate to a safer path.`;
  sampleChips("samples", info.samples, "question");
  if (info.mock) show("mock-badge");
});

$("run").addEventListener("click", runOne);
$("classify").addEventListener("click", classifyAll);

function busy(on) { $("run").disabled = $("classify").disabled = on; }
function showError(m) { $("error-card").textContent = m; show("error-card"); }

async function runOne() {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); return; }
  const compare = $("compare").checked;
  busy(true);
  ["error-card", "decision", "savings", "batch"].forEach(hide);
  document.querySelectorAll(".route").forEach((el) => el.classList.remove("picked"));
  for (const id of ["col-routed", "col-baseline"]) {
    const col = $(id);
    col.querySelector(".status-dot").className = "status-dot running";
    col.querySelector(".stats").textContent = "";
    col.querySelector(".answer").innerHTML = `<span class="spinner-note">Working…</span>`;
    col.querySelector(".trace").innerHTML = "";
    col.querySelector(".trace").classList.add("hidden");
  }
  $("col-baseline").classList.toggle("hidden", !compare);
  show("results");
  try {
    const done = await runJob("api/run", { question, compare }, onEvent);
    renderSavings(done);
  } catch (err) { showError(err.message); }
  busy(false);
}

function onEvent(ev) {
  if (ev.type === "route_decided") {
    document.querySelector(`.route[data-route="${ev.route}"]`)?.classList.add("picked");
    $("decision").innerHTML = `<strong>Router decided:</strong> ${routeBadge(ev.route)}
      ${ev.escalated_from ? `<span class="badge warn">escalated from ${esc(ev.escalated_from)} (low confidence)</span>` : ""}
      <span class="kv">confidence <span class="conf"><span style="width:${Math.round(ev.confidence * 100)}%"></span></span> ${ev.confidence.toFixed(2)}</span>
      <span class="kv">→ ${esc(ev.handler)}</span>
      <div class="muted small" style="flex-basis:100%">“${esc(ev.reason)}”</div>`;
    show("decision");
  } else if (ev.type === "tool_call") {
    const trace = $(ev.index === 0 ? "col-routed" : "col-baseline").querySelector(".trace");
    trace.classList.remove("hidden");
    let args = ev.args;
    try { args = Object.values(JSON.parse(ev.args)).join(" "); } catch {}
    trace.insertAdjacentHTML("beforeend", `<div class="trace-entry">&#128295; <code>${esc(ev.tool)}</code> ${esc(args)}</div>`);
  } else if (ev.type === "path_finished") {
    const col = $(ev.path === "routed" ? "col-routed" : "col-baseline");
    col.querySelector(".status-dot").className = `status-dot ${ev.error ? "fail" : "ok"}`;
    if (ev.usage) col.querySelector(".stats").textContent = usageText(ev.usage);
    col.querySelector(".answer").innerHTML = ev.error ? `<span class="bad-text">Error: ${esc(ev.error)}</span>` : mdLite(ev.answer);
  }
}

function renderSavings(done) {
  const a = done.routed, b = done.baseline;
  if (!b || a.error || b.error) return;
  const pct = (x, y) => (y ? Math.round((1 - x / y) * 100) : 0);
  const costLine = b.usage.cost ? `, <strong>${pct(a.usage.cost, b.usage.cost)}%</strong> of the dollar cost` : "";
  $("savings").innerHTML = `<strong>Routing saved</strong> <strong>${pct(a.usage.tokens, b.usage.tokens)}%</strong>
    of the tokens${costLine} and ${pct(a.usage.latency, b.usage.latency)}% of the time on this request
    (the router's own call included). <span class="muted">Negative = routing cost more. Same quality?
    Compare the answers — then measure it over many questions in the <a href="../eval/">Eval Harness</a>.</span>`;
  show("savings");
}

async function classifyAll() {
  busy(true);
  ["error-card", "decision", "results", "savings"].forEach(hide);
  $("batch-rows").innerHTML = "";
  $("batch-lesson").textContent = "";
  $("batch-stats").textContent = "routing…";
  show("batch");
  const rows = [];
  try {
    const done = await runJob("api/classify", {}, (ev) => {
      if (ev.type !== "classified") return;
      rows[ev.i] = ev;
      $("batch-rows").innerHTML = rows.filter(Boolean).map((r) => `<tr>
        <td>${esc(r.question)}</td>
        <td class="r">${routeBadge(r.route)}${r.escalated_from ? ` <span class="badge warn">↑ from ${esc(r.escalated_from)}</span>` : ""}</td>
        <td>${r.confidence.toFixed(2)}</td><td class="muted">${esc(r.reason)}</td></tr>`).join("");
    });
    const mix = Object.entries(done.mix).map(([r, n]) => `${n} ${r}`).join(" · ");
    $("batch-stats").textContent = `${mix} · router used ~${done.router_tokens} tokens total`;
    const cheap = (done.mix.simple || 0) + (done.mix.decline || 0);
    $("batch-lesson").textContent = `${cheap} of ${rows.length} requests never touch the strong model. ` +
      `If those were your real traffic mix, that share of spend is what a router saves — minus the router calls and any misroutes.`;
  } catch (err) { showError(err.message); }
  busy(false);
}
