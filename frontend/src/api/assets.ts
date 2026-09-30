import { ApiError, api, tokenStore } from './client';

/**
 * Annotation image assets (contract v5, W4): a picture for an `image`
 * annotation is uploaded ONCE and referred to by `asset_id` from then on.
 *
 *   POST /api/contracts/{id}/annotation-assets   JSON `{image_data}` (a PNG /
 *        JPEG data URL, as the signature endpoints take) → 201
 *        `{asset_id, width_px, height_px}`
 *   GET  /api/contracts/{id}/annotation-assets/{asset_id}   → `image/png`
 *        (the server's re-encoded copy: what finalize will print)
 *
 * The upload is authorised like PLACING A SIGNATURE (the contract must be
 * signable -- 409 on one nobody approved yet), ≤ 2 MB (413), Pillow's limits
 * (422), ≤ 100 per contract (409 with `details.limit`).
 */

export interface AnnotationAsset {
  asset_id: string;
  width_px: number;
  height_px: number;
}

export const assetsApi = {
  upload: (contractId: string, imageData: string) =>
    api.post<AnnotationAsset>(`/api/contracts/${contractId}/annotation-assets`, { image_data: imageData }),

  /** The stored PNG, with the auth header (a plain `<img src>` could not send it). */
  blob: async (contractId: string, assetId: string): Promise<Blob> => {
    const token = tokenStore.get();
    const response = await fetch(`/api/contracts/${contractId}/annotation-assets/${assetId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        'annotation_asset_failed',
        `Could not load the image (${response.status})`,
      );
    }
    return response.blob();
  },
};

/** A refused upload, in words a signer can act on. */
export function describeAssetError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 413) {
      return 'That image is too large for the server (the limit is 2 MB). Choose a smaller file.';
    }
    if (cause.status === 409) {
      if (typeof cause.details.limit === 'number') {
        return `This contract has reached the maximum of ${cause.details.limit} uploaded images (every upload counts, also deleted ones). No further image can be added.`;
      }
      return (
        'Images can only be added once the contract is approved and can be signed. ' +
        `The server said: ${cause.message}`
      );
    }
    if (cause.status === 422) {
      return `The server could not use this image: ${cause.message}. Save it as a PNG or JPEG and try again.`;
    }
    if (cause.status === 403) return 'You are not allowed to add images to this contract.';
    return `The image could not be uploaded: ${cause.message}`;
  }
  return cause instanceof Error ? `The image could not be uploaded: ${cause.message}` : 'The image could not be uploaded.';
}
