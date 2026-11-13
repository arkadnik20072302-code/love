import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

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
  if (detail.includes("Could not find the table 'public.memories' in the schema cache")) {
    return "В Supabase ещё нет таблицы воспоминаний. Выполни файл supabase/memories_access.sql и обнови страницу.";
  }
  if (
    /story_(title|text)/i.test(detail)
    && /(schema cache|column|does not exist)/i.test(detail)
  ) {
    return "В Supabase ещё не добавлены поля историй. Повторно выполни supabase/memories_access.sql в SQL Editor и обнови страницу.";
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
    "id,memory_date,photo_path,thought_her,thought_him,story_title,story_text,created_at",
    { order: "memory_date.desc,created_at.desc" },
  );
  return Promise.all(rows.map(async memory => {
    const { data, error } = await supabase.storage
      .from("memories")
      .createSignedUrl(memory.photo_path, 3600);
    if (error) throw error;
    return { ...memory, photo: data.signedUrl };
  }));
}

export async function listMemoryStories() {
  return selectRows(
    "memories",
    "id,memory_date,story_title,story_text",
    { order: "memory_date.desc,created_at.desc" },
  ).then(rows => rows.filter(memory => memory.story_title || memory.story_text));
}

export async function uploadMemory(memoryDate, file, storyTitle = "", storyText = "") {
  if (!memoryDate) throw new Error("Укажите дату воспоминания.");
  if (storyTitle.length > 120 || storyText.length > 8000) {
    throw new Error("Название истории ограничено 120, а текст — 8000 символами.");
  }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("Поддерживаются фотографии JPEG, PNG и WebP.");
  }
  if (file.size > 3 * 1024 * 1024) {
    throw new Error("Фотография должна быть меньше 3 МБ.");
  }
  const id = crypto.randomUUID().replaceAll("-", "");
  const extension = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[file.type];
  const photoPath = `${id}.${extension}`;
  const { error: uploadError } = await supabase.storage
    .from("memories")
    .upload(photoPath, file, { contentType: file.type, upsert: false });
  if (uploadError) throw uploadError;
  const { error: insertError } = await supabase.from("memories").insert({
    id,
    memory_date: memoryDate,
    photo_path: photoPath,
    story_title: storyTitle.trim(),
    story_text: storyText.trim(),
  });
  if (insertError) {
    const { error: cleanupError } = await supabase.storage.from("memories").remove([photoPath]);
    if (cleanupError) {
      throw new Error(`${insertError.message} Файл не удалось автоматически очистить: ${cleanupError.message}`);
    }
    throw insertError;
  }
}

export async function deleteMemory(id) {
  const rows = await selectRows("memories", "id,photo_path", { id: `eq.${id}` });
  if (!rows.length) throw new Error("Воспоминание не найдено.");
  const { error: rowError } = await supabase.from("memories").delete().eq("id", id);
  if (rowError) throw rowError;
  const { error: photoError } = await supabase.storage
    .from("memories")
    .remove([rows[0].photo_path]);
  if (photoError) {
    throw new Error(`Воспоминание удалено, но фото не удалось очистить из хранилища: ${photoError.message}`);
  }
}
