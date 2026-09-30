import { useCallback, useRef, useState } from 'react';

/** Client-side ceiling; the server enforces its own (authoritative) limit. */
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

/**
 * Only raster PNG/JPEG. SVG is deliberately absent: it is a document format
 * that can carry scripts and external references, and the server stores what
 * it receives as an image blob.
 */
const ALLOWED: Record<string, string[]> = {
  'image/png': ['png'],
  'image/jpeg': ['jpg', 'jpeg'],
};

// Bound the PREVIEW canvas. A 2 MB JPEG can decode into a very large bitmap;
// the preview never needs more than this. (Uploads send the original file.)
const MAX_EDGE = 1600;

// Background removal (preview only -- the server repeats it authoritatively):
// the paper colour is the median of a ring along the image edge, every pixel is
// scored by its colour distance from it, Otsu splits paper from ink on that
// score, and a soft ramp around the split keeps anti-aliased strokes smooth.
/** Ring width as a share of the shorter side, bounded in pixels. */
const RING_SHARE = 0.04;
const RING_MAX_PX = 24;
/** Below this distance nothing counts as ink, however flat the image is. */
const MIN_THRESHOLD = 24;
/** Soft alpha ramp: fully clear under `t·LOW`, fully opaque over `t·HIGH`. */
const RAMP_LOW = 0.6;
const RAMP_HIGH = 1.4;
/** A pixel at least this opaque counts as ink when cropping. */
const CROP_ALPHA = 24;
const CROP_PADDING = 2;

/** Largest between-class variance split of a 256-bin histogram. */
function otsu(histogram: Uint32Array, total: number): number {
  let sum = 0;
  for (let i = 0; i < 256; i += 1) sum += i * histogram[i];
  let sumBackground = 0;
  let weightBackground = 0;
  let best = 0;
  let threshold = 0;
  for (let i = 0; i < 256; i += 1) {
    weightBackground += histogram[i];
    if (weightBackground === 0) continue;
    const weightForeground = total - weightBackground;
    if (weightForeground === 0) break;
    sumBackground += i * histogram[i];
    const meanBackground = sumBackground / weightBackground;
    const meanForeground = (sum - sumBackground) / weightForeground;
    const between =
      weightBackground * weightForeground * (meanBackground - meanForeground) ** 2;
    if (between > best) {
      best = between;
      threshold = i;
    }
  }
  return threshold;
}

function channelMedian(histogram: Uint32Array, count: number): number {
  let seen = 0;
  for (let i = 0; i < 256; i += 1) {
    seen += histogram[i];
    if (seen * 2 >= count) return i;
  }
  return 255;
}

/** Knock the paper out of the canvas in place and crop it to the ink. */
function removeBackground(canvas: HTMLCanvasElement, context: CanvasRenderingContext2D): void {
  const { width, height } = canvas;
  const pixels = context.getImageData(0, 0, width, height);
  const data = pixels.data;

  // 1. Paper colour: per-channel median of the (opaque) edge ring.
  const ring = Math.max(1, Math.min(Math.round(Math.min(width, height) * RING_SHARE), RING_MAX_PX));
  const red = new Uint32Array(256);
  const green = new Uint32Array(256);
  const blue = new Uint32Array(256);
  let ringCount = 0;
  const sample = (x: number, y: number) => {
    const i = (y * width + x) * 4;
    if (data[i + 3] < 128) return;
    red[data[i]] += 1;
    green[data[i + 1]] += 1;
    blue[data[i + 2]] += 1;
    ringCount += 1;
  };
  for (let y = 0; y < height; y += 1) {
    if (y < ring || y >= height - ring) {
      for (let x = 0; x < width; x += 1) sample(x, y);
    } else {
      for (let x = 0; x < Math.min(ring, width); x += 1) sample(x, y);
      for (let x = Math.max(width - ring, ring); x < width; x += 1) sample(x, y);
    }
  }
  const paper =
    ringCount > 0
      ? [channelMedian(red, ringCount), channelMedian(green, ringCount), channelMedian(blue, ringCount)]
      : [255, 255, 255];

  // 2. Distance from the paper colour, capped to a byte, and its histogram.
  const distance = new Uint8Array(width * height);
  const histogram = new Uint32Array(256);
  for (let p = 0, i = 0; p < distance.length; p += 1, i += 4) {
    const d =
      data[i + 3] < 128
        ? 0
        : Math.min(
            255,
            Math.round(
              Math.hypot(data[i] - paper[0], data[i + 1] - paper[1], data[i + 2] - paper[2]),
            ),
          );
    distance[p] = d;
    histogram[d] += 1;
  }

  // 3. Otsu split, then a soft ramp around it.
  const threshold = Math.max(otsu(histogram, distance.length), MIN_THRESHOLD);
  const low = threshold * RAMP_LOW;
  const high = Math.min(255, Math.max(low + 1, threshold * RAMP_HIGH));

  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  for (let p = 0, i = 0; p < distance.length; p += 1, i += 4) {
    const d = distance[p];
    const factor = d <= low ? 0 : d >= high ? 1 : (d - low) / (high - low);
    const alpha = Math.round(data[i + 3] * factor);
    data[i + 3] = alpha;
    if (alpha >= CROP_ALPHA) {
      const x = p % width;
      const y = (p - x) / width;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  context.putImageData(pixels, 0, 0);

  // 4. Crop to the ink (plus a hair of padding). No ink at all: leave as is.
  if (maxX < 0) return;
  const left = Math.max(0, minX - CROP_PADDING);
  const top = Math.max(0, minY - CROP_PADDING);
  const cropWidth = Math.min(width, maxX + CROP_PADDING + 1) - left;
  const cropHeight = Math.min(height, maxY + CROP_PADDING + 1) - top;
  if (cropWidth === width && cropHeight === height) return;
  const region = context.getImageData(left, top, cropWidth, cropHeight);
  canvas.width = cropWidth;
  canvas.height = cropHeight;
  context.putImageData(region, 0, 0);
}

export function validateSignatureFile(file: File): string | null {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  const allowedExtensions = ALLOWED[file.type];
  if (!allowedExtensions) {
    return 'Only PNG or JPEG images are accepted.';
  }
  if (!allowedExtensions.includes(extension)) {
    return `The file extension ".${extension}" does not match its type (${file.type}).`;
  }
  if (file.size === 0) {
    return 'The file is empty.';
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `The image is ${(file.size / 1024 / 1024).toFixed(1)} MB; the limit is 2 MB.`;
  }
  return null;
}

/**
 * Decode a file through the browser's image pipeline, both to prove it really
 * is an image and to draw the preview. The upload itself carries the original
 * bytes: the server decodes, re-encodes and cleans the image authoritatively.
 */
function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('The file could not be decoded as an image.'));
    };
    image.src = url;
  });
}

export interface SignatureUploadState {
  canvasRef: React.RefObject<HTMLCanvasElement>;
  fileName: string | null;
  error: string | null;
  isEmpty: boolean;
  select: (file: File | null) => Promise<void>;
  clear: () => void;
  /**
   * The ORIGINAL file, byte for byte, as a `data:image/png|jpeg;base64,` URL --
   * what goes to the server. The server removes the background itself and
   * skips that step for images that already carry transparency, so sending a
   * client-cleaned image would leave paper behind. Null when empty.
   */
  toDataUrl: () => string | null;
  /** The cleaned preview (PNG) for on-screen display only; never uploaded. */
  toPreviewDataUrl: () => string | null;
}

/** Exact file bytes as a data URL; the MIME type is the validated `file.type`. */
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('The file could not be read.'));
    reader.onload = () => resolve(reader.result as string);
    reader.readAsDataURL(file);
  });
}

export function useSignatureUpload(): SignatureUploadState {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const originalRef = useRef<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(true);

  // Preview only: scaled to MAX_EDGE and cleaned so the user sees roughly what
  // the server will produce. None of these pixels are uploaded.
  const render = useCallback(
    (image: HTMLImageElement | null) => {
      const canvas = canvasRef.current;
      const context = canvas?.getContext('2d');
      if (!canvas || !context) return;

      if (!image) {
        canvas.width = 1;
        canvas.height = 1;
        context.clearRect(0, 0, 1, 1);
        return;
      }

      const scale = Math.min(1, MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);

      removeBackground(canvas, context);
    },
    [],
  );

  const clear = useCallback(() => {
    originalRef.current = null;
    setFileName(null);
    setError(null);
    setIsEmpty(true);
    render(null);
  }, [render]);

  const select = useCallback(
    async (file: File | null) => {
      if (!file) {
        clear();
        return;
      }
      const problem = validateSignatureFile(file);
      if (problem) {
        clear();
        setError(problem);
        return;
      }
      try {
        // Decoding still gates the file (it must really be an image), but the
        // bytes sent are the original ones. Validation already caps them at
        // 2 MB, the server's own limit, so no re-encode or downscale is needed.
        const [image, original] = await Promise.all([loadImage(file), readAsDataUrl(file)]);
        originalRef.current = original;
        setFileName(file.name);
        setError(null);
        setIsEmpty(false);
        render(image);
      } catch (cause) {
        clear();
        setError(cause instanceof Error ? cause.message : 'Could not read the image.');
      }
    },
    [clear, render],
  );

  const toDataUrl = useCallback(
    () => (isEmpty ? null : originalRef.current),
    [isEmpty],
  );

  const toPreviewDataUrl = useCallback(
    () => (isEmpty ? null : (canvasRef.current?.toDataURL('image/png') ?? null)),
    [isEmpty],
  );

  return {
    canvasRef,
    fileName,
    error,
    isEmpty,
    select,
    clear,
    toDataUrl,
    toPreviewDataUrl,
  };
}
