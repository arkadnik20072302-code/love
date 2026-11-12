import { initMemoryGallery } from "./memory-gallery.js";

const $ = selector => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("Сессия истекла. Войдите снова.");
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос.");
  return payload;
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Не удалось прочитать фотографию."));
    reader.readAsDataURL(file);
  });
}

async function uploadMemory(memoryDate, file) {
  const photo = await readAsDataUrl(file);
  await api("/api/memories", {
    method: "POST",
    body: JSON.stringify({ memory_date: memoryDate, photo }),
  });
}

initMemoryGallery({
  listMemories: async () => (await api("/api/memories")).memories,
  uploadMemory,
  updateMemory: (id, changes) => api(`/api/memories/${id}`, {
    method: "PUT",
    body: JSON.stringify(changes),
  }),
}).catch(error => console.error("Не удалось загрузить альбом воспоминаний:", error));

fetch("/health")
  .then(response => response.json())
  .then(health => { $("#logout").hidden = !health.authentication; })
  .catch(() => {});

$("#logout").addEventListener("click", async () => {
  try {
    const response = await fetch("/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error("Не удалось завершить сеанс.");
    location.replace("/login");
  } catch (error) {
    const toast = $("#toast");
    toast.textContent = error.message;
    toast.classList.add("is-visible");
  }
});
