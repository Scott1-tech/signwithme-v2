/**
 * Client-side rules for the "upload a contract" page.
 *
 * The browser checks nothing the server does not check again: `read_pdf_upload`
 * (backend/app/api/deps.py) re-reads the type, the size and the `%PDF` magic.
 * These only exist so the user hears about an obvious mistake before spending a
 * minute on an upload that was always going to be refused.
 */

import { ApiError } from '../api/client';

/** Mirror of `settings.max_upload_bytes` (backend/app/config.py). */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const MAX_UPLOAD_MB = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));

/** `612 B`, `41.3 KB`, `7.8 MB` -- short enough to sit on one line. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export const pageLabel = (pages: number) => `${pages} page${pages === 1 ? '' : 's'}`;

/**
 * Why this file cannot be uploaded, or `null` when it can.
 *
 * The type is read from both the browser's MIME guess and the extension: a
 * `.pdf` dragged out of some archivers arrives as `application/octet-stream`,
 * which the server accepts too, so the extension decides there.
 */
export function pdfFileProblem(file: File): string | null {
  const name = file.name ?? '';
  const type = (file.type ?? '').toLowerCase();
  const looksPdf =
    name.toLowerCase().endsWith('.pdf') &&
    (type === '' || type === 'application/pdf' || type === 'application/octet-stream');

  if (!looksPdf) {
    return `“${name || 'This file'}” is not a PDF. Only PDF files can be uploaded — export or print the contract to PDF first.`;
  }
  if (file.size === 0) {
    return `“${name}” is empty (0 bytes). Check that the file finished downloading, then choose it again.`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `“${name}” is ${formatBytes(file.size)}; the limit is ${MAX_UPLOAD_MB} MB. Split or re-export the PDF and try again.`;
  }
  return null;
}

/** The server's own wording, punctuated so it can be quoted mid-paragraph. */
const sentence = (message: string): string => {
  const text = (message || '').trim();
  if (!text) return '';
  return /[.!?]$/.test(text) ? text : `${text}.`;
};

/** Turns a refused upload into a sentence the uploader can act on. */
export function describeUploadError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 413) {
      return `The file is too large for the server (limit ${MAX_UPLOAD_MB} MB). Re-export the PDF at a smaller size and try again.`;
    }
    if (cause.status === 422) {
      return `The server could not accept this file: ${sentence(cause.message)} Check that it is the completed contract, saved as a PDF.`;
    }
    if (cause.status === 409) {
      return `The selected template is not ready for contracts yet: ${sentence(cause.message)} Pick another template, or wait until its analysis finishes.`;
    }
    if (cause.status === 403) {
      return 'Your account is not allowed to upload contracts for this template.';
    }
    if (cause.status === 401) {
      return 'Your session has expired. Sign in again, then upload the file once more.';
    }
    return cause.message || `Upload failed (${cause.status}).`;
  }
  return cause instanceof Error ? cause.message : 'Upload failed';
}
