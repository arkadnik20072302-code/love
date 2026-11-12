import {
  constantTimeEqual,
  formatLocalDateTime,
  parseReminder,
  parseRestResponse,
  stableId,
} from "./shared.ts";

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const telegramToken = Deno.env.get("TELEGRAM_BOT_TOKEN") ?? "";
const webhookSecret = Deno.env.get("TELEGRAM_WEBHOOK_SECRET") ?? "";
const cronSecret = Deno.env.get("TELEGRAM_CRON_SECRET") ?? "";
const internalSecret = Deno.env.get("TELEGRAM_INTERNAL_SECRET") ?? "";
const voiceFunctionSecret = Deno.env.get("VOICE_FUNCTION_SECRET") ?? "";
const allowedUserIds = new Set(
  (Deno.env.get("TELEGRAM_ALLOWED_USER_IDS") ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter((id) =>
      /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0
    ),
);
const offsetValue = Number(
  Deno.env.get("BOT_TIMEZONE_OFFSET_MINUTES") ?? "360",
);
const timezoneOffsetMinutes = Number.isInteger(offsetValue) &&
    Math.abs(offsetValue) <= 14 * 60
  ? offsetValue
  : 360;
const MAX_VOICE_BYTES = 5 * 1024 * 1024;
const MAX_VOICE_SECONDS = 180;
const MAX_UPDATE_BYTES = 1_000_000;
const KEYBOARD = {
  keyboard: [
    [{ text: "➕ Добавить желание" }, { text: "🔔 Напоминание" }],
    [{ text: "📋 Мои желания" }, { text: "ℹ️ Помощь" }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

type Wish = {
  id: string;
  title: string;
  category: string;
  status: string;
  planned_for?: string | null;
};

type Reminder = {
  id: string;
  title: string;
  remind_at: string;
  created_by: string;
  sent: boolean;
  sent_to: string;
};

class UserInputError extends Error {}
class TelegramRequestError extends Error {
  constructor(readonly status: number, readonly method: string) {
    super(`Telegram request ${method} failed.`);
  }
}

function logSafeError(context: string, error: unknown): void {
  let detail = error instanceof Error ? error.message : "Unknown error";
  for (
    const secret of [
      telegramToken,
      serviceRoleKey,
      webhookSecret,
      cronSecret,
      internalSecret,
      voiceFunctionSecret,
    ]
  ) {
    if (secret) detail = detail.replaceAll(secret, "[redacted]");
  }
  console.error(`${context}: ${detail.slice(0, 240)}`);
}

function jsonResponse(
  status: number,
  payload: Record<string, unknown>,
): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function databaseRequest<T>(
  method: string,
  resource: string,
  body?: unknown,
  prefer?: string,
): Promise<T> {
  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error("Supabase function is not configured.");
  }
  const headers: Record<string, string> = {
    apikey: serviceRoleKey,
    authorization: `Bearer ${serviceRoleKey}`,
    accept: "application/json",
  };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (prefer) headers.prefer = prefer;
  const response = await fetch(`${supabaseUrl}/rest/v1/${resource}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`Supabase request failed (${response.status}).`);
  }
  return parseRestResponse<T>(response.status, await response.text());
}

async function telegramRequest(
  method: string,
  payload: unknown,
): Promise<unknown> {
  const response = await fetch(
    `https://api.telegram.org/bot${telegramToken}/${method}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  let result: { ok?: boolean; result?: unknown };
  try {
    result = await response.json();
  } catch {
    throw new TelegramRequestError(response.status, method);
  }
  if (!response.ok || !result?.ok) {
    throw new TelegramRequestError(response.status, method);
  }
  return result.result;
}

async function sendMessage(chatId: number, text: string): Promise<void> {
  await telegramRequest("sendMessage", {
    chat_id: chatId,
    text,
    reply_markup: KEYBOARD,
  });
}

async function setSession(
  userId: string,
  pendingAction: string,
): Promise<void> {
  await databaseRequest(
    "POST",
    "telegram_sessions?on_conflict=telegram_user_id",
    {
      telegram_user_id: userId,
      pending_action: pendingAction,
      updated_at: new Date().toISOString(),
    },
    "resolution=merge-duplicates,return=minimal",
  );
}

async function getSession(userId: string): Promise<string | null> {
  const rows = await databaseRequest<Array<{ pending_action: string }>>(
    "GET",
    `telegram_sessions?select=pending_action&telegram_user_id=eq.${
      encodeURIComponent(userId)
    }&limit=1`,
  );
  return rows[0]?.pending_action ?? null;
}

async function clearSession(userId: string): Promise<void> {
  await databaseRequest(
    "DELETE",
    `telegram_sessions?telegram_user_id=eq.${encodeURIComponent(userId)}`,
  );
}

async function createWish(title: string, updateId: number): Promise<Wish> {
  const trimmed = title.trim();
  if (!trimmed || trimmed.length > 120) {
    throw new UserInputError("Желание должно содержать от 1 до 120 символов.");
  }
  const rows = await databaseRequest<Wish[]>(
    "POST",
    "wishes?on_conflict=id",
    {
      id: stableId(updateId, "wish"),
      title: trimmed,
      category: "Разное",
      status: "planned",
      created_at: new Date().toISOString().slice(0, 10),
    },
    "resolution=ignore-duplicates,return=representation",
  );
  if (rows[0]) return rows[0];
  return {
    id: stableId(updateId, "wish"),
    title: trimmed,
    category: "Разное",
    status: "planned",
  };
}

async function createReminder(
  input: string,
  userId: string,
  updateId: number,
): Promise<Reminder> {
  let parsed: ReturnType<typeof parseReminder>;
  try {
    parsed = parseReminder(input, new Date(), timezoneOffsetMinutes);
  } catch (error) {
    if (error instanceof Error) throw new UserInputError(error.message);
    throw error;
  }
  const reminder = {
    id: stableId(updateId, "reminder"),
    title: parsed.title,
    remind_at: parsed.remindAt,
    created_by: userId,
    sent: false,
    sent_to: "",
  };
  await databaseRequest(
    "POST",
    "reminders?on_conflict=id",
    reminder,
    "resolution=ignore-duplicates,return=minimal",
  );
  return reminder;
}

function localIsoTime(date: Date, offsetMinutes: number): string {
  const local = new Date(date.getTime() + offsetMinutes * 60_000);
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absoluteOffset = Math.abs(offsetMinutes);
  const offset = `${sign}${
    String(Math.floor(absoluteOffset / 60)).padStart(2, "0")
  }:${String(absoluteOffset % 60).padStart(2, "0")}`;
  return `${local.toISOString().slice(0, 19)}${offset}`;
}

async function createVoiceReminder(
  voice: Record<string, unknown>,
  userId: string,
  updateId: number,
): Promise<Reminder> {
  const duration = Number(voice.duration ?? 0);
  const fileSize = Number(voice.file_size ?? 0);
  if (
    !Number.isFinite(duration) ||
    !Number.isFinite(fileSize) ||
    duration < 0 ||
    fileSize < 0 ||
    duration > MAX_VOICE_SECONDS ||
    fileSize > MAX_VOICE_BYTES
  ) {
    throw new UserInputError(
      "Голосовое должно быть короче 3 минут и меньше 5 МБ.",
    );
  }
  if (!voiceFunctionSecret || !voice.file_id) {
    throw new UserInputError(
      "Распознавание голоса пока не настроено. Напишите напоминание текстом.",
    );
  }
  const fileInfo = await telegramRequest("getFile", { file_id: voice.file_id });
  const filePath = (fileInfo as { file_path?: string })?.file_path;
  if (
    !filePath ||
    !/^[A-Za-z0-9_./-]+$/.test(filePath) ||
    filePath.split("/").includes("..")
  ) {
    throw new Error(
      "Telegram не вернул корректный путь к голосовому сообщению.",
    );
  }
  const audioResponse = await fetch(
    `https://api.telegram.org/file/bot${telegramToken}/${filePath}`,
  );
  if (!audioResponse.ok) {
    throw new Error("Не удалось загрузить голосовое сообщение.");
  }
  const audio = new Uint8Array(await audioResponse.arrayBuffer());
  if (!audio.length || audio.length > MAX_VOICE_BYTES) {
    throw new Error("Голосовое должно быть меньше 5 МБ.");
  }

  const now = new Date();
  const response = await fetch(`${supabaseUrl}/functions/v1/voice-reminder`, {
    method: "POST",
    headers: {
      apikey: serviceRoleKey,
      "content-type": "audio/ogg",
      "x-voice-function-secret": voiceFunctionSecret,
      "x-local-time": localIsoTime(now, timezoneOffsetMinutes),
    },
    body: audio,
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(
      "Не удалось распознать голосовое. Отправьте напоминание текстом.",
    );
  }
  const payload = await response.json();
  if (typeof payload?.reminder !== "string" || !payload.reminder.trim()) {
    throw new UserInputError(
      "Не расслышала время и текст напоминания. Попробуйте ещё раз.",
    );
  }
  return await createReminder(payload.reminder, userId, updateId);
}

async function handleMessage(update: Record<string, unknown>): Promise<void> {
  const message = update.message as Record<string, unknown> | undefined;
  const chat = message?.chat as Record<string, unknown> | undefined;
  const sender = message?.from as Record<string, unknown> | undefined;
  const chatId = Number(chat?.id);
  const userId = String(sender?.id ?? "");
  const updateId = Number(update.update_id);
  if (
    chat?.type !== "private" ||
    !Number.isSafeInteger(chatId) ||
    chatId <= 0 ||
    !allowedUserIds.has(userId) ||
    !Number.isSafeInteger(updateId)
  ) return;

  const text = typeof message?.text === "string" ? message.text.trim() : "";
  if (text === "/start" || text === "/help" || text === "ℹ️ Помощь") {
    await clearSession(userId);
    await sendMessage(
      chatId,
      "Привет! Добавить желание: /add устроить поездку\n" +
        "Напоминание: /remind завтра 18:30 купить цветы\n" +
        "Голосовое тоже можно отправить как напоминание.",
    );
    return;
  }
  if (text === "/add" || text === "➕ Добавить желание") {
    await setSession(userId, "wish");
    await sendMessage(chatId, "Напишите желание одним сообщением.");
    return;
  }
  if (text.startsWith("/add ")) {
    await clearSession(userId);
    const wish = await createWish(text.slice(5), updateId);
    await sendMessage(chatId, `Добавила в ваши планы: «${wish.title}» 💛`);
    return;
  }
  if (text === "/list" || text === "📋 Мои желания") {
    await clearSession(userId);
    const wishes = await databaseRequest<Wish[]>(
      "GET",
      "wishes?select=title,status&status=neq.done&order=created_at.desc&limit=20",
    );
    const list = wishes.length
      ? wishes.map((wish) => `• ${wish.title}`).join("\n")
      : "Пока нет планов. Добавьте идею командой /add";
    await sendMessage(chatId, `Ваши приключения:\n${list}`);
    return;
  }
  if (text === "/remind" || text === "🔔 Напоминание") {
    await setSession(userId, "reminder");
    await sendMessage(
      chatId,
      "Когда и о чём напомнить? Например: завтра 18:30 купить цветы.",
    );
    return;
  }
  if (text.startsWith("/remind ")) {
    await clearSession(userId);
    const reminder = await createReminder(text.slice(8), userId, updateId);
    await sendMessage(
      chatId,
      `Напоминание сохранено на ${
        formatLocalDateTime(reminder.remind_at, timezoneOffsetMinutes)
      }: ${reminder.title}`,
    );
    return;
  }

  if (message?.voice && typeof message.voice === "object") {
    const reminder = await createVoiceReminder(
      message.voice as Record<string, unknown>,
      userId,
      updateId,
    );
    await sendMessage(
      chatId,
      `Голосовое распознано. Напомню ${
        formatLocalDateTime(reminder.remind_at, timezoneOffsetMinutes)
      }: ${reminder.title}`,
    );
    return;
  }

  if (!text) return;
  const pendingAction = await getSession(userId);
  if (pendingAction === "wish") {
    await clearSession(userId);
    const wish = await createWish(text, updateId);
    await sendMessage(chatId, `Добавила в ваши планы: «${wish.title}» 💛`);
  } else if (pendingAction === "reminder") {
    const reminder = await createReminder(text, userId, updateId);
    await clearSession(userId);
    await sendMessage(
      chatId,
      `Напоминание сохранено на ${
        formatLocalDateTime(reminder.remind_at, timezoneOffsetMinutes)
      }: ${reminder.title}`,
    );
  } else {
    await sendMessage(
      chatId,
      "Добавить идею: /add устроить поездку\nПосмотреть планы: /list",
    );
  }
}

async function dispatchReminders(): Promise<number> {
  const lockRows = await databaseRequest<boolean>(
    "POST",
    "rpc/try_claim_telegram_dispatch",
    { lock_seconds: 240 },
  );
  if (!lockRows) return 0;
  let deliveredCount = 0;
  try {
    const now = new Date().toISOString();
    const reminders = await databaseRequest<Reminder[]>(
      "GET",
      `reminders?select=id,title,remind_at,created_by,sent,sent_to&sent=eq.false&remind_at=lte.${
        encodeURIComponent(now)
      }&order=remind_at.asc&limit=100`,
    );
    for (const reminder of reminders) {
      const sentTo = new Set(
        (reminder.sent_to ?? "").split(",").filter(Boolean),
      );
      for (const userId of [...allowedUserIds].sort()) {
        if (sentTo.has(userId)) continue;
        try {
          await telegramRequest("sendMessage", {
            chat_id: Number(userId),
            text: `🔔 Напоминание\n${reminder.title}`,
          });
          sentTo.add(userId);
          deliveredCount += 1;
        } catch (error) {
          if (
            error instanceof TelegramRequestError && error.status >= 400 &&
            error.status < 500
          ) {
            sentTo.add(userId);
            logSafeError(
              "Telegram permanently rejected a reminder recipient; verify the user has started the bot",
              error,
            );
          } else {
            logSafeError("Telegram reminder delivery failed", error);
            continue;
          }
        }
        await databaseRequest(
          "PATCH",
          `reminders?id=eq.${encodeURIComponent(reminder.id)}`,
          {
            sent_to: [...sentTo].sort().join(","),
            sent: sentTo.size >= allowedUserIds.size,
          },
          "return=minimal",
        );
      }
    }
  } finally {
    await databaseRequest(
      "PATCH",
      "telegram_dispatch_lock?id=eq.1",
      { locked_until: null },
      "return=minimal",
    );
  }
  return deliveredCount;
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "Only POST is supported." });
  }
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > MAX_UPDATE_BYTES) {
    return jsonResponse(413, { error: "Request is too large." });
  }
  if (
    !telegramToken || allowedUserIds.size === 0 || !supabaseUrl ||
    !serviceRoleKey
  ) {
    return jsonResponse(503, { error: "Telegram bot is not configured." });
  }

  const cronAuthorization = request.headers.get("x-telegram-cron-secret") ?? "";
  if (cronSecret && constantTimeEqual(cronAuthorization, cronSecret)) {
    try {
      const payload = await request.json();
      if (payload?.action !== "dispatch_reminders") {
        return jsonResponse(400, { error: "Unknown action." });
      }
      const delivered = await dispatchReminders();
      return jsonResponse(200, { ok: true, delivered });
    } catch (error) {
      logSafeError("Telegram reminder dispatch failed", error);
      return jsonResponse(500, { error: "Reminder dispatch failed." });
    }
  }

  const internalAuthorization = request.headers.get(
    "x-telegram-internal-secret",
  ) ?? "";
  if (
    internalSecret && constantTimeEqual(internalAuthorization, internalSecret)
  ) {
    try {
      const payload = await request.json();
      if (
        payload?.action !== "notify_wish_completed" ||
        typeof payload.title !== "string" ||
        !payload.title.trim() ||
        payload.title.length > 120
      ) {
        return jsonResponse(400, { error: "Invalid notification." });
      }
      const results = await Promise.allSettled(
        [...allowedUserIds].map((userId) =>
          telegramRequest("sendMessage", {
            chat_id: Number(userId),
            text: `✅ Приключение сбылось!\n«${payload.title.trim()}»`,
          })
        ),
      );
      for (const result of results) {
        if (result.status === "rejected") {
          logSafeError(
            "Telegram completion notification recipient failed",
            result.reason,
          );
        }
      }
      return jsonResponse(200, {
        ok: true,
        delivered: results.filter((result) =>
          result.status === "fulfilled"
        ).length,
      });
    } catch (error) {
      logSafeError("Telegram completion notification failed", error);
      return jsonResponse(500, { error: "Notification failed." });
    }
  }

  const telegramAuthorization = request.headers.get(
    "x-telegram-bot-api-secret-token",
  ) ?? "";
  if (
    !webhookSecret || !constantTimeEqual(telegramAuthorization, webhookSecret)
  ) {
    return jsonResponse(401, { error: "Unauthorized." });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse(400, { error: "Invalid update." });
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return jsonResponse(400, { error: "Invalid update." });
  }
  const update = payload as Record<string, unknown>;
  const updateId = update.update_id;
  if (typeof updateId !== "number" || !Number.isSafeInteger(updateId)) {
    return jsonResponse(400, { error: "Invalid update ID." });
  }
  try {
    const claimed = await databaseRequest<boolean>(
      "POST",
      "rpc/claim_telegram_update",
      { p_update_id: updateId },
    );
    if (!claimed) return jsonResponse(200, { ok: true, duplicate: true });

    await handleMessage(update);
    await databaseRequest(
      "PATCH",
      `telegram_updates?update_id=eq.${updateId}`,
      { processed_at: new Date().toISOString() },
      "return=minimal",
    );
    return jsonResponse(200, { ok: true });
  } catch (error) {
    if (error instanceof UserInputError) {
      const message = update.message as Record<string, unknown> | undefined;
      const chat = message?.chat as Record<string, unknown> | undefined;
      if (Number.isSafeInteger(Number(chat?.id))) {
        try {
          await sendMessage(Number(chat?.id), error.message);
          await databaseRequest(
            "PATCH",
            `telegram_updates?update_id=eq.${updateId}`,
            { processed_at: new Date().toISOString() },
            "return=minimal",
          );
          return jsonResponse(200, { ok: true });
        } catch (replyError) {
          error = replyError;
        }
      }
    }
    try {
      await databaseRequest(
        "POST",
        "rpc/release_telegram_update",
        { p_update_id: updateId },
      );
    } catch (releaseError) {
      logSafeError("Could not release the Telegram update claim", releaseError);
    }
    logSafeError("Telegram update processing failed", error);
    return jsonResponse(500, { error: "Update processing failed." });
  }
});
