// Shared nav bar for exercise pages: a link back to the landing page and a
// switcher to jump straight to another exercise. Include it with
// <script src="/static/nav.js"></script> — it inserts itself at the top.
(async () => {
  const nav = document.createElement("nav");
  nav.className = "exercise-nav";
  const home = document.createElement("a");
  home.href = "/";
  home.textContent = "← All exercises";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Switch exercise");
  nav.append(home, select);
  document.body.prepend(nav);

  const current = location.pathname.split("/")[1];
  try {
    const { exercises } = await (await fetch("/api/exercises")).json();
    for (const ex of exercises) {
      const opt = new Option(ex.title, ex.url, false, ex.slug === current);
      select.append(opt);
    }
    select.addEventListener("change", () => { location.href = select.value; });
  } catch {
    select.remove();  // landing link still works without the switcher
  }
})();
