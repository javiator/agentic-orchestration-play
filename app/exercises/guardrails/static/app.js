// Guardrails UI: send one message to both bots; show the guarded pipeline's
// checks as they run and flag any leak of the secret.
// Helpers come from /static/util.js.

const VERDICT = { pass: "ok", redact: "info", block: "bad" };
const ALL_GUARDS = ["PII redaction", "injection patterns", "input classifier",
  "secret-leak check", "PII in output", "output policy"];
let secret = "";

fetch("api/info").then((r) => r.json()).then((info) => {
  secret = info.secret;
  $("setup").innerHTML = [["bot model (both)", info.bot], ["guard model", info.guard],
    ["the secret", info.secret], ["output policy check", info.output_policy ? "on" : "off"]]
    .map(([k, v]) => `<span><span class="muted">${esc(k)}:</span> <code>${esc(v)}</code></span>`).join("");
  $("prompt").textContent = info.system_prompt;
  sampleChips("samples", info.samples, "message");
  if (info.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

async function run() {
  const message = $("message").value.trim();
  if (!message) { $("message").focus(); return; }
  $("run").disabled = true;
  ["error-card", "lesson"].forEach(hide);
  for (const v of ["unguarded", "guarded"]) {
    const el = $(`v-${v}`);
    el.querySelector(".status-dot").className = "status-dot running";
    el.querySelector(".stats").textContent = "";
    el.querySelector(".flag").innerHTML = "";
    el.querySelector(".answer").innerHTML = `<span class="spinner-note">Working…</span>`;
  }
  $("v-guarded").querySelector(".pipeline").innerHTML = "";
  show("results");
  try {
    const done = await runJob("api/run", { message }, onEvent);
    finish(done.results);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("run").disabled = false;
}

function onEvent(ev) {
  if (ev.type === "guard") {
    $("v-guarded").querySelector(".pipeline").insertAdjacentHTML("beforeend", `
      <div class="guard-row"><span class="stage">${esc(ev.stage)}</span>
        <span class="badge ${VERDICT[ev.verdict]}">${esc(ev.verdict)}</span>
        <span><strong>${esc(ev.name)}</strong> <span class="badge">${esc(ev.kind)}</span> <span class="detail">${esc(ev.detail)}</span></span>
        <span class="ms">${ev.ms} ms</span></div>`);
  } else if (ev.type === "variant_finished") {
    const el = $(`v-${ev.variant}`);
    el.querySelector(".status-dot").className = `status-dot ${ev.error ? "fail" : "ok"}`;
    if (ev.error) { el.querySelector(".answer").innerHTML = `<span class="bad-text">Error: ${esc(ev.error)}</span>`; return; }
    el.querySelector(".stats").textContent = usageText(ev.usage);
    el.querySelector(".flag").innerHTML = ev.leaked ? `<span class="badge bad">⚠ leaked the secret</span>`
      : ev.blocked_by ? `<span class="badge warn">blocked by: ${esc(ev.blocked_by)}</span>`
      : `<span class="badge ok">answered, no leak</span>`;
    el.querySelector(".answer").innerHTML = highlight(ev.answer);
    if (ev.variant === "guarded") markSkipped();
  }
}

function markSkipped() {
  const pipe = $("v-guarded").querySelector(".pipeline");
  const ran = new Set([...pipe.querySelectorAll("strong")].map((s) => s.textContent));
  for (const g of ALL_GUARDS) if (!ran.has(g))
    pipe.insertAdjacentHTML("beforeend", `<div class="guard-row skipped"><span class="stage"></span><span class="badge">skipped</span>
      <span><strong>${esc(g)}</strong> <span class="detail">not reached</span></span><span></span></div>`);
}

function highlight(text) {
  const html = mdLite(text);
  return secret ? html.split(esc(secret)).join(`<mark>${esc(secret)}</mark>`) : html;
}

function finish(r) {
  const u = r.unguarded, g = r.guarded;
  if (!u || !g || u.error || g.error) return;
  let text;
  if (u.leaked && !g.leaked) text = "The prompt instruction alone failed — the unguarded bot leaked the code. The guarded pipeline stopped it.";
  else if (!u.leaked && g.blocked_by) text = `The unguarded bot didn't leak this time, but the guarded one still blocked (${g.blocked_by}). For an attack that's defence in depth; for a normal question it's a false positive — the cost of guarding.`;
  else if (!u.leaked && !g.blocked_by) text = "Both answered normally. Note what the guards cost on a harmless message (calls, tokens, latency).";
  else text = "Both leaked — a gap in the guards. Which layer should have caught it?";
  $("lesson").innerHTML = `${esc(text)} <span class="muted">Guarded cost ${g.usage.calls} model calls vs ${u.usage.calls};
    cheap code checks ran first so obvious attacks never reached a model.</span>`;
  show("lesson");
}
