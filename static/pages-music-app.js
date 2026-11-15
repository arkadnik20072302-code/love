import {
  createMusicTrack,
  currentUser,
  deleteMusicTrack,
  isWishlistMember,
  listMusicTracks,
  signOut,
} from "./pages-client.js";
import { initMusicPlayer } from "./music-player.js";

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
  initMusicPlayer({
    listTracks: listMusicTracks,
    createTrack: createMusicTrack,
    deleteTrack: deleteMusicTrack,
  });
}

start().catch(error => console.error("Не удалось загрузить плейлист:", error));
