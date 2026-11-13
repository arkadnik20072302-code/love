import { createThumbnailDataUrl } from "./photo-utils.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const MAX_PHOTO_BYTES = 20 * 1024 * 1024;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatMemoryDate(value, options = {}) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    ...options,
  }).format(new Date(`${value}T00:00:00`));
}

function memoryLoadError(error) {
  const detail = String(error.message || error);
  if (detail.includes("Could not find the table 'public.memories' in the schema cache")) {
    return "В Supabase ещё нет таблицы воспоминаний. Выполни файл supabase/memories_access.sql и обнови страницу.";
  }
  if (detail.includes("thumbnail_path") && /(schema cache|column|does not exist)/i.test(detail)) {
    return "В Supabase ещё не добавлена поддержка превью. Выполни файл supabase/memories_access.sql и обнови страницу.";
  }
  return `Не удалось загрузить фотографии: ${detail}`;
}

export function initMemoryGallery({ listMemories, uploadMemory, updateMemory, deleteMemory }) {
  const memoryGrid = $("#memory-grid");
  const monthFilter = $("#memory-month-filter");
  const state = { memories: [], memoryId: null, dayMemories: [], dayIndex: 0, month: "all" };
  let toastTimer;
  let uploadFiles = [];
  const thumbnailCache = new Map();
  const imageObserver = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
      entries.forEach(entry => {
        const image = entry.target;
        if (entry.isIntersecting) {
          if (image.dataset.photoSrc && image.dataset.loadedPhotoSrc !== image.dataset.photoSrc) {
            image.src = image.dataset.photoSrc;
            image.dataset.loadedPhotoSrc = image.dataset.photoSrc;
          }
        } else if (image.hasAttribute("src")) {
          image.removeAttribute("src");
          delete image.dataset.loadedPhotoSrc;
        }
      });
    }, { rootMargin: "160px" })
    : null;

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
  }

  function openDialog(id) {
    const dialog = $(`#${id}`);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function thumbnailFor(file) {
    const key = uploadFileKey(file);
    if (!thumbnailCache.has(key)) {
      thumbnailCache.set(key, createThumbnailDataUrl(file).catch(error => {
        thumbnailCache.delete(key);
        throw error;
      }));
    }
    return thumbnailCache.get(key);
  }

  function renderMonthFilter() {
    const selected = state.month;
    const months = [...new Set(state.memories.map(memory => memory.memory_date.slice(0, 7)))];
    monthFilter.innerHTML = `<option value="all">Все месяцы</option>${months.map(month => {
      const label = formatMemoryDate(`${month}-01`, { day: undefined });
      return `<option value="${month}">${escapeHtml(label)}</option>`;
    }).join("")}`;
    state.month = months.includes(selected) ? selected : "all";
    monthFilter.value = state.month;
  }

  function renderMemories() {
    if (imageObserver) {
      $$("#memory-grid img").forEach(image => imageObserver.unobserve(image));
      $$("#memories-cover-collage img").forEach(image => imageObserver.unobserve(image));
    }
    const sorted = [...state.memories].sort((a, b) =>
      b.memory_date.localeCompare(a.memory_date) || b.created_at.localeCompare(a.created_at),
    );
    const filtered = state.month === "all"
      ? sorted
      : sorted.filter(memory => memory.memory_date.startsWith(state.month));
    const byDate = new Map();
    filtered.forEach(memory => {
      if (!byDate.has(memory.memory_date)) byDate.set(memory.memory_date, []);
      byDate.get(memory.memory_date).push(memory);
    });

    memoryGrid.innerHTML = byDate.size
      ? [...byDate.entries()].map(([, memories]) => {
        const cards = memories.map(memory =>
          `<button class="memory-card" type="button" data-memory-id="${memory.id}" aria-label="Открыть фотографию, ${escapeHtml(formatMemoryDate(memory.memory_date))}">
            <img data-photo-src="${escapeHtml(memory.photo)}" alt="Фотография из воспоминания" loading="lazy" decoding="async" fetchpriority="low">
          </button>`
        ).join("");
        return `<div class="memory-day-group">
          <div class="memory-day-stack${memories.length > 2 ? " is-overflowing" : ""}" role="group" aria-label="Фотографии одного дня">${cards}</div>
          <div class="memory-day-date">${escapeHtml(formatMemoryDate(memories[0].memory_date))}</div>
        </div>`;
      }).join("")
      : `<div class="empty-state">${state.memories.length ? "За этот месяц фотографий нет." : "Здесь будут ваши общие моменты."}</div>`;
    $$(".memory-day-stack", memoryGrid).forEach(stack => {
      if (stack.classList.contains("is-overflowing")) {
        stack.scrollLeft = Math.max(0, (stack.scrollWidth - stack.clientWidth) / 2);
      }
    });
    $$(".memory-card img", memoryGrid).forEach(image => {
      if (imageObserver) imageObserver.observe(image);
      else image.src = image.dataset.photoSrc;
    });

    const collage = $("#memories-cover-collage");
    if (collage) {
      collage.innerHTML = sorted.length
        ? sorted.slice(0, 3).map((memory, index) =>
          `<figure class="cover-photo-card cover-photo-card-${index}">
            <img data-photo-src="${escapeHtml(memory.photo)}" alt="Фотография из вашего альбома" loading="lazy" decoding="async" fetchpriority="low">
          </figure>`,
        ).join("")
        : `<div class="cover-empty-card">
          <span>А + Д</span>
          <strong>И это только начало</strong>
          <small>ВАША ИСТОРИЯ · ГЛАВА 01</small>
        </div>`;
      $$("#memories-cover-collage img").forEach(image => {
        if (imageObserver) imageObserver.observe(image);
        else image.src = image.dataset.photoSrc;
      });
    }
  }

  function refresh() {
    return listMemories().then(memories => {
      state.memories = memories;
      renderMonthFilter();
      renderMemories();
    }).catch(error => {
      memoryGrid.innerHTML = `<div class="empty-state">${escapeHtml(memoryLoadError(error))}</div>`;
      throw error;
    });
  }

  function openMemory(memory) {
    state.dayMemories = state.memories
      .filter(item => item.memory_date === memory.memory_date)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    state.dayIndex = state.dayMemories.findIndex(item => item.id === memory.id);
    showMemory(memory);
    openDialog("memory-dialog");
  }

  function showMemory(memory, direction = "") {
    state.memoryId = memory.id;
    const photo = $("#memory-viewer-photo");
    photo.classList.remove("is-transitioning-next", "is-transitioning-previous");
    if (direction) {
      photo.addEventListener("load", () => {
        if (state.memoryId === memory.id) {
          photo.classList.add(`is-transitioning-${direction}`);
        }
      }, { once: true });
      photo.addEventListener("animationend", () => {
        photo.classList.remove("is-transitioning-next", "is-transitioning-previous");
      }, { once: true });
    }
    photo.src = memory.photo_original || memory.photo;
    $("#memory-date").value = memory.memory_date;
    $("#thought-her").value = memory.thought_her || "";
    $("#thought-him").value = memory.thought_him || "";
    $("#memory-error").textContent = "";
    $("#memory-photo-count").textContent = `${state.dayIndex + 1} / ${state.dayMemories.length}`;
    $("#memory-photo-count").hidden = state.dayMemories.length < 2;
    $("#memory-prev").hidden = state.dayMemories.length < 2;
    $("#memory-next").hidden = state.dayMemories.length < 2;
  }

  function navigateMemory(direction) {
    if (state.dayMemories.length < 2) return;
    state.dayIndex = (state.dayIndex + direction + state.dayMemories.length)
      % state.dayMemories.length;
    showMemory(state.dayMemories[state.dayIndex], direction > 0 ? "next" : "previous");
  }

  function thumbnailForDisplayedMemory() {
    const photo = $("#memory-viewer-photo");
    photo.removeAttribute("src");
    photo.classList.remove("is-transitioning-next", "is-transitioning-previous");
  }

  function uploadFileKey(file) {
    return `${file.name}:${file.size}:${file.lastModified}`;
  }

  function currentUploadValues() {
    return new Map($$(".memory-upload-item").map(item => [
      uploadFileKey(uploadFiles[Number(item.dataset.uploadIndex)]),
      {
        date: $("#memory-upload-date", item).value,
      },
    ]));
  }

  async function renderUploadFiles(preservedValues = new Map()) {
    const list = $("#memory-upload-list");
    list.innerHTML = uploadFiles.map((file, index) => {
      const values = preservedValues.get(uploadFileKey(file)) || {};
      return `<article class="memory-upload-item" data-upload-index="${index}">
        <img data-preview-index="${index}" alt="" loading="lazy">
        <div class="memory-upload-fields">
          <strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong>
          <label for="memory-upload-date-${index}">Дата этого момента</label>
          <input id="memory-upload-date-${index}" class="memory-upload-date" type="date" value="${escapeHtml(values.date || "")}" required>
        </div>
      </article>`;
    }).join("");
    $("#memory-upload-submit").disabled = uploadFiles.length === 0;
    for (let index = 0; index < uploadFiles.length; index += 1) {
      const file = uploadFiles[index];
      try {
        const preview = await thumbnailFor(file);
        if (uploadFiles[index] !== file) continue;
        const image = $(`img[data-preview-index="${index}"]`, list);
        if (image) image.src = preview;
      } catch (error) {
        $("#memory-upload-error").textContent = error.message;
      }
    }
  }

  memoryGrid.addEventListener("click", event => {
    const card = event.target.closest("[data-memory-id]");
    const memory = state.memories.find(item => item.id === card?.dataset.memoryId);
    if (memory) openMemory(memory);
  });

  monthFilter.addEventListener("change", () => {
    state.month = monthFilter.value;
    renderMemories();
  });

  $("#memory-add-open").addEventListener("click", () => {
    uploadFiles = [];
    thumbnailCache.clear();
    $("#memory-files").value = "";
    $("#memory-folder").value = "";
    $("#memory-upload-list").replaceChildren();
    $("#memory-upload-error").textContent = "";
    $("#memory-upload-submit").disabled = true;
    $("#memory-upload-submit").textContent = "Сохранить фотографии";
    openDialog("memories-upload-dialog");
  });

  function addSelectedFiles(files) {
    const preservedValues = currentUploadValues();
    const seen = new Set(uploadFiles.map(uploadFileKey));
    let unsupported = 0;
    let oversized = 0;
    let duplicates = 0;
    for (const file of files) {
      if (!IMAGE_TYPES.includes(file.type)) {
        unsupported += 1;
      } else if (file.size > MAX_PHOTO_BYTES) {
        oversized += 1;
      } else if (seen.has(uploadFileKey(file))) {
        duplicates += 1;
      } else {
        seen.add(uploadFileKey(file));
        uploadFiles.push(file);
      }
    }
    const messages = [];
    if (unsupported) messages.push(`${unsupported} неподдерживаемых файлов пропущено (нужны JPEG, PNG или WebP).`);
    if (oversized) messages.push(`${oversized} файлов больше 20 МБ пропущено.`);
    if (duplicates) messages.push(`${duplicates} повторных файлов пропущено.`);
    $("#memory-upload-error").textContent = messages.join(" ");
    renderUploadFiles(preservedValues);
  }

  ["#memory-files", "#memory-folder"].forEach(selector => {
    $(selector).addEventListener("change", event => {
      const selectedFiles = [...event.currentTarget.files];
      event.currentTarget.value = "";
      addSelectedFiles(selectedFiles);
    });
  });

  $("#memories-upload-form").addEventListener("submit", async event => {
    event.preventDefault();
    const items = $$(".memory-upload-item");
    const values = items.map(item => ({
      date: $(".memory-upload-date", item).value,
    }));
    const missingDate = values.some(item => !item.date);
    if (missingDate) {
      $("#memory-upload-error").textContent = "Укажи дату для каждой фотографии.";
      items.forEach((item, index) => item.classList.toggle("is-invalid", !values[index].date));
      return;
    }
    const submit = $("#memory-upload-submit");
    submit.disabled = true;
    $("#memory-upload-error").textContent = "";
    let uploaded = 0;
    const errors = [];
    const failedFiles = [];
    const failedValues = [];
    for (let index = 0; index < uploadFiles.length; index += 1) {
      const file = uploadFiles[index];
      submit.textContent = `Загружаю ${index + 1} из ${uploadFiles.length}…`;
      try {
        await uploadMemory(values[index].date, file, await thumbnailFor(file));
        uploaded += 1;
        thumbnailCache.delete(uploadFileKey(file));
      } catch (error) {
        errors.push(`${file.name}: ${error.message}`);
        failedFiles.push(file);
        failedValues.push(values[index]);
      }
    }
    submit.textContent = "Сохранить фотографии";
    const failedByFile = new Map(failedFiles.map((file, index) => [
      uploadFileKey(file),
      failedValues[index],
    ]));
    uploadFiles = failedFiles;
    renderUploadFiles(failedByFile);
    try {
      await refresh();
    } catch (error) {
      $("#memory-upload-error").textContent =
        `Не удалось обновить альбом после загрузки: ${error.message}`;
      return;
    }
    if (errors.length) {
      $("#memory-upload-error").textContent =
        `${uploaded} фото сохранено. Остались только неудачные загрузки: ${errors.join("; ")}`;
    } else {
      closeDialog($("#memories-upload-dialog"));
      showToast(`Добавлено фотографий: ${uploaded}`);
    }
  });

  $("#memory-save").addEventListener("click", async () => {
    const memory = state.memories.find(item => item.id === state.memoryId);
    if (!memory) return;
    const memoryDate = $("#memory-date").value;
    if (!memoryDate) {
      $("#memory-error").textContent = "Укажи дату воспоминания.";
      return;
    }
    const saveButton = $("#memory-save");
    saveButton.disabled = true;
    try {
      await updateMemory(memory.id, {
        memory_date: memoryDate,
        thought_her: $("#thought-her").value.trim(),
        thought_him: $("#thought-him").value.trim(),
      });
      await refresh();
      closeDialog($("#memory-dialog"));
      showToast("Дата и мысли сохранены.");
    } catch (error) {
      $("#memory-error").textContent = error.message;
    } finally {
      saveButton.disabled = false;
    }
  });

  $("#memory-delete").addEventListener("click", async () => {
    const memory = state.memories.find(item => item.id === state.memoryId);
    if (!memory || !window.confirm("Удалить это фото и мысли к нему?")) return;
    const deleteButton = $("#memory-delete");
    deleteButton.disabled = true;
    try {
      await deleteMemory(memory.id);
      await refresh();
      closeDialog($("#memory-dialog"));
      showToast("Фото и его история удалены.");
    } catch (error) {
      $("#memory-error").textContent = error.message;
      await refresh();
    } finally {
      deleteButton.disabled = false;
    }
  });

  $$("[data-close]").forEach(button => button.addEventListener("click", () =>
    closeDialog($(`#${button.dataset.close}`))));
  $("#memory-dialog").addEventListener("close", thumbnailForDisplayedMemory);
  $("#memory-prev").addEventListener("click", () => navigateMemory(-1));
  $("#memory-next").addEventListener("click", () => navigateMemory(1));
  $("#memory-dialog").addEventListener("keydown", event => {
    if (event.target.matches("input, textarea, select, [contenteditable='true']")) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      navigateMemory(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      navigateMemory(1);
    }
  });
  $$(".modal").forEach(dialog => dialog.addEventListener("click", event => {
    if (event.target === dialog) closeDialog(dialog);
  }));
  return refresh();
}
