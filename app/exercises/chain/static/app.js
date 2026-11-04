// Chain UI: run single prompt vs prompt chain on one topic; show each
// strategy's steps live, then the code-checked spec results side by side.
// Helpers come from /static/util.js.

fetch("api/info").then((r) => r.json()).then((info) => {
  $("spec").textContent = info.spec;
  $("setup").textContent = `Model (both strategies): ${info.model} · chain fix rounds: ${info.fix_rounds}. ` +
    "Checks are plain code, not a model — free, instant and deterministic.";
  sampleChips("samples", info.samples, "topic");
  if (info.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

const KIND = { llm: '<span class="badge info">LLM</span>', code: '<span class="badge">code</span>',
               gate: '<span class="badge warn">gate</span>' };

async function run() {
  const topic = $("topic").value.trim();
  if (!topic) { $("topic").focus(); return; }
  $("run").disabled = true;
  ["error-card", "verdict"].forEach(hide);
  for (const v of ["single", "chain"]) {
    const el = $(`v-${v}`);
    el.querySelector(".status-dot").className = "status-dot running";
    for (const c of [".stats", ".steps", ".checks", ".output"]) el.querySelector(c).innerHTML = "";
  }
  show("results");
  try {
    const done = await runJob("api/run", { topic }, onEvent);
    renderVerdict(done.results);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("run").disabled = false;
}

function onEvent(ev) {
  if (ev.type === "step") {
    const steps = $(`v-${ev.variant}`).querySelector(".steps");
    const key = CSS.escape(ev.name);
    let row = steps.querySelector(`[data-name="${key}"]`);
    if (!row) {
      row = document.createElement("div");
      row.className = "step-row";
      row.dataset.name = ev.name;
      steps.append(row);
    }
    const status = ev.status === "running" ? `<span class="status-dot running"></span>`
      : ev.status === "ok" ? "✓" : `<span class="bad-text">✗</span>`;
    row.innerHTML = `<span class="kind">${KIND[ev.kind] || ""}</span><span>${status}</span>
      <span>${esc(ev.name)} <span class="detail">${esc(ev.detail || "")}</span></span>`;
  } else if (ev.type === "variant_finished") {
    const el = $(`v-${ev.variant}`);
    el.querySelector(".status-dot").className = `status-dot ${ev.error ? "fail" : "ok"}`;
    if (ev.error) { el.querySelector(".output").innerHTML = `<p class="bad-text">Error: ${esc(ev.error)}</p>`; return; }
    el.querySelector(".stats").textContent = usageText(ev.usage);
    el.querySelector(".checks").innerHTML = `<span class="score">${ev.passed}/${ev.total} checks</span>` +
      ev.checks.map((c) => `<span class="badge ${c.ok ? "ok" : "bad"}" title="${esc(c.detail)}">${c.ok ? "✓" : "✗"} ${esc(c.name)}</span>`).join("");
    el.querySelector(".output").innerHTML = `<details open><summary class="small muted">output</summary><pre class="box">${esc(ev.text)}</pre></details>
      <ul class="small muted">${ev.checks.filter((c) => !c.ok).map((c) => `<li>${esc(c.name)}: ${esc(c.detail)}</li>`).join("")}</ul>`;
  }
}

function renderVerdict(r) {
  const s = r.single, c = r.chain;
  if (!s || !c || s.error || c.error) return;
  const ratio = (a, b) => (b ? (a / b).toFixed(1) : "—");
  let lesson;
  if (c.passed > s.passed) lesson = "The chain met more of the spec — small focused steps plus code gates catch what one big prompt misses.";
  else if (c.passed === s.passed) lesson = "Same compliance: for this model and task, one prompt was enough — the chain's extra calls bought nothing. Try a weaker model or a stricter spec.";
  else lesson = "The single prompt did better this time — chains can lose coherence when parts are written separately.";
  $("verdict").innerHTML = `<h3>Result: single ${s.passed}/${s.total} vs chain ${c.passed}/${c.total} checks</h3>
    ${esc(lesson)}<br><span class="muted">The chain used ${ratio(c.usage.calls, s.usage.calls)}× the calls,
    ${ratio(c.usage.tokens, s.usage.tokens)}× the tokens and ${ratio(c.usage.latency, s.usage.latency)}× the time.</span>`;
  show("verdict");
}
