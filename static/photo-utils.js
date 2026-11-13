const MAX_THUMBNAIL_BYTES = 256 * 1024;

export async function createThumbnailDataUrl(file) {
  const objectUrl = URL.createObjectURL(file);
  const image = new Image();
  try {
    image.src = objectUrl;
    await image.decode();
    if (!image.naturalWidth || !image.naturalHeight) {
      throw new Error("Не удалось прочитать фотографию.");
    }

    for (const maxSide of [480, 360, 280]) {
      const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.72, 0.58, 0.44]) {
        let thumbnail = canvas.toDataURL("image/webp", quality);
        if (!thumbnail.startsWith("data:image/webp;")) {
          thumbnail = canvas.toDataURL("image/jpeg", quality);
        }
        if (Math.ceil((thumbnail.length - thumbnail.indexOf(",") - 1) * 3 / 4) <= MAX_THUMBNAIL_BYTES) {
          canvas.width = 0;
          canvas.height = 0;
          return thumbnail;
        }
      }
      canvas.width = 0;
      canvas.height = 0;
    }
    throw new Error("Не удалось подготовить компактное превью фотографии.");
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export function dataUrlToBlob(dataUrl) {
  const [header, encoded] = dataUrl.split(",", 2);
  const mimeType = /^data:(image\/(?:jpeg|png|webp));base64$/.exec(header)?.[1];
  if (!mimeType || !encoded) throw new Error("Не удалось прочитать превью фотографии.");
  const binary = atob(encoded);
  const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  return new Blob([bytes], { type: mimeType });
}
