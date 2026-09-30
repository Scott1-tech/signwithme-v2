import {
  MAX_IMAGE_BYTES,
  MAX_IMAGE_DIMENSION,
  MAX_IMAGE_PIXELS,
} from './annotationKinds/image';

/**
 * A picture chosen for the Image tool (W4), checked HERE before anything is
 * uploaded -- the same gates the server applies (`app/api/deps.py`), so the
 * common refusals are said at once, in words, instead of as a 413 / 422:
 *
 *  * PNG or JPEG, decided by the file's first bytes (not its name or MIME);
 *  * at most 2 MB;
 *  * at most 4000 px on either side and 20 000 000 pixels.
 *
 * The server stays the authority (it re-checks all of it and decodes the
 * file for real).
 */

export type CheckedImage =
  | { ok: true; dataUrl: string; width: number; height: number }
  | { ok: false; error: string };

const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG_MAGIC = [0xff, 0xd8, 0xff];

const startsWith = (bytes: Uint8Array, magic: number[]) => magic.every((value, index) => bytes[index] === value);

const megabytes = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

function readAs<T extends 'dataUrl' | 'buffer'>(file: Blob, as: T): Promise<T extends 'dataUrl' ? string : ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read'));
    reader.onload = () => resolve(reader.result as T extends 'dataUrl' ? string : ArrayBuffer);
    if (as === 'dataUrl') reader.readAsDataURL(file);
    else reader.readAsArrayBuffer(file);
  });
}

function naturalSize(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error('undecodable'));
    image.src = url;
  });
}

export async function checkImageFile(file: File): Promise<CheckedImage> {
  if (file.size === 0) return { ok: false, error: 'That file is empty.' };
  if (file.size > MAX_IMAGE_BYTES) {
    return {
      ok: false,
      error: `That image is ${megabytes(file.size)} — the limit is 2 MB. Choose a smaller file or save it with more compression.`,
    };
  }
  let head: Uint8Array;
  try {
    head = new Uint8Array(await readAs(file.slice(0, 8), 'buffer'));
  } catch {
    return { ok: false, error: 'That file could not be read.' };
  }
  const isPng = startsWith(head, PNG_MAGIC);
  if (!isPng && !startsWith(head, JPEG_MAGIC)) {
    return { ok: false, error: `“${file.name}” is not a PNG or JPEG image. Only PNG and JPEG pictures can be placed.` };
  }
  let dataUrl: string;
  try {
    // The MIME in the data URL is the one the BYTES say (a file saved without
    // an extension reads as `application/octet-stream`, which the server
    // would refuse as an unsupported data URL).
    const raw = await readAs(file, 'dataUrl');
    dataUrl = `data:image/${isPng ? 'png' : 'jpeg'};base64,${raw.slice(raw.indexOf(',') + 1)}`;
  } catch {
    return { ok: false, error: 'That file could not be read.' };
  }
  let size: { width: number; height: number };
  try {
    size = await naturalSize(dataUrl);
  } catch {
    return { ok: false, error: `“${file.name}” is damaged or not a readable picture.` };
  }
  if (
    size.width > MAX_IMAGE_DIMENSION ||
    size.height > MAX_IMAGE_DIMENSION ||
    size.width * size.height > MAX_IMAGE_PIXELS
  ) {
    return {
      ok: false,
      error: `That image is ${size.width} × ${size.height} px — at most ${MAX_IMAGE_DIMENSION} px on a side is accepted. Scale it down first.`,
    };
  }
  return { ok: true, dataUrl, ...size };
}
