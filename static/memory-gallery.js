const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatMemoryDate(value) {
  if (!value) return "";
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

function memoryLoadError(error) {
  const detail = String(error.message || error);
  if (detail.includes("Could not find the table 'public.memories' in the schema cache")) {
    return "В Supabase ещё нет таблицы public.memories. Открой SQL Editor, выполни файл supabase/memories_access.sql и обнови страницу.";
  }
  return `Не удалось загрузить фотографии: ${detail}`;
}

export function initMemoryGallery({ listMemories, uploadMemory, updateMemory }) {
  const memoryGrid = $("#memory-grid");
  const state = { memories: [], memoryId: null };
  let toastTimer;
  let uploadFiles = [];

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

  function renderMemories() {
    const sorted = [...state.memories].sort((a, b) =>
      b.memory_date.localeCompare(a.memory_date) || b.created_at.localeCompare(a.created_at),
    );
    memoryGrid.innerHTML = sorted.length
      ? sorted.map(memory => {
        const thought = memory.thought_her || memory.thought_him || "";
        return `<button class="memory-card" type="button" data-memory-id="${memory.id}" aria-label="Открыть воспоминание от ${escapeHtml(formatMemoryDate(memory.memory_date))}">
          <img src="${escapeHtml(memory.photo)}" alt="Фотография от ${escapeHtml(formatMemoryDate(memory.memory_date))}" loading="lazy">
          <span class="memory-card-copy">
            <strong class="memory-card-date">${escapeHtml(formatMemoryDate(memory.memory_date))}</strong>
            ${thought ? `<span class="memory-card-thought">${escapeHtml(thought)}</span>` : ""}
          </span>
        </button>`;
      }).join("")
      : `<div class="empty-state">Здесь будут ваши общие моменты ✿</div>`;

    const collage = $("#memories-cover-collage");
    if (collage && sorted.length) {
      collage.innerHTML = sorted.slice(0, 3).map((memory, index) =>
        `<figure class="cover-photo-card cover-photo-card-${index}">
          <img src="${escapeHtml(memory.photo)}" alt="Ваш момент от ${escapeHtml(formatMemoryDate(memory.memory_date))}">
          <figcaption>${escapeHtml(formatMemoryDate(memory.memory_date))}</figcaption>
        </figure>`,
      ).join("");
    }
  }

  function refresh() {
    return listMemories().then(memories => {
      state.memories = memories;
      renderMemories();
    }).catch(error => {
      memoryGrid.innerHTML = `<div class="empty-state">${escapeHtml(memoryLoadError(error))}</div>`;
      throw error;
    });
  }

  function openMemory(memory) {
    state.memoryId = memory.id;
    $("#memory-viewer-photo").src = memory.photo;
    $("#memory-date").value = memory.memory_date;
    $("#thought-her").value = memory.thought_her || "";
    $("#thought-him").value = memory.thought_him || "";
    $("#memory-error").textContent = "";
    openDialog("memory-dialog");
  }

  function uploadFileKey(file) {
    return `${file.name}:${file.size}:${file.lastModified}`;
  }

  function renderUploadFiles(preservedDates = new Map()) {
    const list = $("#memory-upload-list");
    list.innerHTML = uploadFiles.map((file, index) => {
      return `<article class="memory-upload-item" data-upload-index="${index}">
        <img data-preview-index="${index}" alt="" loading="lazy">
        <div>
          <strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong>
          <label for="memory-upload-date-${index}">Дата этого момента</label>
          <input id="memory-upload-date-${index}" type="date" value="${escapeHtml(preservedDates.get(uploadFileKey(file)) || "")}" required>
        </div>
      </article>`;
    }).join("");
    $("#memory-upload-submit").disabled = uploadFiles.length === 0;
    uploadFiles.forEach((file, index) => {
      readAsDataUrl(file).then(preview => {
        if (uploadFiles[index] !== file) return;
        const image = $(`img[data-preview-index="${index}"]`, list);
        if (image) image.src = preview;
      }).catch(error => {
        $("#memory-upload-error").textContent = error.message;
      });
    });
  }

  memoryGrid.addEventListener("click", event => {
    const card = event.target.closest("[data-memory-id]");
    const memory = state.memories.find(item => item.id === card?.dataset.memoryId);
    if (memory) openMemory(memory);
  });

  $("#memory-add-open").addEventListener("click", () => {
    uploadFiles = [];
    $("#memory-files").value = "";
    $("#memory-folder").value = "";
    $("#memory-upload-list").replaceChildren();
    $("#memory-upload-error").textContent = "";
    $("#memory-upload-submit").disabled = true;
    openDialog("memories-upload-dialog");
  });

  function addSelectedFiles(files) {
    const preservedDates = new Map(uploadFiles.map((file, index) => [
      uploadFileKey(file),
      $(`#memory-upload-date-${index}`)?.value || "",
    ]));
    const seen = new Set(uploadFiles.map(uploadFileKey));
    let unsupported = 0;
    let oversized = 0;
    let duplicates = 0;
    for (const file of files) {
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        unsupported += 1;
      } else if (file.size > 3 * 1024 * 1024) {
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
    if (oversized) messages.push(`${oversized} файлов больше 3 МБ пропущено.`);
    if (duplicates) messages.push(`${duplicates} повторных файлов пропущено.`);
    $("#memory-upload-error").textContent = messages.join(" ");
    renderUploadFiles(preservedDates);
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
    const missingDate = items.some(item => !$("input", item).value);
    if (missingDate) {
      $("#memory-upload-error").textContent = "Укажи дату для каждой фотографии.";
      items.forEach(item => item.classList.toggle("is-invalid", !$("input", item).value));
      return;
    }
    const submit = $("#memory-upload-submit");
    submit.disabled = true;
    $("#memory-upload-error").textContent = "";
    let uploaded = 0;
    const errors = [];
    const failedFiles = [];
    const failedDates = [];
    for (let index = 0; index < uploadFiles.length; index += 1) {
      const file = uploadFiles[index];
      const memoryDate = $("input", items[index]).value;
      submit.textContent = `Загружаю ${index + 1} из ${uploadFiles.length}…`;
      try {
        await uploadMemory(memoryDate, file);
        uploaded += 1;
      } catch (error) {
        errors.push(`${file.name}: ${error.message}`);
        failedFiles.push(file);
        failedDates.push(memoryDate);
      }
    }
    submit.textContent = "Сохранить фотографии";
    uploadFiles = failedFiles;
    renderUploadFiles();
    uploadFiles.forEach((_file, index) => {
      $(`#memory-upload-date-${index}`).value = failedDates[index];
    });
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
      showToast(`Добавлено фотографий: ${uploaded} ♡`);
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
      showToast("Воспоминание сохранено ♡");
    } catch (error) {
      $("#memory-error").textContent = error.message;
    } finally {
      saveButton.disabled = false;
    }
  });

  $$("[data-close]").forEach(button => button.addEventListener("click", () =>
    closeDialog($(`#${button.dataset.close}`))));
  $$(".modal").forEach(dialog => dialog.addEventListener("click", event => {
    if (event.target === dialog) closeDialog(dialog);
  }));
  return refresh();
}
