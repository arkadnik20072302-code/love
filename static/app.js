const state = { wishes: [], settings: { together_since: "", demob_date: "" }, filter: "all", scheduleId: null, photoId: null };
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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 401 && location.pathname !== "/login") {
    location.replace("/login");
    throw new Error("Сессия истекла. Войдите снова.");
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос.");
  return payload;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function daysBetween(start, end) {
  const startDate = new Date(`${start}T00:00:00`);
  const endDate = new Date(`${end}T00:00:00`);
  return Math.floor((endDate - startDate) / 86_400_000);
}

function renderCounters() {
  const today = new Date();
  const localToday = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const together = state.settings.together_since;
  const demob = state.settings.demob_date;
  $("#together-count").textContent = together && daysBetween(together, localToday) >= 0
    ? new Intl.NumberFormat("ru-RU").format(daysBetween(together, localToday) + 1)
    : "—";
  $("#together-note").textContent = together ? "дней нашей истории" : "добавьте дату начала";
  const remaining = demob ? daysBetween(localToday, demob) : null;
  $("#demob-count").textContent = remaining === null ? "—" : remaining > 0
    ? new Intl.NumberFormat("ru-RU").format(remaining)
    : remaining === 0 ? "Сегодня!" : "Уже дома ♡";
  $("#demob-note").textContent = !demob ? "добавьте дату дембеля" : remaining > 0 ? "дней до нового этапа" : "время для вашей главы";
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" }).format(new Date(`${value}T00:00:00`));
}

function formatPlannedDate(wish) {
  if (!wish.planned_for) return "";
  const date = formatDate(wish.planned_for);
  return wish.planned_time ? `${date} в ${wish.planned_time.slice(0, 5)}` : date;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
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
  const data = await api("/api/state");
  state.wishes = data.wishes;
  state.settings = data.settings;
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

$$("[data-close]").forEach(button => button.addEventListener("click", () => closeDialog($(`#${button.dataset.close}`))));
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
  try {
    await api("/api/wishes", { method: "POST", body: JSON.stringify({ title: form.get("title"), category: form.get("category") }) });
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
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({
      together_since: form.get("together_since"),
      demob_date: form.get("demob_date"),
    }) });
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
  const wish = state.wishes.find(item => item.id === state.scheduleId);
  if (!wish) return;
  try {
    await api(`/api/wishes/${wish.id}`, { method: "PUT", body: JSON.stringify({
      planned_for: form.get("planned_for"),
      planned_time: form.get("planned_time"),
    }) });
    await refresh();
    closeDialog($("#schedule-dialog"));
    showToast("Записала в планы — пусть получится!");
    if ("Notification" in window && Notification.permission === "granted") {
      const plannedDate = form.get("planned_for")
        ? `${formatDate(form.get("planned_for"))}${form.get("planned_time") ? ` в ${form.get("planned_time")}` : ""}`
        : "";
      new Notification("Ваше приключение запланировано", { body: `${wish.title}${plannedDate ? ` — ${plannedDate}` : ""}` });
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
      await api(`/api/wishes/${wish.id}`, { method: "DELETE" });
      showToast("Идея удалена");
    } else {
      const done = button.dataset.action === "done";
      const result = await api(`/api/wishes/${wish.id}`, { method: "PUT", body: JSON.stringify({
        status: done ? "done" : "planned",
        ...(!done ? { photo: "" } : {}),
      }) });
      if (!done) {
        showToast("Идея снова в планах");
      } else if (result.telegram_notification === "sent") {
        showToast("Ещё одно ваше «мы это сделали»! ✓ В Telegram отправлено уведомление.");
      } else if (result.telegram_notification === "partial") {
        showToast("Приключение отмечено, но уведомление дошло не всем. Проверьте запуск бота.");
      } else if (result.telegram_notification === "failed") {
        showToast("Приключение отмечено, но Telegram не доставил уведомление. Проверьте бота.");
      } else {
        showToast("Приключение отмечено. Telegram-бот не настроен.");
      }
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
    await api(`/api/wishes/${wishId}`, { method: "PUT", body: JSON.stringify({ photo }) });
    await refresh();
    showToast("Фотография добавлена в вашу историю ♡");
  } catch (error) {
    showToast(error.message);
  }
});

refresh().catch(error => {
  grid.innerHTML = `<div class="empty-state">Не удалось загрузить планы: ${escapeHtml(error.message)}<br>Обновите страницу через минуту.</div>`;
});

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
    showToast(error.message);
  }
});

const heroPhoto = $(".hero-photo");
const hideUnavailableHeroPhoto = () => {
  heroPhoto.closest(".hero-photo-art").classList.add("photo-missing");
};
heroPhoto.addEventListener("error", hideUnavailableHeroPhoto, { once: true });
if (heroPhoto.complete && !heroPhoto.naturalWidth) {
  hideUnavailableHeroPhoto();
}
