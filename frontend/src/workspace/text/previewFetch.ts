import { ApiError, tokenStore } from '../../api/client';

/**
 * `GET …/document/draft/pages/{uid}/preview?revision=N[&hide=bK]` -- like
 * `fetchPdf`, but a refusal keeps the server's `{error, message, details}`
 * (a 422 `text_overflow` names the op and how many lines it is over; a 409
 * says the revision moved on), which the editor needs to act on.
 */
export async function fetchPreview(
  contractId: string,
  pageUid: string,
  revision: number,
  hideBlockId?: string | null,
): Promise<ArrayBuffer> {
  const token = tokenStore.get();
  const url =
    `/api/contracts/${contractId}/document/draft/pages/${pageUid}/preview?revision=${revision}` +
    (hideBlockId ? `&hide=${encodeURIComponent(hideBlockId)}` : '');
  const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (response.ok) return response.arrayBuffer();
  let body: { error?: string; message?: string; details?: Record<string, unknown> } = {};
  try {
    body = await response.json();
  } catch {
    /* not JSON */
  }
  throw new ApiError(
    response.status,
    body.error ?? 'preview_failed',
    body.message ?? `Could not load the preview (${response.status})`,
    body.details ?? {},
  );
}
