export function constantTimeEqual(left: string, right: string): boolean {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  let difference = leftBytes.length ^ rightBytes.length;
  const length = Math.max(leftBytes.length, rightBytes.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return difference === 0;
}

export function parseRestResponse<T>(status: number, body: string): T {
  if (status === 204 || status === 205 || body.length === 0) return [] as T;
  return JSON.parse(body) as T;
}

function localDateParts(date: Date, offsetMinutes: number) {
  const local = new Date(date.getTime() + offsetMinutes * 60_000);
  return {
    year: local.getUTCFullYear(),
    month: local.getUTCMonth() + 1,
    day: local.getUTCDate(),
    hour: local.getUTCHours(),
    minute: local.getUTCMinutes(),
  };
}

function toUtcIso(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  offsetMinutes: number,
): string {
  const timestamp = Date.UTC(year, month - 1, day, hour, minute) -
    offsetMinutes * 60_000;
  return new Date(timestamp).toISOString();
}

export function parseReminder(
  input: string,
  now: Date,
  offsetMinutes: number,
): { title: string; remindAt: string } {
  let value = input.trim().replace(/^напомни(?:\s+мне)?[,:]?\s+/i, "");
  value = value.replace(
    /^(сегодня|завтра|послезавтра)\s+в\s+(\d{1,2}:\d{2})/i,
    "$1 $2",
  );
  const current = localDateParts(now, offsetMinutes);
  let year = current.year;
  let month = current.month;
  let day = current.day;
  let hour: number;
  let minute: number;
  let title: string;

  const relative = value.match(
    /^через\s+(\d{1,3})\s+(минут(?:у|ы)?|мин|час(?:а|ов)?|день|дня|дней)\s+(.+)$/i,
  );
  if (relative) {
    const amount = Number(relative[1]);
    if (amount < 1) throw new Error("Укажите положительный срок напоминания.");
    const unit = relative[2].toLowerCase();
    const delta = unit.startsWith("мин")
      ? amount * 60_000
      : unit.startsWith("час")
      ? amount * 3_600_000
      : amount * 86_400_000;
    const remindAt = new Date(now.getTime() + delta).toISOString();
    title = relative[3].trim();
    if (!title || title.length > 240) {
      throw new Error(
        "Текст напоминания должен содержать от 1 до 240 символов.",
      );
    }
    return { title, remindAt };
  }

  const match = value.match(
    /^(?:(сегодня|завтра|послезавтра)|(\d{4}-\d{2}-\d{2})|(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?)\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(.+)$/i,
  );
  if (!match) {
    throw new Error(
      "Формат: «завтра 18:30 купить цветы» или «через 2 часа позвонить маме».",
    );
  }
  if (match[1]) {
    const dayOffset = {
      сегодня: 0,
      завтра: 1,
      послезавтра: 2,
    }[match[1].toLowerCase()];
    if (dayOffset === undefined) throw new Error("Укажите корректную дату.");
    const date = new Date(Date.UTC(year, month - 1, day + dayOffset));
    year = date.getUTCFullYear();
    month = date.getUTCMonth() + 1;
    day = date.getUTCDate();
  } else if (match[2]) {
    const isoDate = new Date(`${match[2]}T00:00:00Z`);
    if (
      Number.isNaN(isoDate.getTime()) ||
      isoDate.toISOString().slice(0, 10) !== match[2]
    ) {
      throw new Error("Укажите корректную дату.");
    }
    year = isoDate.getUTCFullYear();
    month = isoDate.getUTCMonth() + 1;
    day = isoDate.getUTCDate();
  } else if (match[3]) {
    day = Number(match[3]);
    month = Number(match[4]);
    year = Number(match[5] || year);
  }

  [hour, minute] = match[6].split(":").map(Number);
  title = match[7].trim();
  const dateCheck = new Date(Date.UTC(year, month - 1, day));
  if (
    dateCheck.getUTCFullYear() !== year ||
    dateCheck.getUTCMonth() + 1 !== month ||
    dateCheck.getUTCDate() !== day ||
    hour > 23 ||
    minute > 59
  ) {
    throw new Error("Укажите корректные дату и время.");
  }
  const remindAt = toUtcIso(year, month, day, hour, minute, offsetMinutes);
  if (Date.parse(remindAt) <= now.getTime()) {
    throw new Error("Укажите время в будущем.");
  }
  if (!title || title.length > 240) {
    throw new Error("Текст напоминания должен содержать от 1 до 240 символов.");
  }
  return { title, remindAt };
}

export function formatLocalDateTime(
  value: string,
  offsetMinutes: number,
): string {
  const local = new Date(Date.parse(value) + offsetMinutes * 60_000);
  const day = String(local.getUTCDate()).padStart(2, "0");
  const month = String(local.getUTCMonth() + 1).padStart(2, "0");
  const hour = String(local.getUTCHours()).padStart(2, "0");
  const minute = String(local.getUTCMinutes()).padStart(2, "0");
  return `${day}.${month} в ${hour}:${minute}`;
}

export function stableId(updateId: number, kind: "wish" | "reminder"): string {
  if (!Number.isSafeInteger(updateId) || updateId < 0) {
    throw new Error("Telegram update ID must be a non-negative safe integer.");
  }
  const typePrefix = kind === "wish" ? "1" : "2";
  const numericId = BigInt(updateId).toString(16).padStart(31, "0").slice(-31);
  return `${typePrefix}${numericId}`;
}
