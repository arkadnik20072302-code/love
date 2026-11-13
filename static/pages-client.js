import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { createThumbnailDataUrl, dataUrlToBlob } from "./photo-utils.js";

const config = window.WISHLIST_CONFIG;
if (!config?.url || !config?.publishableKey) {
  throw new Error("Не заданы настройки Supabase для GitHub Pages.");
}

export const supabase = createClient(config.url, config.publishableKey, {
  auth: {
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: true,
  },
});

export async function signIn(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data.user;
}

export async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
}

export async function currentUser() {
  const { data: sessionData, error: sessionError } = await supabase.auth.getSession();
  if (sessionError) throw sessionError;
  return sessionData.session?.user ?? null;
}

export function memorySchemaHint(error) {
  const detail = String(error?.message || error);
  if (
    detail.includes("Could not find the table 'public.memories' in the schema cache")
  ) {
    return "В Supabase ещё нет таблицы воспоминаний. Выполни файл supabase/memories_access.sql и обнови страницу.";
  }
  if (detail.includes("Could not find the table 'public.stories' in the schema cache")) {
    return "В Supabase ещё нет таблицы историй. Повторно выполни supabase/memories_access.sql в SQL Editor и обнови страницу.";
  }
  if (
    /story_(title|text)/i.test(detail)
    && /(schema cache|column|does not exist)/i.test(detail)
  ) {
    return "В Supabase ещё не добавлены поля историй. Повторно выполни supabase/memories_access.sql в SQL Editor и обнови страницу.";
  }
  if (
    /thumbnail_path/i.test(detail)
    && /(schema cache|column|does not exist)/i.test(detail)
  ) {
    return "В Supabase ещё не добавлена поддержка превью. Повторно выполни supabase/memories_access.sql в SQL Editor и обнови страницу.";
  }
  return null;
}

export async function isWishlistMember() {
  const { data, error } = await supabase.rpc("is_wishlist_member");
  if (error) throw error;
  return data === true;
}

export async function selectRows(table, select, query = {}) {
  let request = supabase.from(table).select(select);
  for (const [key, value] of Object.entries(query)) {
    if (key === "order") {
      for (const item of value.split(",")) {
        const [column, direction] = item.split(".");
        request = request.order(column, { ascending: direction !== "desc" });
      }
    } else if (value.startsWith("eq.")) {
      request = request.eq(key, value.slice(3));
    }
  }
  const { data, error } = await request;
  if (error) throw error;
  return data;
}

export async function insertRow(table, row, select = "*") {
  const { data, error } = await supabase.from(table).insert(row).select(select).single();
  if (error) throw error;
  return data;
}

export async function updateRow(table, id, changes, select = "*") {
  const { data, error } = await supabase
    .from(table)
    .update(changes)
    .eq("id", id)
    .select(select)
    .single();
  if (error) throw error;
  return data;
}

export async function deleteRow(table, id) {
  const { data, error } = await supabase
    .from(table)
    .delete()
    .eq("id", id)
    .select("id")
    .single();
  if (error) throw error;
  return data;
}

export async function listMemories() {
  const rows = await selectRows(
    "memories",
    "id,memory_date,photo_path,thumbnail_path,thought_her,thought_him,story_title,story_text,created_at",
    { order: "memory_date.desc,created_at.desc" },
  );
  return Promise.all(rows.map(async memory => {
    const [photo, photoOriginal] = await Promise.all([
      signedMemoryPhoto(memory.thumbnail_path || memory.photo_path),
      signedMemoryPhoto(memory.photo_path),
    ]);
    return { ...memory, photo, photo_original: photoOriginal };
  }));
}

async function signedMemoryPhoto(photoPath) {
  const { data, error } = await supabase.storage
    .from("memories")
    .createSignedUrl(photoPath, 3600);
  if (error) throw error;
  return data.signedUrl;
}

export async function listStories() {
  const [storyRows, memoryRows] = await Promise.all([
    selectRows(
      "stories",
      "id,story_date,title,story_text,photo_path,created_at",
      { order: "story_date.desc,created_at.desc" },
    ),
    selectRows(
      "memories",
      "id,memory_date,photo_path,story_title,story_text,created_at",
      { order: "memory_date.desc,created_at.desc" },
    ),
  ]);
  const stories = await Promise.all(storyRows.map(async story => ({
    id: story.id,
    date: story.story_date,
    title: story.title,
    story_text: story.story_text,
    photo: story.photo_path ? await signedMemoryPhoto(story.photo_path) : "",
    created_at: story.created_at,
  })));
  const migratedMemoryIds = new Set(storyRows
    .filter(story => story.id.startsWith("legacy-"))
    .map(story => story.id.slice("legacy-".length)));
  const legacyStories = await Promise.all(memoryRows
    .filter(memory =>
      (memory.story_title || memory.story_text) && !migratedMemoryIds.has(memory.id)
    )
    .map(async memory => ({
      id: memory.id,
      date: memory.memory_date,
      title: memory.story_title || "История одного момента",
      story_text: memory.story_text,
      photo: await signedMemoryPhoto(memory.photo_path),
      created_at: memory.created_at,
    })));
  return [...stories, ...legacyStories].sort((a, b) =>
    b.date.localeCompare(a.date) || b.created_at.localeCompare(a.created_at),
  );
}

export async function createStory(story) {
  const title = story.title.trim();
  const storyText = story.story_text.trim();
  if (!story.date) throw new Error("Укажите дату истории.");
  if (!title || title.length > 120) {
    throw new Error("Название истории должно содержать от 1 до 120 символов.");
  }
  if (!storyText || storyText.length > 8000) {
    throw new Error("Текст истории должен содержать от 1 до 8000 символов.");
  }
  const file = story.photo;
  if (file && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("Поддерживаются фотографии JPEG, PNG и WebP.");
  }
  if (file && file.size > 3 * 1024 * 1024) {
    throw new Error("Фотография для истории должна быть меньше 3 МБ.");
  }
  const id = crypto.randomUUID().replaceAll("-", "");
  const extension = file
    ? { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[file.type]
    : "";
  const photoPath = file ? `stories/${id}.${extension}` : null;
  if (file) {
    const { error: uploadError } = await supabase.storage
      .from("memories")
      .upload(photoPath, file, { contentType: file.type, upsert: false });
    if (uploadError) throw uploadError;
  }
  const { error } = await supabase.from("stories").insert({
    id,
    story_date: story.date,
    title,
    story_text: storyText,
    photo_path: photoPath,
  });
  if (error) {
    if (photoPath) {
      const { error: cleanupError } = await supabase.storage
        .from("memories")
        .remove([photoPath]);
      if (cleanupError) {
        throw new Error(`${error.message} Фото не удалось автоматически удалить: ${cleanupError.message}`);
      }
    }
    throw error;
  }
}

export async function uploadMemory(memoryDate, file, thumbnailDataUrl) {
  if (!memoryDate) throw new Error("Укажите дату воспоминания.");
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("Поддерживаются фотографии JPEG, PNG и WebP.");
  }
  if (file.size > 20 * 1024 * 1024) {
    throw new Error("Размер фотографии не должен превышать 20 МБ.");
  }
  const thumbnail = thumbnailDataUrl || await createThumbnailDataUrl(file);
  const thumbnailBlob = dataUrlToBlob(thumbnail);
  const thumbnailExtension = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[thumbnailBlob.type];
  const id = crypto.randomUUID().replaceAll("-", "");
  const extension = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[file.type];
  const photoPath = `${id}.${extension}`;
  const thumbnailPath = `thumbnails/${id}.${thumbnailExtension}`;
  const { error: uploadError } = await supabase.storage
    .from("memories")
    .upload(photoPath, file, { contentType: file.type, upsert: false });
  if (uploadError) throw uploadError;
  const { error: thumbnailError } = await supabase.storage
    .from("memories")
    .upload(thumbnailPath, thumbnailBlob, { contentType: thumbnailBlob.type, upsert: false });
  if (thumbnailError) {
    const { error: cleanupError } = await supabase.storage.from("memories").remove([photoPath]);
    if (cleanupError) {
      throw new Error(`${thumbnailError.message} Оригинал не удалось автоматически удалить: ${cleanupError.message}`);
    }
    throw thumbnailError;
  }
  const { error: insertError } = await supabase.from("memories").insert({
    id,
    memory_date: memoryDate,
    photo_path: photoPath,
    thumbnail_path: thumbnailPath,
  });
  if (insertError) {
    const { error: cleanupError } = await supabase.storage
      .from("memories").remove([photoPath, thumbnailPath]);
    if (cleanupError) {
      throw new Error(`${insertError.message} Файл не удалось автоматически очистить: ${cleanupError.message}`);
    }
    throw insertError;
  }
}

export async function deleteMemory(id) {
  const rows = await selectRows("memories", "id,photo_path,thumbnail_path", { id: `eq.${id}` });
  if (!rows.length) throw new Error("Воспоминание не найдено.");
  const storyReferences = await selectRows("stories", "id", {
    photo_path: `eq.${rows[0].photo_path}`,
  });
  const { error: rowError } = await supabase.from("memories").delete().eq("id", id);
  if (rowError) throw rowError;
  if (storyReferences.length) return;
  const paths = [rows[0].photo_path, rows[0].thumbnail_path].filter(Boolean);
  const { error: photoError } = await supabase.storage
    .from("memories")
    .remove(paths);
  if (photoError) {
    throw new Error(`Воспоминание удалено, но фото не удалось очистить из хранилища: ${photoError.message}`);
  }
}
