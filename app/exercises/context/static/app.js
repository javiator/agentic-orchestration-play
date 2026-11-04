// Context UI: replay the script under every strategy, then show the quiz
// grid (probe × strategy), a chart of context size per turn, and what the
// summary/notes strategies kept. Helpers come from /static/util.js.

const COLORS = { full: "#2563eb", window: "#dc2626", summary: "#16a34a", notes: "#d97706" };
let info = null;
const answers = {};  // "strategy|probe" → {answer, correct}

fetch("api/info").then((r) => r.json()).then((i) => {
  info = i;
  const s = i.script;
  const planted = Object.fromEntries(s.probes.map((p) => [p.planted, p.id]));
  $("script-info").textContent = `${s.title}: the user mentions facts early, mid-way and late — and changes one (the budget). ` +
    `Afterwards each strategy is asked ${s.probes.length} questions about those facts.`;
  $("script").innerHTML = s.messages.map((m, n) => `<div class="msg ${m.role} ${planted[n + 1] ? "planted" : ""}">
    <span class="who">#${n + 1} ${m.role}</span>${esc(m.content)}
    ${planted[n + 1] ? ` <span class="badge warn tag">quiz: ${esc(planted[n + 1])}</span>` : ""}</div>`).join("");
  $("setup").innerHTML = [["model", i.model], ["window", `${i.window} messages`],
    ["compact every", `${i.compact_every} messages`], ["keep raw", `last ${i.keep_recent}`]]
    .map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  if (i.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

async function run() {
  $("run").disabled = true;
  ["chart-card", "state-card", "lesson", "error-card", "answer-box"].forEach(hide);
  buildGrid();
  show("results");
  try {
    const done = await runJob("api/run", {}, onEvent);
    finish(done.results);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("run").disabled = false;
}

function buildGrid() {
  const kinds = Object.keys(info.strategies);
  $("grid").innerHTML = `<tr><th>Question (fact from message #)</th>${kinds.map((k) =>
      `<th style="color:${COLORS[k]}">${esc(info.strategies[k])}</th>`).join("")}</tr>` +
    info.script.probes.map((p) => `<tr><td>${esc(p.question)} <span class="muted small">#${p.planted}${p.note ? ` · ${esc(p.note)}` : ""}</span></td>
      ${kinds.map((k) => `<td class="cell" data-key="${k}|${p.id}"><span class="status-dot running" style="display:inline-block"></span></td>`).join("")}</tr>`).join("") +
    `<tr class="total"><td>Score</td>${kinds.map((k) => `<td class="cell" id="score-${k}">…</td>`).join("")}</tr>` +
    `<tr><td class="muted small">Context at the end · all turns</td>${kinds.map((k) => `<td class="muted small" id="tok-${k}"></td>`).join("")}</tr>` +
    `<tr><td class="muted small">Model calls (compaction + quiz)</td>${kinds.map((k) => `<td class="muted small" id="use-${k}"></td>`).join("")}</tr>`;
}

$("grid").addEventListener("click", (e) => {
  const cell = e.target.closest("td.cell[data-key]");
  const a = cell && answers[cell.dataset.key];
  if (!a) return;
  const [k, p] = cell.dataset.key.split("|");
  const probe = info.script.probes.find((x) => x.id === p);
  $("answer-box").innerHTML = `<div class="lesson"><strong>${esc(info.strategies[k])}</strong> — “${esc(probe.question)}”
    <div>${esc(a.answer)}</div><div class="small muted">expected: ${esc(probe.expected.join(" / "))} → ${a.correct ? "✓ correct" : "✗ missed"}</div></div>`;
  show("answer-box");
});

function onEvent(ev) {
  if (ev.type === "probe_result") {
    answers[`${ev.strategy}|${ev.probe}`] = ev;
    const cell = document.querySelector(`td.cell[data-key="${ev.strategy}|${ev.probe}"]`);
    if (cell) cell.innerHTML = ev.correct ? '<span class="badge ok">✓</span>' : '<span class="badge bad">✗</span>';
  } else if (ev.type === "strategy_finished") {
    if (ev.error) { $(`score-${ev.strategy}`).innerHTML = `<span class="bad-text small">${esc(ev.error)}</span>`; return; }
    $(`score-${ev.strategy}`).textContent = `${ev.score}/${ev.probes}`;
    $(`tok-${ev.strategy}`).textContent = `~${ev.final_tokens.toLocaleString()} · ~${ev.total_tokens.toLocaleString()}`;
    $(`use-${ev.strategy}`).textContent = `${ev.usage.calls} calls`;
  }
}

function finish(results) {
  const ok = Object.values(results).filter((r) => !r.error);
  drawChart(ok);
  const states = ok.filter((r) => r.state);
  $("states").innerHTML = states.map((r) => `<div class="state"><strong style="color:${COLORS[r.strategy]}">${esc(info.strategies[r.strategy])}</strong>
    <pre class="box">${esc(r.state)}</pre></div>`).join("");
  if (states.length) show("state-card");
  const by = Object.fromEntries(ok.map((r) => [r.strategy, r]));
  if (by.full && by.window) {
    const ratio = (by.full.total_tokens / Math.max(1, by.window.total_tokens)).toFixed(1);
    $("lesson").innerHTML = `<h3>What to notice</h3><ul>
      <li><strong>Full history</strong> scored ${by.full.score}/${by.full.probes} but sent ${ratio}× the tokens of the window over the conversation — and its cost keeps growing every turn.</li>
      <li><strong>Sliding window</strong> scored ${by.window.score}/${by.window.probes}: it remembers recent facts perfectly and older ones not at all.</li>
      ${by.summary ? `<li><strong>Summary</strong> scored ${by.summary.score}/${by.summary.probes}: check the summary above for which specifics (numbers, names) survived.</li>` : ""}
      ${by.notes ? `<li><strong>Notes</strong> scored ${by.notes.score}/${by.notes.probes}: did it keep the <em>updated</em> budget, not the old one?</li>` : ""}
      </ul><span class="muted">Real assistants combine these: recent messages verbatim, a summary of older ones, and a fact store for things that must never be forgotten.</span>`;
    show("lesson");
  }
}

// Minimal SVG line chart: x = user turn, y = context tokens.
function drawChart(results) {
  const W = 640, H = 220, P = 34;
  const n = Math.max(...results.map((r) => r.per_turn.length));
  const max = Math.max(...results.flatMap((r) => r.per_turn), 1);
  const x = (i) => P + (i * (W - 2 * P)) / Math.max(1, n - 1);
  const y = (v) => H - P + 6 - (v / max) * (H - 2 * P);
  const lines = results.map((r) => `<polyline fill="none" stroke="${COLORS[r.strategy]}" stroke-width="2.5"
      points="${r.per_turn.map((v, i) => `${x(i)},${y(v)}`).join(" ")}"/>`).join("");
  const ticks = [0, Math.round(max / 2), max].map((v) => `<text x="${P - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="currentColor" opacity=".6">${v}</text>
      <line x1="${P}" x2="${W - P}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".08"/>`).join("");
  const xs = Array.from({ length: n }, (_, i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="10" fill="currentColor" opacity=".6">${i + 1}</text>`).join("");
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Context tokens per user turn">${ticks}${xs}${lines}</svg>`;
  $("legend").innerHTML = results.map((r) => `<span style="--c:${COLORS[r.strategy]}">${esc(info.strategies[r.strategy])}</span>`).join("") +
    `<span class="muted">x-axis: user turn</span>`;
  show("chart-card");
}
