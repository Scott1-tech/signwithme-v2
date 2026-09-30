/**
 * Page count and a first-page thumbnail for a file the user just picked.
 *
 * Read entirely in the browser, before anything is sent: the point is to let
 * the uploader see *which* document they are about to upload. Nothing here may
 * throw -- an unreadable PDF is still allowed through to the server, which is
 * the only place a file is really validated.
 */

import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';

// Same bundled worker as the viewer: no CDN fetch, works offline / behind a
// proxy. Assigning the identical URL twice is harmless.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export interface PdfThumbnail {
  /** `0` when the file could not be opened in the browser. */
  pageCount: number;
  /** A PNG data URL, or `null` when the first page could not be drawn. */
  thumbnail: string | null;
}

const EMPTY: PdfThumbnail = { pageCount: 0, thumbnail: null };

export async function readPdfThumbnail(file: File, maxWidth = 176): Promise<PdfThumbnail> {
  let doc: PDFDocumentProxy | null = null;
  try {
    const bytes = await file.arrayBuffer();
    doc = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise;
    const pageCount = doc.numPages;

    let thumbnail: string | null = null;
    try {
      const page = await doc.getPage(1);
      const unscaled = page.getViewport({ scale: 1 });
      const scale = Math.max(0.05, Math.min(maxWidth / unscaled.width, 2));
      const viewport = page.getViewport({ scale });

      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      const context = canvas.getContext('2d');
      if (context) {
        // A PDF page is transparent where nothing is drawn; paper is white.
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas.width, canvas.height);
        await page.render({ canvasContext: context, viewport }).promise;
        thumbnail = canvas.toDataURL('image/png');
      }
    } catch {
      thumbnail = null;
    }

    return { pageCount, thumbnail };
  } catch {
    return EMPTY;
  } finally {
    void doc?.destroy();
  }
}
