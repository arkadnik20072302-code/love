import { currentUser, deleteMemory, isWishlistMember, listMemories, memorySchemaHint, signOut, updateRow, uploadMemory } from "./pages-client.js";
import { initMemoryGallery } from "./memory-gallery.js";

const $ = selector => document.querySelector(selector);

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
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

  $("#logout").hidden = false;
  await initMemoryGallery({
    listMemories,
    uploadMemory,
    updateMemory: (id, changes) => updateRow("memories", id, changes),
    deleteMemory,
  });
}

$("#logout").addEventListener("click", async () => {
  await signOut();
  location.replace("./login.html");
});

start().catch(error => {
  const grid = $("#memory-grid");
  const detail = String(error.message || error);
  const message = memorySchemaHint(error) || `Не удалось открыть альбом: ${detail}`;
  grid.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
});
