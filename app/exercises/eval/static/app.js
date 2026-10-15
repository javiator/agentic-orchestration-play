// Eval UI: pick a question set + targets, stream per-question results into a
// grid (rows = questions, columns = targets), then show the summary table.
// $, esc, mdLite, show, hide come from /static/util.js.

let datasets = [];
let cells = {};  // "target|qid" → result row, for the detail panel
let labels = {};  // target id → display label

fetch("api/options").then((r) => r.json()).then((opts) => {
  datasets = opts.datasets;
  $("dataset").innerHTML = datasets
    .map((d) => `<option value="${esc(d.id)}">${esc(d.name)} (${d.count} questions)</option>`)
    .join("");
  // Default selection: the first single model plus every exercise — the
  // comparison that answers "does orchestration beat one model?"
  $("targets").innerHTML = opts.targets
    .map((t, i) => {
      const on = i === 0 || t.id.startsWith("exercise:");
      return `<label class="toggle"><input type="checkbox" value="${esc(t.id)}" ${on ? "checked" : ""}>
        <span>${esc(t.label)}</span></label>`;
    })
    .join("");
  $("run-info").textContent = `grader: ${opts.grader} · ${opts.concurrency} at a time`;
  if (opts.mock) show("mock-badge");
  describeDataset();
});
$("dataset").addEventListener("change", describeDataset);
$("run").addEventListener("click", startEval);
loadHistory();

function describeDataset() {
  const d = datasets.find((x) => x.id === $("dataset").value);
  $("dataset-desc").textContent = d ? d.description : "";
}

async function startEval() {
  const targets = [...document.querySelectorAll("#targets input:checked")].map((i) => i.value);
  if (!targets.length) { showError("Select at least one target."); return; }
  $("run").disabled = true;
  ["summary-card", "error-card", "detail"].forEach(hide);
  cells = {};

  let job;
  try {
    const form = new FormData();
    form.append("dataset", $("dataset").value);
    targets.forEach((t) => form.append("targets", t));
    const res = await fetch("api/run", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    job = await res.json();
  } catch (err) {
    showError(`Failed to start: ${err.message}`);
    $("run").disabled = false;
    return;
  }

  let total = 0, finished = 0;
  const es = new EventSource(`api/stream/${job.job_id}`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "eval_started") {
      total = ev.targets.length * ev.questions.length;
      buildGrid(ev);
      $("summary-byline").textContent = `— ${ev.dataset} · graded by ${ev.grader}`;
    } else if (ev.type === "item_started") {
      setCell(ev.target, ev.qid, `<span class="status-dot running"></span>`);
    } else if (ev.type === "item_finished") {
      cells[`${ev.target}|${ev.qid}`] = ev;
      setCell(ev.target, ev.qid, cellHtml(ev));
      $("progress").textContent = `— ${++finished}/${total} done`;
    } else if (ev.type === "done") {
      renderSummary(ev.summaries);
      loadHistory();
      finish(es);
    } else if (ev.type === "error") {
      showError(ev.message);
      finish(es);
    }
  };
  es.onerror = () => { showError("Connection to server lost."); finish(es); };
}

function finish(es) { es.close(); $("run").disabled = false; }

// ── Live grid ───────────────────────────────────────────────────────────────

function buildGrid(ev) {
  labels = Object.fromEntries(ev.targets.map((t) => [t.id, t.label]));
  const head = `<tr><th>Question</th>${ev.targets.map((t) => `<th>${esc(t.label)}</th>`).join("")}</tr>`;
  const rows = ev.questions
    .map(
      (q) => `<tr><td class="q"><code>${esc(q.id)}</code> ${esc(q.question)}
          ${q.checkable ? `<span class="chip">checkable</span>` : ""}</td>
        ${ev.targets.map((t) => `<td class="cell" data-key="${esc(t.id)}|${esc(q.id)}"><span class="muted">queued</span></td>`).join("")}</tr>`
    )
    .join("");
  $("grid").innerHTML = head + rows;
  $("progress").textContent = `— 0/${ev.targets.length * ev.questions.length} done`;
  show("grid-card");
}

function setCell(target, qid, html) {
  const cell = document.querySelector(`#grid td[data-key="${CSS.escape(`${target}|${qid}`)}"]`);
  if (cell) cell.innerHTML = html;
}

function cellHtml(r) {
  if (r.error) return `<span class="badge bad">error</span>`;
  const match = r.match === null ? "" : `<span class="badge ${r.match ? "good" : "bad"}">${r.match ? "✓" : "✗"}</span>`;
  const grade = r.grade === null ? `<span class="badge">?</span>`
    : `<span class="badge g${r.grade}">${r.grade}/5</span>`;
  return `${match}${grade}<div class="muted small">${r.latency}s · ${r.calls} call${r.calls === 1 ? "" : "s"}</div>`;
}

$("grid").addEventListener("click", (e) => {
  const cell = e.target.closest("td.cell");
  const r = cell && cells[cell.dataset.key];
  if (!r) return;
  $("detail").innerHTML = `
    <h3>${esc(r.qid)} · ${esc(labels[r.target] || r.target)}</h3>
    ${r.error ? `<p class="bad-text">Error: ${esc(r.error)}</p>`
      : `<p><strong>Grade:</strong> ${r.grade ?? "?"}/5 — ${esc(r.reason)}</p>
         ${r.match === null ? "" : `<p><strong>Exact match:</strong> ${r.match ? "yes" : "no"}</p>`}
         <div class="markdown answer">${mdLite(r.answer)}</div>`}
    <p class="muted small">${r.latency}s · ${r.calls} model calls · ~${r.tokens} tokens</p>`;
  show("detail");
});

// ── Summary + history ───────────────────────────────────────────────────────

// Which direction is "better" for each column, for best-value highlighting.
const COLUMNS = [
  ["match_accuracy", "Match", (v) => (v === null ? "—" : `${Math.round(v * 100)}%`), "max"],
  ["grade_avg", "Grade (1–5)", (v) => (v === null ? "—" : v.toFixed(2)), "max"],
  ["latency_avg", "Avg latency", (v) => (v === null ? "—" : `${v}s`), "min"],
  ["calls_avg", "Calls / question", (v) => (v === null ? "—" : v), "min"],
  ["tokens_avg", "Tokens / question", (v) => (v === null ? "—" : Math.round(v)), "min"],
  ["errors", "Errors", (v) => v, "min"],
];

// Highlights the best value per column — only when values differ (a tie
// isn't a win) and only within one run (`highlight` off for history).
function summaryTable(rows, labelKey, extraCols = [], highlight = true) {
  const best = {};
  for (const [key, , , dir] of highlight ? COLUMNS : []) {
    const vals = rows.map((r) => r[key]).filter((v) => v !== null && v !== undefined);
    if (vals.length > 1 && new Set(vals).size > 1)
      best[key] = dir === "max" ? Math.max(...vals) : Math.min(...vals);
  }
  const head = `<tr>${extraCols.map(([, h]) => `<th>${h}</th>`).join("")}<th>Target</th>
    ${COLUMNS.map(([, h]) => `<th>${h}</th>`).join("")}</tr>`;
  const body = rows
    .map(
      (r) => `<tr>${extraCols.map(([k]) => `<td class="muted">${esc(r[k])}</td>`).join("")}
        <td><strong>${esc(r[labelKey])}</strong></td>
        ${COLUMNS.map(([k, , fmt]) =>
          `<td class="${best[k] !== undefined && r[k] === best[k] ? "best" : ""}">${esc(fmt(r[k]))}</td>`
        ).join("")}</tr>`
    )
    .join("");
  return head + body;
}

function renderSummary(summaries) {
  $("summary-table").innerHTML = summaryTable(summaries, "label");
  show("summary-card");
}

function loadHistory() {
  fetch("api/history").then((r) => r.json()).then(({ runs }) => {
    $("history").innerHTML = runs.length
      ? summaryTable(runs, "label", [["date", "When"], ["dataset", "Set"]], false)
      : `<tr><td class="muted">No runs yet.</td></tr>`;
  });
}

function showError(message) {
  $("error-card").textContent = message;
  show("error-card");
}
