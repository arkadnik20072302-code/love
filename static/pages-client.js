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
    "id,memory_date,photo_path,thought_her,thought_him,created_at",
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

export async function uploadMemory(memoryDate, file) {
  if (!memoryDate) throw new Error("Укажите дату воспоминания.");
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
  });
  if (insertError) {
    const { error: cleanupError } = await supabase.storage.from("memories").remove([photoPath]);
    if (cleanupError) {
      throw new Error(`${insertError.message} Файл не удалось автоматически очистить: ${cleanupError.message}`);
    }
    throw insertError;
  }
}
