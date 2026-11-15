const YANDEX_HOSTS = new Set([
  "music.yandex.ru",
  "music.yandex.com",
  "music.yandex.by",
  "music.yandex.kz",
  "music.yandex.uz",
]);

const $ = (selector, root = document) => root.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

export function parseYandexTrackUrl(value) {
  let url;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("Вставь ссылку на трек из Яндекс Музыки.");
  }
  if (url.protocol !== "https:" || !YANDEX_HOSTS.has(url.hostname)) {
    throw new Error("Нужна HTTPS-ссылка с сайта Яндекс Музыки.");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  let trackId = "";
  let albumId = "";
  if (parts[0] === "album" && parts[2] === "track") {
    albumId = parts[1] || "";
    trackId = parts[3] || "";
  } else if (parts[0] === "track") {
    trackId = parts[1] || "";
  }
  if (!/^\d+$/.test(trackId) || (albumId && !/^\d+$/.test(albumId))) {
    throw new Error("Ссылка должна вести на конкретный трек, а не на альбом или плейлист.");
  }
  return {
    track_id: trackId,
    album_id: albumId,
    yandex_url: albumId
      ? `https://music.yandex.ru/album/${albumId}/track/${trackId}`
      : `https://music.yandex.ru/track/${trackId}`,
  };
}

function embedUrl(track) {
  const album = track.album_id ? `/${encodeURIComponent(track.album_id)}` : "";
  return `https://music.yandex.ru/iframe/#track/${encodeURIComponent(track.track_id)}${album}`;
}

export function initMusicPlayer({ listTracks, createTrack, deleteTrack, formatError = error => error.message }) {
  const root = document.createElement("section");
  root.className = "music-player is-collapsed";
  root.innerHTML = `
    <div class="music-player__collapsed">
      <span class="music-player__note" aria-hidden="true">♫</span>
      <div class="music-player__mini-copy">
        <strong class="music-player__mini-title">Наша музыка</strong>
        <span>Песни-память о нас двоих</span>
      </div>
      <button class="music-player__expand" type="button" aria-label="Открыть нашу музыку">Развернуть</button>
      <button class="music-player__stop" type="button" aria-label="Остановить музыку" title="Остановить музыку" hidden>■</button>
    </div>
    <div class="music-player__expanded" aria-hidden="true">
      <header class="music-player__header">
        <div>
          <div class="music-player__eyebrow">НАШИ ОБЩИЕ МОМЕНТЫ</div>
          <h2>Песни, которые хранятся в памяти</h2>
        </div>
        <button class="music-player__collapse" type="button" aria-label="Свернуть плеер">−</button>
      </header>
      <p class="music-player__intro">Музыкальные ассоциации о нас двоих — как память о хороших моментах вместе.</p>
      <div class="music-player__now" aria-live="polite">
        <div class="music-player__art" aria-hidden="true">♫</div>
        <div class="music-player__now-copy">
          <strong class="music-player__active-title">Выбери песню из нашей памяти</strong>
          <span class="music-player__now-caption">Проигрыватель Яндекс Музыки</span>
        </div>
        <div class="music-player__now-controls">
          <button class="music-player__skip music-player__previous" type="button" aria-label="Предыдущий трек" title="Предыдущий трек" disabled>
            <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5v14m14-14L8 12l11 7z"></path></svg>
          </button>
          <button class="music-player__skip music-player__next" type="button" aria-label="Следующий трек" title="Следующий трек" disabled>
            <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19 5v14M5 5l11 7-11 7z"></path></svg>
          </button>
        </div>
        <button class="music-player__stop-inline" type="button" aria-label="Остановить музыку" title="Остановить музыку" hidden>■</button>
      </div>
      <div class="music-player__embed-wrap">
        <iframe class="music-player__embed" title="Проигрыватель Яндекс Музыки"
          allow="autoplay; encrypted-media; picture-in-picture" referrerpolicy="strict-origin-when-cross-origin"></iframe>
        <div class="music-player__embed-empty">Добавь трек и выбери его в плейлисте ниже</div>
      </div>
      <form class="music-player__form">
        <label for="music-url">Ссылка на песню в Яндекс Музыке</label>
        <input id="music-url" name="url" type="url" inputmode="url" autocomplete="url" placeholder="https://music.yandex.ru/album/…/track/…" required>
        <label for="music-title">Наша подпись <span>(необязательно)</span></label>
        <input id="music-title" name="title" type="text" maxlength="120" placeholder="Например, песня нашего лета">
        <div class="music-player__error" role="alert"></div>
        <button class="music-player__add" type="submit">Добавить в нашу память <span aria-hidden="true">＋</span></button>
      </form>
      <div class="music-player__playlist-heading">
        <h3>Плейлист нашей памяти</h3>
        <span class="music-player__track-count">0 песен</span>
      </div>
      <ol class="music-player__playlist"></ol>
    </div>`;
  document.body.append(root);

  const iframe = $(".music-player__embed", root);
  const error = $(".music-player__error", root);
  const playlist = $(".music-player__playlist", root);
  const form = $(".music-player__form", root);
  const addButton = $(".music-player__add", root);
  const state = { tracks: [], activeId: null, expanded: false, stopped: true };

  function showError(message) {
    error.textContent = message;
  }

  function setExpanded(expanded) {
    state.expanded = expanded;
    root.classList.toggle("is-expanded", expanded);
    root.classList.toggle("is-collapsed", !expanded);
    $(".music-player__expanded", root).setAttribute("aria-hidden", String(!expanded));
    $(".music-player__expand", root).setAttribute("aria-expanded", String(expanded));
  }

  function updateNavigation() {
    const disabled = state.tracks.length < 2;
    $(".music-player__previous", root).disabled = disabled;
    $(".music-player__next", root).disabled = disabled;
  }

  function setActive(track, load = true) {
    state.activeId = track?.id ?? null;
    if (track && load) {
      iframe.src = embedUrl(track);
      state.stopped = false;
    }
    const active = state.tracks.find(item => item.id === state.activeId);
    $(".music-player__active-title", root).textContent =
      active?.title || "Выбери песню из нашей памяти";
    $(".music-player__mini-title", root).textContent =
      active?.title || "Наша музыка";
    $(".music-player__now-caption", root).textContent =
      active ? `Воспоминание через музыку · ${active.yandex_url ? "Яндекс Музыка" : ""}` : "Проигрыватель Яндекс Музыки";
    $(".music-player__art", root).classList.toggle("has-track", Boolean(active));
    $(".music-player__stop", root).hidden = !active;
    $(".music-player__stop-inline", root).hidden = !active;
    $(".music-player__embed-empty", root).hidden = Boolean(active);
    $(".music-player__embed-wrap", root).classList.toggle("is-empty", !active);
    updateNavigation();
    playlist.querySelectorAll("[data-track-id]").forEach(row => {
      const selected = row.dataset.trackId === state.activeId;
      row.classList.toggle("is-active", selected);
      row.querySelector(".music-player__track-play").setAttribute("aria-pressed", String(selected));
    });
  }

  function renderTracks() {
    playlist.innerHTML = state.tracks.map(track => `
      <li class="music-player__track${track.id === state.activeId ? " is-active" : ""}" data-track-id="${escapeHtml(track.id)}">
        <button class="music-player__track-play" type="button" aria-label="Слушать «${escapeHtml(track.title)}»" aria-pressed="${track.id === state.activeId}">
          <span class="music-player__track-icon" aria-hidden="true">${track.id === state.activeId && !state.stopped ? "♫" : "▶"}</span>
          <span class="music-player__track-copy"><strong>${escapeHtml(track.title)}</strong><small>Яндекс Музыка · песня о нас</small></span>
        </button>
        <a class="music-player__source" href="${escapeHtml(track.yandex_url)}" target="_blank" rel="noopener noreferrer" aria-label="Открыть «${escapeHtml(track.title)}» в Яндекс Музыке">↗</a>
        <button class="music-player__remove" type="button" title="Удалить из плейлиста" aria-label="Удалить «${escapeHtml(track.title)}» из плейлиста">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16m-10 4v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"></path></svg>
        </button>
      </li>`).join("");
    $(".music-player__track-count", root).textContent = `${state.tracks.length} ${trackWord(state.tracks.length)}`;
    setActive(state.tracks.find(track => track.id === state.activeId), false);
  }

  function trackWord(count) {
    const remainder = count % 10;
    const lastTwo = count % 100;
    if (remainder === 1 && lastTwo !== 11) return "песня";
    if (remainder >= 2 && remainder <= 4 && (lastTwo < 12 || lastTwo > 14)) return "песни";
    return "песен";
  }

  async function refresh() {
    try {
      state.tracks = await listTracks();
      if (!state.tracks.some(track => track.id === state.activeId)) {
        state.activeId = null;
        state.stopped = true;
        iframe.removeAttribute("src");
      }
      renderTracks();
    } catch (loadError) {
      showError(formatError(loadError));
      throw loadError;
    }
  }

  $(".music-player__expand", root).addEventListener("click", () => setExpanded(true));
  $(".music-player__collapse", root).addEventListener("click", () => setExpanded(false));
  $(".music-player__stop", root).addEventListener("click", stopPlayback);
  $(".music-player__stop-inline", root).addEventListener("click", stopPlayback);

  function stopPlayback() {
    iframe.removeAttribute("src");
    state.stopped = true;
    const active = state.tracks.find(track => track.id === state.activeId);
    setActive(active, false);
    renderTracks();
  }

  function moveTrack(direction) {
    if (state.tracks.length < 2) return;
    const currentIndex = state.tracks.findIndex(track => track.id === state.activeId);
    const nextIndex = currentIndex === -1
      ? direction > 0 ? 0 : state.tracks.length - 1
      : (currentIndex + direction + state.tracks.length) % state.tracks.length;
    setActive(state.tracks[nextIndex]);
    renderTracks();
  }

  $(".music-player__previous", root).addEventListener("click", () => moveTrack(-1));
  $(".music-player__next", root).addEventListener("click", () => moveTrack(1));

  playlist.addEventListener("click", async event => {
    const row = event.target.closest("[data-track-id]");
    if (!row) return;
    const track = state.tracks.find(item => item.id === row.dataset.trackId);
    if (!track) return;
    if (event.target.closest(".music-player__remove")) {
      try {
        await deleteTrack(track.id);
        if (state.activeId === track.id) stopPlayback();
        await refresh();
      } catch (deleteError) {
        showError(formatError(deleteError));
      }
      return;
    }
    if (event.target.closest(".music-player__track-play")) {
      if (state.activeId === track.id && state.stopped) {
        iframe.src = embedUrl(track);
        state.stopped = false;
      } else {
        setActive(track);
      }
      renderTracks();
    }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    error.textContent = "";
    addButton.disabled = true;
    try {
      const parsed = parseYandexTrackUrl($("#music-url", form).value);
      const savedTrack = await createTrack({
        ...parsed,
        title: $("#music-title", form).value.trim(),
      });
      form.reset();
      await refresh();
      setExpanded(true);
      const addedTrack = state.tracks.find(track => track.id === savedTrack?.id)
        || state.tracks[state.tracks.length - 1];
      if (addedTrack) setActive(addedTrack);
    } catch (saveError) {
      showError(formatError(saveError));
    } finally {
      addButton.disabled = false;
    }
  });

  refresh().catch(() => {});
  return root;
}
