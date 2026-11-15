import { initMusicPlayer } from "./music-player.js";

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("Сессия истекла. Войдите снова.");
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос.");
  return payload;
}

initMusicPlayer({
  listTracks: async () => (await request("/api/music")).tracks,
  createTrack: async track => (await request("/api/music", {
    method: "POST",
    body: JSON.stringify({ ...track, url: track.yandex_url }),
  })).track,
  deleteTrack: id => request(`/api/music/${id}`, { method: "DELETE" }),
});
