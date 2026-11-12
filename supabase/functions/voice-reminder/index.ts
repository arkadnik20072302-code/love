const MAX_AUDIO_BYTES = 5 * 1024 * 1024;
const VOICE_FUNCTION_SECRET = Deno.env.get("VOICE_FUNCTION_SECRET") ?? "";
const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY") ?? "";

function jsonResponse(status: number, body: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function constantTimeEqual(left: string, right: string): boolean {
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

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "Only POST is supported." });
  }
  if (!VOICE_FUNCTION_SECRET || !GEMINI_API_KEY) {
    return jsonResponse(503, { error: "Voice processing is not configured." });
  }
  if (
    !constantTimeEqual(
      request.headers.get("x-voice-function-secret") ?? "",
      VOICE_FUNCTION_SECRET,
    )
  ) {
    return jsonResponse(401, { error: "Unauthorized." });
  }
  if (!request.headers.get("content-type")?.startsWith("audio/ogg")) {
    return jsonResponse(415, { error: "Expected an OGG audio file." });
  }

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > MAX_AUDIO_BYTES) {
    return jsonResponse(413, { error: "Audio file is too large." });
  }

  const localTime = request.headers.get("x-local-time") ?? "";
  if (!Number.isFinite(Date.parse(localTime))) {
    return jsonResponse(400, { error: "A valid local time is required." });
  }

  let audio: Uint8Array;
  try {
    audio = new Uint8Array(await request.arrayBuffer());
  } catch {
    return jsonResponse(400, { error: "Could not read the audio file." });
  }
  if (audio.length === 0 || audio.length > MAX_AUDIO_BYTES) {
    return jsonResponse(413, { error: "Audio file is empty or too large." });
  }

  let audioBinary = "";
  for (let offset = 0; offset < audio.length; offset += 0x8000) {
    audioBinary += String.fromCharCode(...audio.subarray(offset, offset + 0x8000));
  }
  const audioBase64 = btoa(audioBinary);
  const prompt = [
    "Распознай русскую речь и преврати её в напоминание.",
    `Текущее локальное время с часовым поясом: ${localTime}.`,
    "Верни только JSON вида {\"reminder\":\"...\"}.",
    "В reminder укажи время и текст на русском в формате «завтра 18:30 купить цветы»",
    "или «через 2 часа позвонить маме». Для конкретной даты используй YYYY-MM-DD HH:MM.",
    "Не добавляй деталей, которых нет в записи. Если время или действие неразборчивы, верни пустую строку.",
  ].join(" ");

  let modelResponse: Response;
  try {
    modelResponse = await fetch(
      "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-goog-api-key": GEMINI_API_KEY,
        },
        body: JSON.stringify({
          contents: [{
            parts: [
              { text: prompt },
              { inline_data: { mime_type: "audio/ogg", data: audioBase64 } },
            ],
          }],
          generationConfig: {
            responseMimeType: "application/json",
            temperature: 0.1,
            maxOutputTokens: 256,
          },
        }),
      },
    );
  } catch {
    return jsonResponse(502, { error: "The AI provider could not be reached." });
  }

  if (!modelResponse.ok) {
    return jsonResponse(502, { error: "The AI provider could not process the audio." });
  }

  try {
    const result = await modelResponse.json();
    const modelText = result?.candidates?.[0]?.content?.parts
      ?.map((part: { text?: string }) => part.text ?? "")
      .join("");
    const parsed = JSON.parse(modelText);
    if (typeof parsed.reminder !== "string" || parsed.reminder.length > 400) {
      return jsonResponse(502, { error: "The AI provider returned an invalid reminder." });
    }
    return jsonResponse(200, { reminder: parsed.reminder.trim() });
  } catch {
    return jsonResponse(502, { error: "The AI provider returned an invalid response." });
  }
});
