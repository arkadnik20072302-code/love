import { initStoriesPage } from "./stories-page.js";

async function requestStories(method = "GET", payload) {
  const response = await fetch("/api/stories", {
    method,
    headers: payload ? { "Content-Type": "application/json" } : {},
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("Сессия истекла. Войдите снова.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Не удалось загрузить истории.");
  return result.stories || result.story;
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Не удалось прочитать фотографию."));
    reader.readAsDataURL(file);
  });
}

async function listStories() {
  return requestStories();
}

async function createStory(story) {
  const photo = story.photo ? await readAsDataUrl(story.photo) : "";
  return requestStories("POST", { ...story, photo });
}

initStoriesPage({ listStories, createStory }).catch(error => {
  const message = document.createElement("div");
  message.className = "empty-state";
  message.textContent = error.message;
  document.querySelector("#story-list").replaceChildren(message);
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
