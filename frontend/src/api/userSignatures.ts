import type { UserSignature } from '../types/contract';
import { ApiError, api, tokenStore } from './client';

/**
 * The current user's private, reusable signature library.
 *
 * These are distinct from the contract-scoped signing events in
 * `signatures.ts`. A stored signature is picked once and reused when signing
 * any contract; the reuse path feeds its PNG bytes into the *unchanged*
 * placement endpoint (Option A), so no new server behaviour is involved -- the
 * only new surface is this library and its ownership-checked reads.
 */
/**
 * Turn a failed `save` into something a person can act on. The server is the
 * authority on size and format; this only translates its verdict.
 */
export function describeSaveError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 413) {
      return 'The image is too large for the server. Use a smaller file (under 2 MB).';
    }
    if (cause.status === 422 || cause.status === 400) {
      return `The server rejected the image: ${cause.message}`;
    }
    if (cause.status === 401) {
      return 'Your session has expired. Sign in again and retry.';
    }
    if (cause.status >= 500) {
      return `The server could not save the signature right now (${cause.status}). Try again shortly.`;
    }
    return cause.message;
  }
  return cause instanceof Error ? cause.message : 'Could not save the signature.';
}

export const userSignaturesApi = {
  list: () => api.get<UserSignature[]>('/api/signatures'),

  save: (payload: { image_data: string; label?: string | null; is_default?: boolean }) =>
    api.post<UserSignature>('/api/signatures', payload),

  /** F2 (B2): a TYPED signature -- the server renders the PNG (`source: "typed"`); the text is not kept. */
  saveTyped: (payload: { typed: { text: string; font: string }; label?: string }) =>
    api.post<UserSignature>('/api/signatures', payload),

  remove: (signatureId: string) =>
    api.del<{ signature_id: string; removed: boolean }>(`/api/signatures/${signatureId}`),

  /**
   * Fetch a stored signature's PNG and return it as a base64 data URL.
   *
   * This is the reuse bridge: the byte payload from the ownership-checked image
   * route is re-encoded into exactly the `data:image/png;base64,...` string the
   * canvas pad would have produced, then handed to `signaturesApi.place` via
   * its existing `image_data` field. The image endpoint streams bytes (not
   * JSON), so this uses an authorised `fetch` in the style of `fetchPdf`.
   */
  imageAsDataUrl: async (signatureId: string): Promise<string> => {
    const token = tokenStore.get();
    const response = await fetch(`/api/signatures/${signatureId}/image`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        'signature_image_failed',
        `Could not load stored signature (${response.status})`,
      );
    }
    const blob = await response.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error('Could not read stored signature'));
      reader.onload = () => resolve(reader.result as string);
      reader.readAsDataURL(blob);
    });
  },
};
