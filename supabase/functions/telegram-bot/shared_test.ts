import {
  constantTimeEqual,
  formatLocalDateTime,
  parseReminder,
  parseRestResponse,
  stableId,
} from "./shared.ts";

Deno.test("parses relative reminder times using the configured local offset", () => {
  const now = new Date("2026-11-11T06:00:00.000Z");
  const result = parseReminder("завтра в 18:30 купить цветы", now, 360);
  if (result.title !== "купить цветы") throw new Error("Unexpected title.");
  if (result.remindAt !== "2026-11-12T12:30:00.000Z") {
    throw new Error(`Unexpected UTC time: ${result.remindAt}`);
  }
  if (formatLocalDateTime(result.remindAt, 360) !== "12.11 в 18:30") {
    throw new Error("Local time formatting failed.");
  }
});

Deno.test("parses relative duration and rejects past times", () => {
  const now = new Date("2026-11-11T06:00:00.000Z");
  const result = parseReminder("через 2 часа позвонить маме", now, 360);
  if (result.remindAt !== "2026-11-11T08:00:00.000Z") {
    throw new Error(`Unexpected duration result: ${result.remindAt}`);
  }
  let rejectedPastTime = false;
  try {
    parseReminder("сегодня 05:00 купить цветы", now, 360);
  } catch {
    rejectedPastTime = true;
  }
  if (!rejectedPastTime) throw new Error("Past reminder time was accepted.");
});

Deno.test("constant-time comparison checks equality and stable IDs are repeatable", () => {
  if (!constantTimeEqual("secret-token", "secret-token")) {
    throw new Error("Equal strings did not match.");
  }
  if (constantTimeEqual("secret-token", "wrong-token")) {
    throw new Error("Different strings matched.");
  }
  const first = stableId(123456, "wish");
  if (first !== stableId(123456, "wish") || first.length !== 32) {
    throw new Error("Stable update ID generation failed.");
  }
  if (first === stableId(123456, "reminder")) {
    throw new Error("Wish and reminder IDs must not collide.");
  }
});

Deno.test("accepts successful Supabase writes with empty response bodies", () => {
  const result = parseRestResponse<unknown[]>(201, "");
  if (!Array.isArray(result) || result.length !== 0) {
    throw new Error("Empty minimal response was not accepted.");
  }
  if (parseRestResponse<boolean>(200, "true") !== true) {
    throw new Error("JSON RPC response was not parsed.");
  }
});
