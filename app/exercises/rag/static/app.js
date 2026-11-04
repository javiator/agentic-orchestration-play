// RAG UI: run one question through plain and agentic RAG, render each
// variant's retrieval steps side by side, then compare answers and cost.
// $, esc, mdLite, show, hide come from /static/util.js.

const VARIANTS = ["plain", "agentic"];
const CITE_RE = /\[([\w-]+#\d+)\]/g;

fetch("api/info").then((r) => r.json()).then((info) => {
  $("setup").innerHTML = [
    ["model (both)", info.model],
    ["search", info.backend],
    ["top-k", info.top_k],
    ["agent budget", `${info.max_steps} steps`],
    ["grounding retries", info.verify_rounds],
  ].map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  $("docs-summary").textContent = `Documents (${info.docs.length} docs, ${info.chunks} chunks)`;
  $("doc-list").innerHTML = info.docs
    .map((d) => `<button data-doc="${esc(d.id)}" title="${esc(d.id)}">${esc(d.title)}</button>`).join("");
  $("samples").innerHTML = info.samples
    .map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("");
  if (info.mock) show("mock-badge");
});

$("samples").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-q]");
  if (b) $("question").value = b.dataset.q;
});
// Any element with data-doc (doc buttons, citation chips) opens the document.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-doc]");
  if (el) openDoc(el.dataset.doc);
});
$("doc-close").addEventListener("click", () => hide("doc-view"));
$("run").addEventListener("click", startRun);

async function openDoc(id) {
  const res = await fetch(`api/doc/${encodeURIComponent(id)}`);
  if (!res.ok) return;
  const doc = await res.json();
  $("doc-title").textContent = doc.id;
  $("doc-text").textContent = doc.text;
  show("doc-view");
  $("doc-view").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function startRun() {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); return; }
  $("run").disabled = true;
  ["error-card", "compare"].forEach(hide);
  for (const v of VARIANTS) resetVariant(v);
  show("variants");

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
    if (ev.type === "retrieval" || ev.type === "agent_step") addStep(ev);
    else if (ev.type === "grounding") addGrounding(ev);
    else if (ev.type === "variant_finished") finishVariant(ev);
    else if (ev.type === "done") { renderCompare(ev.results); finish(es); }
    else if (ev.type === "error") { showError(ev.message); finish(es); }
  };
  es.onerror = () => { showError("Connection to server lost."); finish(es); };
}

function finish(es) { es.close(); $("run").disabled = false; }

// ── Variant columns ─────────────────────────────────────────────────────────

const box = (v) => $(`v-${v}`);

function resetVariant(v) {
  const el = box(v);
  el.classList.remove("failed");
  el.querySelector(".status-dot").className = "status-dot running";
  el.querySelector(".stats").textContent = "";
  el.querySelector(".steps").innerHTML = `<p class="spinner-note">Working&hellip;</p>`;
  el.querySelector(".answer").innerHTML = "";
}

function stepsOf(v) {
  const steps = box(v).querySelector(".steps");
  steps.querySelector(".spinner-note")?.remove();
  return steps;
}

function hitsHtml(hits) {
  if (!hits.length) return `<div class="muted small">no matching passages</div>`;
  return `<details><summary>${hits.length} passages: ${hits.map((h) => esc(h.id)).join(", ")}</summary>
    ${hits.map((h) => `<div class="hit"><span class="cite" data-doc="${esc(h.doc_id)}">${esc(h.id)}</span>
      <span class="meta">${esc(h.title)}${h.section ? " › " + esc(h.section) : ""} · score ${h.score}</span>
      <div>${esc(h.snippet)}</div></div>`).join("")}</details>`;
}

function addStep(ev) {
  const el = document.createElement("div");
  el.className = "step";
  let body;
  if (ev.tool === "read_doc") {
    body = ev.found
      ? `&#128196; read <code>${esc(ev.doc_id)}</code> — ${esc(ev.title)}`
      : `&#128196; read <code>${esc(ev.doc_id)}</code> — not found`;
  } else if (ev.repeated) {
    el.classList.add("warn");
    body = `&#9888; repeated search <code>${esc(ev.query)}</code> — blocked by the guard`;
  } else {
    body = `&#128269; search <code>${esc(ev.query)}</code>${hitsHtml(ev.hits || [])}`;
  }
  el.innerHTML = `<div class="head"><span class="n">step ${ev.step}</span><div>${body}</div></div>`;
  stepsOf(ev.variant).append(el);
}

function addGrounding(ev) {
  const el = document.createElement("div");
  el.className = `step ${ev.supported ? "good" : "warn"}`;
  el.innerHTML = `<div class="head"><span class="n">step ${ev.step}</span><div>
    ${ev.supported ? "&#10004; grounding check passed" : "&#10008; grounding check failed — searching more"}
    ${ev.missing ? `<div class="small">${esc(ev.missing)}</div>` : ""}</div></div>`;
  stepsOf(ev.variant).append(el);
}

function renderAnswer(text) {
  // mdLite escapes first; citation ids only contain [\w-#], safe to wrap.
  return mdLite(text).replace(CITE_RE, (_, id) =>
    `<span class="cite" data-doc="${id.split("#")[0]}" title="open ${id.split("#")[0]}">${id}</span>`);
}

function finishVariant(ev) {
  const el = box(ev.variant);
  stepsOf(ev.variant);
  el.querySelector(".status-dot").className = `status-dot ${ev.error ? "fail" : "ok"}`;
  el.querySelector(".stats").textContent =
    `${ev.latency}s · ${ev.calls} call${ev.calls === 1 ? "" : "s"} · ~${ev.tokens} tokens`;
  const answer = el.querySelector(".answer");
  if (ev.error) {
    el.classList.add("failed");
    answer.textContent = `Error: ${ev.error}`;
    return;
  }
  const limit = ev.finished_by === "step_limit"
    ? `<div class="step warn">&#9888; step budget ran out before a clean answer</div>` : "";
  answer.innerHTML = limit + renderAnswer(ev.answer);
}

function renderCompare(results) {
  const p = results.plain, a = results.agentic;
  if (!p || !a || p.error || a.error) return;
  const ratio = (x, y) => (y ? (x / y).toFixed(1) : "—");
  $("compare").innerHTML = `<strong>Cost of agency:</strong> agentic RAG used
    <strong>${ratio(a.calls, p.calls)}×</strong> the model calls,
    <strong>${ratio(a.tokens, p.tokens)}×</strong> the tokens and
    <strong>${ratio(a.latency, p.latency)}×</strong> the time
    (${a.searches} search${a.searches === 1 ? "" : "es"} vs 1).
    <span class="muted">Was the answer better enough to pay for it? Run the
    <a href="../eval/">Eval Harness</a> on the "RAG" question set to measure it over many questions.</span>`;
  show("compare");
}

function showError(message) {
  $("error-card").textContent = message;
  show("error-card");
}
