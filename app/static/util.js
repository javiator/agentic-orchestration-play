// Small helpers shared by exercise pages. Model output is untrusted: always
// escape before inserting as HTML (mdLite does this first).

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Minimal markdown: escape HTML, then render **bold** and headings only —
// enough for model output without pulling in a renderer dependency.
function mdLite(text) {
  return esc(text)
    .replace(/^#{1,4}\s+(.+)$/gm, "<strong>$1</strong>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

// Start a background job and stream its events: POST `url` with `fields`
// (an object; array values become repeated fields), then open the SSE stream
// at `streamBase` + job_id and call onEvent(ev) for each event. Resolves with
// the final 'done' event; rejects with an Error on 'error', a failed start or
// a lost connection. The job pattern every exercise uses (app/core/jobs.py).
async function runJob(url, fields, onEvent, streamBase = "api/stream/") {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields || {}))
    for (const item of Array.isArray(v) ? v : [v]) form.append(k, item);
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(`Failed to start: ${detail}`);
  }
  const job = await res.json();
  return new Promise((resolve, reject) => {
    const es = new EventSource(streamBase + job.job_id);
    es.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      try { onEvent(ev, job); } catch (err) { console.error(err); }
      if (ev.type === "done") { es.close(); resolve(ev); }
      else if (ev.type === "error") { es.close(); reject(new Error(ev.message)); }
    };
    es.onerror = () => { es.close(); reject(new Error("Connection to server lost.")); };
  });
}

// Fill a .samples container with clickable chips that copy into `targetId`.
function sampleChips(containerId, samples, targetId) {
  $(containerId).innerHTML = samples.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("");
  $(containerId).addEventListener("click", (e) => {
    const b = e.target.closest("button[data-q]");
    if (b) $(targetId).value = b.dataset.q;
  });
}

// "~1,234 tok · $0.0012" style usage line from a {calls, tokens, cost} dict.
function usageText(u) {
  const parts = [];
  if (u.latency !== undefined) parts.push(`${u.latency}s`);
  if (u.calls !== undefined) parts.push(`${u.calls} call${u.calls === 1 ? "" : "s"}`);
  if (u.tokens !== undefined) parts.push(`~${Number(u.tokens).toLocaleString()} tok`);
  if (u.cost) parts.push(`$${u.cost.toFixed(4)}`);
  return parts.join(" · ");
}
