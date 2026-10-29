// Reflection UI: each version is a card (text, critic score, issues), with an
// optional word diff against the previous version; a bar chart shows scores
// per round. Helpers come from /static/util.js.

let versions = [];  // [{n, text, score, issues}]
let target = 8;

fetch("api/info").then((r) => r.json()).then((info) => {
  target = info.target;
  $("setup").innerHTML = [["writer", info.writer], ["independent critic", info.critic_independent],
    ["max rounds", info.max_rounds], ["target score", `${info.target}/10`]]
    .map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  $("critic-label").textContent = `(${info.critic_independent})`;
  sampleChips("samples", info.samples, "task");
  if (info.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

async function run() {
  const task = $("task").value.trim();
  if (!task) { $("task").focus(); return; }
  const mode = document.querySelector('input[name="mode"]:checked').value;
  $("run").disabled = true;
  versions = [];
  $("versions").innerHTML = "";
  ["error-card", "lesson"].forEach(hide);
  $("stats").textContent = "";
  renderBars(null);
  show("scoreboard");
  try {
    const done = await runJob("api/run", { task, mode }, onEvent);
    finish(done);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("run").disabled = false;
}

function onEvent(ev) {
  if (ev.type === "version") {
    versions.push({ n: ev.n, text: ev.text, score: null, issues: [] });
    renderVersion(ev.n);
  } else if (ev.type === "critique") {
    const v = versions.find((x) => x.n === ev.n);
    Object.assign(v, { score: ev.score, issues: ev.issues, note: ev.note });
    renderVersion(ev.n);
    renderBars(null);
  } else if (ev.type === "revising") {
    $("versions").insertAdjacentHTML("beforeend", `<p class="spinner-note" id="revising">Writer is revising (v${ev.n})…</p>`);
  }
}

function renderVersion(n) {
  $("revising")?.remove();
  const v = versions.find((x) => x.n === n);
  let card = $(`v-${n}`);
  if (!card) {
    card = document.createElement("div");
    card.className = "card version";
    card.id = `v-${n}`;
    $("versions").append(card);
  }
  const prev = versions.find((x) => x.n === n - 1);
  const scoreBadge = v.score === null ? `<span class="status-dot running"></span><span class="muted small">critic reviewing…</span>`
    : `<span class="badge ${v.score >= target ? "ok" : "info"}">${v.score}/10</span>`;
  card.innerHTML = `<h2>Version ${n} ${scoreBadge}
      ${prev ? `<button class="link-btn toggle-diff">show changes vs v${n - 1}</button>` : ""}</h2>
    <div class="text">${esc(v.text)}</div>
    ${v.score !== null || v.note ? `<div class="critique"><strong class="small">Critic:</strong>
      ${v.note ? `<span class="bad-text small">${esc(v.note)}</span>` : ""}
      ${v.issues.length ? `<ul class="issues">${v.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`
        : v.note ? "" : `<span class="muted small">no issues listed</span>`}</div>` : ""}`;
  const btn = card.querySelector(".toggle-diff");
  if (btn) btn.addEventListener("click", () => {
    const showing = btn.dataset.on === "1";
    card.querySelector(".text").innerHTML = showing ? esc(v.text) : diffHtml(prev.text, v.text);
    btn.dataset.on = showing ? "0" : "1";
    btn.textContent = showing ? `show changes vs v${n - 1}` : "hide changes";
  });
}

function renderBars(bestN) {
  $("bars").innerHTML = versions.map((v) => `
    <div class="bar ${v.n === bestN ? "best" : ""}">
      <span class="val">${v.score ?? "…"}</span>
      <span class="fill" style="height:${((v.score || 0) / 10) * 100}%"></span>
      <span class="lbl">v${v.n}${v.n === bestN ? " ★" : ""}</span></div>`).join("") +
    `<span class="target-line">target ${target}/10</span>`;
}

const STOP_TEXT = {
  "good enough": "reached the target score",
  plateau: "the last revision didn't improve the score (plateau)",
  budget: "used all rounds",
  "critic failed": "a critique couldn't be parsed",
};

function finish(done) {
  renderBars(done.best.n);
  document.querySelectorAll(".version").forEach((el) => el.classList.toggle("best", el.id === `v-${done.best.n}`));
  $("stats").textContent = usageText(done.usage);
  const s = done.scores.filter((x) => x !== null);
  const gains = s.slice(1).map((x, i) => x - s[i]);
  const gainText = gains.length ? `Gain per round: ${gains.map((g) => (g > 0 ? "+" : "") + g).join(", ")}.` : "";
  $("lesson").innerHTML = `<strong>Stopped:</strong> ${esc(STOP_TEXT[done.stop] || done.stop)}.
    Kept <strong>version ${done.best.n}</strong> (score ${done.best.score ?? "?"}) — the best, not necessarily the last.
    ${esc(gainText)} <span class="muted">Every round costs about the same; is the last gain worth it?
    Try "Self-critique" on the same task and compare how generous the scores are.</span>`;
  show("lesson");
}

// Word-level diff (LCS) between two short texts → HTML with <ins>/<del>.
function diffHtml(a, b) {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/);
  const m = x.length, n = y.length;
  if (m * n > 400000) return esc(b);  // too long to diff cheaply
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, out = "";
  while (i < m && j < n) {
    if (x[i] === y[j]) { out += esc(x[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out += `<del>${esc(x[i])}</del>`; i++; }
    else { out += `<ins>${esc(y[j])}</ins>`; j++; }
  }
  while (i < m) out += `<del>${esc(x[i++])}</del>`;
  while (j < n) out += `<ins>${esc(y[j++])}</ins>`;
  return out;
}
