// Landing page: one card per exercise discovered by the server.

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

fetch("/api/exercises")
  .then((r) => r.json())
  .then(({ exercises }) => {
    const grid = document.getElementById("exercise-grid");
    if (exercises.length === 0) {
      document.getElementById("empty").classList.remove("hidden");
      return;
    }
    for (const ex of exercises) {
      const card = el("a", "card exercise-card");
      card.href = ex.url;
      card.append(el("h2", "", ex.title), el("p", "", ex.description));
      for (const tag of ex.tags) card.append(el("span", "chip", tag));
      grid.append(card);
    }
  })
  .catch((err) => {
    const card = document.getElementById("error-card");
    card.textContent = `Could not load exercises: ${err.message}`;
    card.classList.remove("hidden");
  });
