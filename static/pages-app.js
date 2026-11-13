import {
  currentUser,
  deleteRow,
  insertRow,
  isWishlistMember,
  selectRows,
  signOut,
  updateRow,
} from "./pages-client.js?v=memory-stories";

const state = {
  wishes: [],
  settings: { together_since: "", demob_date: "" },
  filter: "all",
  scheduleId: null,
  photoId: null,
};
const PHOTO_PATTERN = /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/;
const ICONS = {
  undo: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-2"></path></svg>',
  remove: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"></path></svg>',
  arrow: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"></path></svg>',
  check: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5L20 7"></path></svg>',
  photo: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="m21 15-5-5L5 20"></path></svg>',
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const grid = $("#wish-grid");
let toastTimer;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function localDateTime() {
  const now = new Date();
  return {
    date: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    time: `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`,
  };
}

function daysBetween(start, end) {
  return Math.floor((new Date(`${end}T00:00:00`) - new Date(`${start}T00:00:00`)) / 86_400_000);
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" })
    .format(new Date(`${value}T00:00:00`));
}

function formatPlannedDate(wish) {
  if (!wish.planned_for) return "";
  const date = formatDate(wish.planned_for);
  return wish.planned_time ? `${date} в ${wish.planned_time.slice(0, 5)}` : date;
}

function renderCounters() {
  const today = localDateTime().date;
  const { together_since: together, demob_date: demob } = state.settings;
  const togetherDays = together ? daysBetween(together, today) : -1;
  $("#together-count").textContent = togetherDays >= 0
    ? new Intl.NumberFormat("ru-RU").format(togetherDays + 1)
    : "—";
  $("#together-note").textContent = together ? "дней нашей истории" : "добавьте дату начала";
  const remaining = demob ? daysBetween(today, demob) : null;
  $("#demob-count").textContent = remaining === null ? "—"
    : remaining > 0 ? new Intl.NumberFormat("ru-RU").format(remaining)
      : remaining === 0 ? "Сегодня!" : "Уже дома ♡";
  $("#demob-note").textContent = !demob ? "добавьте дату дембеля"
    : remaining > 0 ? "дней до нового этапа" : "время для вашей главы";
}

function wishCard(wish) {
  const done = wish.status === "done";
  const descriptionDate = done
    ? (wish.completed_at
      ? `Сбылось ${formatDate(wish.completed_at)}${wish.completed_time ? ` в ${wish.completed_time.slice(0, 5)}` : ""}`
      : "Уже в вашей истории")
    : wish.planned_for ? `Запланировано на ${formatPlannedDate(wish)}` : "Когда-нибудь совсем скоро";
  const safePhoto = wish.photo && PHOTO_PATTERN.test(wish.photo) ? wish.photo : "";
  const safeTitle = escapeHtml(wish.title);
  return `
    <article class="wish-card ${done ? "is-done" : ""}">
      <div class="wish-top">
        <span class="category-tag" data-category="${escapeHtml(wish.category)}">${escapeHtml(wish.category)}</span>
        <div class="wish-actions">
          ${done ? `<button class="wish-action" type="button" data-action="undo" data-id="${wish.id}" aria-label="Вернуть в планы" title="Вернуть в планы">${ICONS.undo}</button>` : `<button class="wish-action" type="button" data-action="delete" data-id="${wish.id}" aria-label="Удалить идею" title="Удалить">${ICONS.remove}</button>`}
        </div>
      </div>
      <h3 class="wish-title">${safeTitle}</h3>
      ${safePhoto ? `<img class="wish-photo" src="${safePhoto}" alt="Фотография: ${safeTitle}">` : ""}
      <div class="wish-bottom">
        <span class="wish-date">${escapeHtml(descriptionDate)}</span>
        ${done
          ? `<button class="wish-cta" type="button" data-action="photo" data-id="${wish.id}">${ICONS.photo}${safePhoto ? "Поменять фото" : "Фото"}</button>`
          : `<button class="wish-cta" type="button" data-action="schedule" data-id="${wish.id}">Запланировать ${ICONS.arrow}</button><button class="wish-cta" type="button" data-action="done" data-id="${wish.id}">Сбылось ${ICONS.check}</button>`}
      </div>
    </article>`;
}

function renderWishes() {
  const counts = {
    all: state.wishes.length,
    planned: state.wishes.filter(wish => wish.status !== "done").length,
    done: state.wishes.filter(wish => wish.status === "done").length,
  };
  $("#all-count").textContent = counts.all;
  $("#planned-count").textContent = counts.planned;
  $("#done-count").textContent = counts.done;
  const shown = state.wishes.filter(wish =>
    state.filter === "all" || (state.filter === "done" ? wish.status === "done" : wish.status !== "done"),
  );
  grid.innerHTML = shown.length
    ? shown.map(wishCard).join("")
    : `<div class="empty-state">${state.filter === "done" ? "Пока ни одно приключение не отмечено как сбывшееся — всё впереди." : "Здесь скоро появится ваша первая идея."}</div>`;
}

async function refresh() {
  const [settings, wishes] = await Promise.all([
    selectRows("settings", "together_since,demob_date", { id: "eq.1" }),
    selectRows(
      "wishes",
      "id,title,category,status,created_at,planned_for,planned_time,completed_at,completed_time,photo",
      { order: "status.desc,created_at.desc" },
    ),
  ]);
  const row = settings[0] || {};
  state.settings = {
    together_since: row.together_since || "",
    demob_date: row.demob_date || "",
  };
  state.wishes = wishes.map(wish => ({
    ...wish,
    planned_for: wish.planned_for || "",
    planned_time: (wish.planned_time || "").slice(0, 5),
    completed_at: wish.completed_at || "",
    completed_time: (wish.completed_time || "").slice(0, 5),
    photo: wish.photo || "",
  }));
  renderCounters();
  renderWishes();
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
  await refresh();
}

$("#settings-open").addEventListener("click", () => {
  $("#together-date").value = state.settings.together_since || "";
  $("#demob-date").value = state.settings.demob_date || "";
  $("#settings-error").textContent = "";
  openDialog("settings-dialog");
});

$("#add-open").addEventListener("click", () => {
  $("#wish-error").textContent = "";
  $("#wish-form").reset();
  openDialog("wish-dialog");
  setTimeout(() => $("#wish-title").focus(), 50);
});

$$("[data-close]").forEach(button => button.addEventListener("click", () =>
  closeDialog($(`#${button.dataset.close}`))));
$$(".modal").forEach(dialog => dialog.addEventListener("click", event => {
  if (event.target === dialog) closeDialog(dialog);
}));

$$(".filter-chip").forEach(button => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  $$(".filter-chip").forEach(chip => chip.classList.toggle("is-active", chip === button));
  renderWishes();
}));

$("#wish-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const title = String(form.get("title") || "").trim();
  const category = String(form.get("category") || "Разное").trim();
  if (!title || title.length > 120 || category.length > 30) {
    $("#wish-error").textContent = "Проверьте название и категорию.";
    return;
  }
  try {
    await insertRow("wishes", { title, category: category || "Разное" });
    await refresh();
    closeDialog($("#wish-dialog"));
    showToast("Идея сохранена. Однажды это точно случится ♡");
  } catch (error) {
    $("#wish-error").textContent = error.message;
  }
});

$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const togetherSince = form.get("together_since") || null;
  const demobDate = form.get("demob_date") || null;
  try {
    await updateRow("settings", "1", {
      together_since: togetherSince,
      demob_date: demobDate,
    });
    await refresh();
    closeDialog($("#settings-dialog"));
    showToast("Ваши важные даты сохранены ♡");
  } catch (error) {
    $("#settings-error").textContent = error.message;
  }
});

$("#schedule-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const plannedFor = form.get("planned_for") || null;
  const plannedTime = form.get("planned_time") || null;
  if (plannedTime && !plannedFor) {
    $("#schedule-error").textContent = "Чтобы указать время, сначала выберите дату.";
    return;
  }
  const wish = state.wishes.find(item => item.id === state.scheduleId);
  if (!wish) return;
  try {
    await updateRow("wishes", wish.id, {
      planned_for: plannedFor,
      planned_time: plannedTime,
    });
    await refresh();
    closeDialog($("#schedule-dialog"));
    showToast("Записала в планы — пусть получится!");
    if ("Notification" in window && Notification.permission === "granted") {
      const plannedDate = plannedFor
        ? `${formatDate(plannedFor)}${plannedTime ? ` в ${plannedTime}` : ""}`
        : "";
      new Notification("Ваше приключение запланировано", {
        body: `${wish.title}${plannedDate ? ` — ${plannedDate}` : ""}`,
      });
    }
  } catch (error) {
    $("#schedule-error").textContent = error.message;
  }
});

grid.addEventListener("click", async event => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const wish = state.wishes.find(item => item.id === button.dataset.id);
  if (!wish) return;
  try {
    if (button.dataset.action === "schedule") {
      state.scheduleId = wish.id;
      $("#schedule-title").textContent = `«${wish.title}»`;
      $("#schedule-date").value = wish.planned_for || "";
      $("#schedule-time").value = wish.planned_time || "";
      $("#schedule-error").textContent = "";
      openDialog("schedule-dialog");
      if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
      }
      return;
    }
    if (button.dataset.action === "photo") {
      state.photoId = wish.id;
      $("#photo-input").click();
      return;
    }
    if (button.dataset.action === "delete") {
      if (!window.confirm(`Удалить идею «${wish.title}»?`)) return;
      await deleteRow("wishes", wish.id);
      showToast("Идея удалена");
    } else {
      const done = button.dataset.action === "done";
      const changes = done
        ? { status: "done", completed_at: localDateTime().date, completed_time: localDateTime().time }
        : { status: "planned", completed_at: null, completed_time: null, photo: "" };
      await updateRow("wishes", wish.id, changes);
      showToast(done
        ? "Приключение отмечено. Telegram-уведомление обработается автоматически, если бот настроен."
        : "Идея снова в планах");
    }
    await refresh();
  } catch (error) {
    showToast(error.message);
  }
});

$("#photo-input").addEventListener("change", async event => {
  const file = event.target.files[0];
  const wishId = state.photoId;
  event.target.value = "";
  if (!file || !wishId) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    showToast("Поддерживаются фотографии JPEG, PNG и WebP.");
    return;
  }
  if (file.size > 3 * 1024 * 1024) {
    showToast("Фотография должна быть меньше 3 МБ");
    return;
  }
  try {
    const photo = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Не удалось прочитать фотографию."));
      reader.readAsDataURL(file);
    });
    await updateRow("wishes", wishId, { photo });
    await refresh();
    showToast("Фотография добавлена в вашу историю ♡");
  } catch (error) {
    showToast(error.message);
  }
});

$("#logout").addEventListener("click", async () => {
  await signOut();
  location.replace("./login.html");
});

const heroPhoto = $(".hero-photo");
const hideUnavailableHeroPhoto = () => {
  heroPhoto.closest(".hero-photo-art").classList.add("photo-missing");
};
heroPhoto.addEventListener("error", hideUnavailableHeroPhoto, { once: true });
if (heroPhoto.complete && !heroPhoto.naturalWidth) hideUnavailableHeroPhoto();

start().catch(error => {
  grid.innerHTML = `<div class="empty-state">Не удалось загрузить планы: ${escapeHtml(error.message)}<br>Проверьте настройки Supabase и доступ.</div>`;
});
