// HITL UI: run the agent; safe actions appear as they run, risky ones appear
// as approval cards (approve / edit arguments / reject with feedback) that
// POST the decision back so the paused agent continues.
// Helpers come from /static/util.js.

const RISK_BADGE = { read: "ok", write: "info", money: "warn", external: "bad" };
let currentJob = null;
let prevWorld = null;

fetch("api/info").then((r) => r.json()).then((info) => {
  const byRisk = {};
  for (const [tool, risk] of Object.entries(info.risks)) (byRisk[risk] ||= []).push(tool);
  const rule = { read: "runs automatically", write: "runs automatically (internal, reversible)",
    money: `auto-approved up to $${info.auto_approve_limit}, above that you decide`, external: "always needs your approval" };
  $("tiers").innerHTML = Object.entries(byRisk).map(([risk, tools]) => `
    <div class="tier"><span class="badge ${RISK_BADGE[risk]}">${esc(risk)}</span> ${tools.map((t) => `<code>${esc(t)}</code>`).join(" ")}
      <div class="muted small">${esc(rule[risk])}</div></div>`).join("");
  $("policy").textContent = info.policy;
  $("setup").textContent = `model: ${info.model} · pauses wait up to ${Math.round(info.approval_timeout / 60)} min`;
  sampleChips("samples", info.samples, "request");
  renderWorld(info.world);
  if (info.mock) show("mock-badge");
});
$("run").addEventListener("click", run);

async function run() {
  const request = $("request").value.trim();
  if (!request) { $("request").focus(); return; }
  $("run").disabled = true;
  $("timeline").innerHTML = "";
  ["final-card", "error-card"].forEach(hide);
  $("stats").textContent = "";
  show("timeline-section");
  prevWorld = null;
  try {
    const done = await runJob("api/run", { request }, (ev, job) => { currentJob = job.job_id; onEvent(ev); });
    renderDone(done);
  } catch (err) { $("error-card").textContent = err.message; show("error-card"); }
  $("run").disabled = false;
}

function onEvent(ev) {
  if (ev.type === "run_started") renderWorld(ev.world);
  else if (ev.type === "agent_note") $("timeline").insertAdjacentHTML("beforeend", `<p class="note">“${esc(ev.text)}”</p>`);
  else if (ev.type === "approval_required") addApproval(ev);
  else if (ev.type === "action") { addAction(ev); renderWorld(ev.world); }
}

function argsText(a) { return JSON.stringify(a, null, 2); }

function addAction(ev) {
  const pending = ev.action_id && $(`ap-${ev.action_id}`);
  const label = { auto: '<span class="badge ok">auto</span>', approve: '<span class="badge ok">you approved</span>',
    edit: '<span class="badge info">you edited + approved</span>', reject: '<span class="badge bad">you rejected</span>' }[ev.decision];
  const html = `<div class="head"><strong>Step ${ev.step}</strong> <code>${esc(ev.tool)}</code>
      <span class="badge ${RISK_BADGE[ev.risk] || ""}">${esc(ev.risk)}</span> ${label}</div>
    <code class="args">${esc(argsText(ev.args))}</code>
    <div class="result">→ ${esc(ev.result)}</div>`;
  if (pending) { pending.className = "card event"; pending.innerHTML = html; }
  else $("timeline").insertAdjacentHTML("beforeend", `<div class="card event">${html}</div>`);
}

function addApproval(ev) {
  const card = document.createElement("div");
  card.className = "card event approval";
  card.id = `ap-${ev.action_id}`;
  card.innerHTML = `<div class="head"><strong>⏸ Step ${ev.step} — approval needed</strong> <code>${esc(ev.tool)}</code>
      <span class="badge ${RISK_BADGE[ev.risk] || ""}">${esc(ev.risk)}</span><span class="meta">the agent is waiting for you</span></div>
    <div class="small">${esc(ev.reason)}</div>
    <textarea rows="5" class="args-edit">${esc(argsText(ev.args))}</textarea>
    <input class="feedback" placeholder="Feedback for the agent (used when rejecting)">
    <div class="buttons"><button class="approve">Approve</button><button class="edit">Approve with my edits</button>
      <button class="reject">Reject</button></div><div class="small bad-text msg"></div>`;
  $("timeline").append(card);
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  const send = async (decision) => {
    const fields = { action_id: ev.action_id, decision, feedback: card.querySelector(".feedback").value };
    if (decision === "edit") fields.args = card.querySelector(".args-edit").value;
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const res = await fetch(`api/decide/${currentJob}`, { method: "POST", body: Object.entries(fields)
      .reduce((f, [k, v]) => (f.append(k, v), f), new FormData()) });
    if (!res.ok) {
      card.querySelector(".msg").textContent = (await res.json()).detail || res.statusText;
      card.querySelectorAll("button").forEach((b) => (b.disabled = false));
    } else card.querySelector(".msg").innerHTML = `<span class="spinner-note">sent — agent continuing…</span>`;
  };
  card.querySelector(".approve").onclick = () => send("approve");
  card.querySelector(".edit").onclick = () => send("edit");
  card.querySelector(".reject").onclick = () => send("reject");
}

function renderWorld(w) {
  const rows = Object.entries(w.orders).map(([id, o]) => {
    const before = prevWorld?.orders[id];
    const changed = before && JSON.stringify(before) !== JSON.stringify(o);
    return `<tr class="${changed ? "changed" : ""}"><td><code>${esc(id)}</code></td><td>${esc(o.customer)}</td>
      <td>${esc(o.item)}</td><td>$${o.total}</td><td>${esc(o.status)}</td><td>${o.refunded ? `$${o.refunded}` : "—"}</td>
      <td>${o.notes.length ? "📝" + o.notes.length : ""}</td></tr>`;
  }).join("");
  $("world").innerHTML = `<table class="world-table"><tr><th>Order</th><th>Customer</th><th>Item</th><th>Total</th>
    <th>Status</th><th>Refunded</th><th></th></tr>${rows}</table>
    <div class="outbox"><strong>Outbox:</strong> ${w.outbox.length ? w.outbox.map((m) =>
      `<div>✉ to ${esc(m.to)} — “${esc(m.subject)}”</div>`).join("") : '<span class="muted">empty</span>'}</div>`;
  prevWorld = w;
}

function renderDone(d) {
  renderWorld(d.world);
  $("final-body").innerHTML = mdLite(d.answer || "(no summary)");
  const s = d.stats;
  $("stats").textContent = `${s.auto} auto · ${s.approved + s.edited} approved · ${s.rejected} rejected · waited ${s.waited}s for you · ${usageText(d.usage)}`;
  $("lesson").innerHTML = `Humans were only involved in ${s.approved + s.edited + s.rejected} of ${s.auto + s.approved + s.edited + s.rejected} actions —
    the irreversible ones. <span class="muted">Try rejecting the email with feedback ("mention the 5–7 day refund time")
    and watch the agent revise it, or edit a refund amount down.</span>`;
  show("final-card");
}
