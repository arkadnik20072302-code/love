import { currentUser, isWishlistMember, listMemoryStories, signOut } from "./pages-client.js";

const list = document.querySelector("#story-list");

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

async function start() {
  const user = await currentUser();
  if (!user) {
    location.replace("./login.html");
    return;
  }
  if (!await isWishlistMember()) {
    await signOut();
    location.replace("./login.html?access=denied");
    return;
  }
  renderStories(await listMemoryStories());
  document.querySelector("#logout").hidden = false;
}

document.querySelector("#logout").addEventListener("click", async () => {
  await signOut();
  location.replace("./login.html");
});

start().catch(error => {
  list.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
});
