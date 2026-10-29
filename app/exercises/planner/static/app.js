// Planner UI: render each plan version as waves of step cards (steps in a
// wave run in parallel), update cards live, show replans and the final
// answer. Helpers come from /static/util.js.

let planCount = 0;

fetch("api/info").then((r) => r.json()).then((info) => {
  $("setup").innerHTML = [["planner", info.planner], ["workers", info.workers.join(", ")],
    ["max steps", info.max_steps], ["max replans", info.max_replans]]
    .map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  sampleChips("samples", info.samples, "goal");
  if (info.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

async function run() {
  const goal = $("goal").value.trim();
  if (!goal) { $("goal").focus(); return; }
  $("run").disabled = true;
  planCount = 0;
  $("plans").innerHTML = `<p class="spinner-note" id="planning">Planner is drafting the plan…</p>`;
  ["final-card", "error-card"].forEach(hide);
  try {
    const done = await runJob("api/run", { goal, inject_failure: $("inject").checked }, onEvent);
    renderDone(done);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("planning")?.remove();
  $("run").disabled = false;
}

// Group steps into waves exactly like the server does (deps on earlier waves).
function toWaves(steps, doneIds) {
  const finished = new Set(doneIds), out = [];
  let left = [...steps];
  while (left.length) {
    const ready = left.filter((s) => s.depends_on.every((d) => finished.has(d)));
    if (!ready.length) break;
    out.push(ready);
    ready.forEach((s) => finished.add(s.id));
    left = left.filter((s) => !finished.has(s.id));
  }
  return out;
}

function onEvent(ev) {
  if (ev.type === "plan_created") {
    $("planning")?.remove();
    planCount = ev.version;
    const card = document.createElement("div");
    card.className = "card plan";
    const waves = toWaves(ev.steps, ev.done_ids);
    card.innerHTML = `<h2>${ev.version === 1 ? "Plan" : `Revised plan (v${ev.version})`}
        <span class="badge info">${ev.steps.length} steps</span>
        <span class="badge">${waves.length} wave${waves.length === 1 ? "" : "s"}</span></h2>
      ${ev.done_ids.length ? `<div class="kept">Keeps finished work from: ${ev.done_ids.map(esc).join(", ")}</div>` : ""}
      ${waves.map((w, i) => `<div class="wave"><div class="wave-label">wave ${i + 1}${w.length > 1 ? " · parallel" : ""}</div>
        <div class="wave-steps">${w.map(stepHtml).join("")}</div></div>`).join("")}
      <div class="banners"></div>`;
    $("plans").append(card);
  } else if (ev.type === "plan_rejected") {
    $("plans").insertAdjacentHTML("beforeend", `<p class="notice small">Plan attempt ${ev.attempt} rejected by validation: ${esc(ev.reason)} — asking the planner to fix it.</p>`);
  } else if (ev.type === "step_started") {
    const el = stepEl(ev.id);
    if (!el) return;
    el.className = "step running";
    el.querySelector(".meta").textContent = `#${ev.number} · ${ev.model}`;
    el.dataset.number = ev.number;
  } else if (ev.type === "tool_call") {
    const el = document.querySelector(`.step[data-number="${ev.index}"]`);
    if (!el) return;
    let args = ev.args;
    try { args = Object.values(JSON.parse(ev.args)).join(" "); } catch {}
    el.querySelector(".trace").insertAdjacentHTML("beforeend", `<div class="trace-entry">&#128295; <code>${esc(ev.tool)}</code> ${esc(args)}</div>`);
  } else if (ev.type === "step_finished") {
    const el = stepEl(ev.id);
    if (!el) return;
    el.className = `step ${ev.ok ? "ok" : "failed"}`;
    el.querySelector(".meta").textContent += ` · ${ev.latency}s`;
    el.querySelector(".result").innerHTML = ev.ok
      ? `<details><summary>output</summary><div class="out">${esc(ev.output)}</div></details>`
      : `<div class="bad-text small">✗ ${esc(ev.error)}</div>`;
  } else if (ev.type === "replanning" || ev.type === "replan_skipped") {
    const last = [...document.querySelectorAll(".plan .banners")].pop();
    last?.insertAdjacentHTML("beforeend", ev.type === "replanning"
      ? `<p class="notice banner">Step <strong>${esc(ev.failed_id)}</strong> failed → asking the planner to replan the remaining work around it.</p>`
      : `<p class="notice banner">${esc(ev.reason)}</p>`);
  } else if (ev.type === "synthesizing") {
    $("plans").insertAdjacentHTML("beforeend", `<p class="spinner-note" id="planning">Synthesizer is writing the final answer…</p>`);
  }
}

const stepEl = (id) => [...document.querySelectorAll(`.step[data-id="${CSS.escape(id)}"]`)].pop();

function stepHtml(s) {
  return `<div class="step" data-id="${esc(s.id)}">
    <div class="head"><strong>${esc(s.id)}</strong>
      <span class="badge ${s.kind === "research" ? "warn" : "info"}">${esc(s.kind)}</span><span class="meta">queued</span></div>
    <div>${esc(s.task)}</div>
    ${s.depends_on.length ? `<div class="deps">needs: ${s.depends_on.map(esc).join(", ")}</div>` : ""}
    <div class="trace"></div><div class="result"></div></div>`;
}

function renderDone(d) {
  $("planning")?.remove();
  $("final-body").innerHTML = mdLite(d.answer);
  $("stats").textContent = usageText(d.usage);
  $("lesson").innerHTML = `${d.steps_run} steps run from ${planCount} plan${planCount === 1 ? "" : "s"}
    (${d.replans} replan${d.replans === 1 ? "" : "s"}${d.failed.length ? `; failed: ${d.failed.map(esc).join(", ")}` : ""}).
    <span class="muted">All routing decisions were made in ${planCount} planning call${planCount === 1 ? "" : "s"} —
    the <a href="../supervisor/">Supervisor</a> makes one per turn instead. Same goal there: compare calls and time.</span>`;
  show("final-card");
}
