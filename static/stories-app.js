const list = document.querySelector("#story-list");

async function loadStories() {
  const response = await fetch("/api/stories");
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("Сессия истекла. Войдите снова.");
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Не удалось загрузить истории.");
  return payload.stories;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00`));
}

function renderStories(stories) {
  list.innerHTML = stories.length
    ? stories.map(story => `<article class="story-card">
        <div class="story-card-date">${escapeHtml(formatDate(story.memory_date))}</div>
        <h2>${escapeHtml(story.story_title || "История одного момента")}</h2>
        <p>${escapeHtml(story.story_text || "Эта фотография тоже хранит свою историю.")}</p>
      </article>`).join("")
    : `<div class="empty-state">Истории появятся здесь, когда вы добавите их к фотографиям.</div>`;
}

loadStories().then(renderStories).catch(error => {
  list.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
});

fetch("/health")
  .then(response => response.json())
  .then(health => { document.querySelector("#logout").hidden = !health.authentication; })
  .catch(() => {});

document.querySelector("#logout").addEventListener("click", async () => {
  try {
    const response = await fetch("/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error("Не удалось завершить сеанс.");
    location.replace("/login");
  } catch (error) {
    const toast = document.querySelector("#toast");
    toast.textContent = error.message;
    toast.classList.add("is-visible");
  }
});
