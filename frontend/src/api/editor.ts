import { api, ApiError } from './client';
import type { AnnotationRequest } from '../lib/annotations';
import type { FinalizeLayoutRequest } from '../lib/layout';

/**
 * Finalize, as the full-screen editor sends it (shared contract v4).
 *
 * A separate module rather than a wider `signaturesApi.finalize`: the editor's
 * body carries `annotations`, which the signing page neither builds nor knows
 * about, and that page's own contract must not change underneath it. Both call
 * the same endpoint -- the server is the one authority on what it accepts.
 *
 * What the editor deliberately does NOT send is `dates`: the date slots of a
 * template belong to the signing page, which holds the values. Sending an empty
 * list here means "I have nothing to add", and a contract with a required date
 * still outstanding comes back as a 409 naming it -- which is exactly what the
 * user has to be told.
 */

export interface EditorFinalizeResult {
  contract_id: string;
  status: string;
  file_hash: string;
  download_url: string;
}

export interface EditorFinalizeBody {
  apply_paired_dates?: boolean;
  /** Position/appearance tuning saved by the signing page; sent unchanged. */
  layout?: FinalizeLayoutRequest;
  /** Free annotations. Omitted entirely while there are none. */
  annotations?: AnnotationRequest[];
}

export const editorApi = {
  finalize: (contractId: string, body: EditorFinalizeBody) =>
    api.post<EditorFinalizeResult>(`/api/contracts/${contractId}/finalize`, body),
};

/**
 * True when a refusal is the server saying it has never heard of `annotations`
 * -- i.e. the v4 backend is not deployed here yet. A 422 whose message or
 * details point at that field is the only shape this can take: FastAPI reports
 * an unknown key as a validation error on it.
 *
 * Worth telling apart from every other 422, because the user's annotations are
 * safe in `localStorage` and the right advice is "this server is older", not
 * "your annotations are wrong".
 */
export function isAnnotationsUnsupported(cause: unknown): boolean {
  if (!(cause instanceof ApiError) || cause.status !== 422) return false;
  const haystack = `${cause.message} ${JSON.stringify(cause.details ?? {})}`.toLowerCase();
  return haystack.includes('annotation');
}
