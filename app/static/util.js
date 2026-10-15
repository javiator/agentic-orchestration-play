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
