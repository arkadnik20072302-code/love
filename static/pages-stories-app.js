import {
  currentUser,
  createStory,
  isWishlistMember,
  listStories,
  memorySchemaHint,
  signOut,
  updateStory,
} from "./pages-client.js";
import { initStoriesPage } from "./stories-page.js";

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

  await initStoriesPage({
    listStories,
    createStory,
    updateStory,
    formatError: error => memorySchemaHint(error) || error.message,
  });
  document.querySelector("#logout").hidden = false;
}

document.querySelector("#logout").addEventListener("click", async () => {
  await signOut();
  location.replace("./login.html");
});

start().catch(error => {
  const message = document.createElement("div");
  message.className = "empty-state";
  message.textContent = memorySchemaHint(error) || error.message;
  document.querySelector("#story-list").replaceChildren(message);
});
