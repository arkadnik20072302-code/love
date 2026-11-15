const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const MAX_PHOTO_BYTES = 3 * 1024 * 1024;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
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

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Не удалось прочитать фотографию."));
    reader.readAsDataURL(file);
  });
}

export function initStoriesPage({
  listStories,
  createStory,
  updateStory,
  formatError = error => error.message,
}) {
  const list = $("#story-list");
  const dialog = $("#story-dialog");
  const form = $("#story-form");
  const photoInput = $("#story-photo");
  const photoPreview = $("#story-photo-preview");
  const photoImage = $("#story-photo-image");
  const error = $("#story-error");
  const saveButton = $("#story-save");
  const heading = $("#story-form-heading");
  let stories = [];
  let editingStoryId = null;
  let originalPhoto = "";
  let removeExistingPhoto = false;
  let toastTimer;

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
  }

  function renderStories(stories) {
    list.innerHTML = stories.length
      ? stories.map(story => `<article class="story-card">
          ${story.photo ? `<img class="story-card-photo" src="${escapeHtml(story.photo)}" alt="Фото к истории «${escapeHtml(story.title)}»" loading="lazy">` : ""}
          <div class="story-card-date">${escapeHtml(formatDate(story.date))}</div>
          <h2>${escapeHtml(story.title)}</h2>
          <p>${escapeHtml(story.story_text)}</p>
          ${story.editable === false ? "" : `<div class="story-card-actions">
            <button class="story-edit-button" type="button" data-edit-story="${escapeHtml(story.id)}">
              <span aria-hidden="true">✎</span> Изменить историю
            </button>
          </div>`}
        </article>`).join("")
      : `<div class="empty-state">Истории появятся здесь, когда вы добавите первую.</div>`;
  }

  async function refresh() {
    try {
      stories = await listStories();
      renderStories(stories);
    } catch (loadError) {
      list.innerHTML = `<div class="empty-state">${escapeHtml(formatError(loadError))}</div>`;
      throw loadError;
    }
  }

  function resetPhotoPreview() {
    photoInput.value = "";
    if (editingStoryId && originalPhoto && !removeExistingPhoto) {
      photoImage.src = originalPhoto;
      photoPreview.hidden = false;
    } else {
      photoImage.removeAttribute("src");
      photoPreview.hidden = true;
    }
  }

  function resetEditor() {
    form.reset();
    editingStoryId = null;
    originalPhoto = "";
    removeExistingPhoto = false;
    error.textContent = "";
    heading.textContent = "Новая история";
    saveButton.textContent = "Сохранить историю";
    resetPhotoPreview();
  }

  function openEditor(story) {
    resetEditor();
    editingStoryId = story.id;
    originalPhoto = story.photo || "";
    $("#story-date").value = story.date;
    $("#story-title").value = story.title;
    $("#story-text").value = story.story_text;
    heading.textContent = "Изменить историю";
    saveButton.textContent = "Сохранить изменения";
    if (originalPhoto) {
      photoImage.src = originalPhoto;
      photoPreview.hidden = false;
    }
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  $("#story-add-open").addEventListener("click", () => {
    resetEditor();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  });

  list.addEventListener("click", event => {
    const button = event.target.closest("[data-edit-story]");
    if (!button) return;
    const story = stories.find(item => item.id === button.dataset.editStory);
    if (story) openEditor(story);
  });

  $("#story-photo-remove").addEventListener("click", () => {
    photoInput.value = "";
    removeExistingPhoto = Boolean(editingStoryId && originalPhoto);
    photoImage.removeAttribute("src");
    photoPreview.hidden = true;
  });

  photoInput.addEventListener("change", async () => {
    const file = photoInput.files[0];
    if (!file) return;
    if (!IMAGE_TYPES.includes(file.type)) {
      resetPhotoPreview();
      error.textContent = "Поддерживаются фотографии JPEG, PNG и WebP.";
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      resetPhotoPreview();
      error.textContent = "Фотография должна быть меньше 3 МБ.";
      return;
    }
    error.textContent = "";
    removeExistingPhoto = false;
    try {
      photoImage.src = await readAsDataUrl(file);
      photoPreview.hidden = false;
    } catch (previewError) {
      resetPhotoPreview();
      error.textContent = previewError.message;
    }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    saveButton.disabled = true;
    const isEditing = Boolean(editingStoryId);
    saveButton.textContent = "Сохраняю…";
    error.textContent = "";
    try {
      const story = {
        date: $("#story-date").value,
        title: $("#story-title").value.trim(),
        story_text: $("#story-text").value.trim(),
        photo: photoInput.files[0] || null,
        remove_photo: removeExistingPhoto,
      };
      const saved = isEditing
        ? await updateStory(editingStoryId, story)
        : await createStory(story);
      resetEditor();
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
      try {
        await refresh();
        showToast(saved?.warning || (isEditing ? "История изменена." : "История сохранена."));
      } catch (loadError) {
        showToast(`История сохранена, но список не обновился: ${formatError(loadError)}`);
      }
    } catch (saveError) {
      error.textContent = formatError(saveError);
    } finally {
      saveButton.disabled = false;
      saveButton.textContent = editingStoryId ? "Сохранить изменения" : "Сохранить историю";
    }
  });

  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });
  $$("[data-close]").forEach(button => button.addEventListener("click", () => {
    if (button.dataset.close === "story-dialog") dialog.close();
  }));

  return refresh();
}
