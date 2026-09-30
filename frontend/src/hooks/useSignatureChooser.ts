import { useCallback, useEffect, useState } from 'react';
import { describeSaveError, userSignaturesApi } from '../api/userSignatures';
import type { PlacementRequest } from './useContractSignatures';
import type { Signature, UserSignature } from '../types/contract';
import type { BBox } from '../types/field';

/**
 * What the signature chooser is open for.
 * `slot`: a template slot (the server positions the image from the template);
 * `free`: wherever the user clicked (`fieldId: null`, the `bbox` is used);
 * `bulk`: nothing is placed -- the image only arms the bulk panel;
 * `initials` (W5): a small free placement centred on `at`, its box fitted to
 * the chosen image (`placeInitials`).
 */
export type ChooserPurpose = 'slot' | 'free' | 'bulk' | 'initials';

export interface ChooserRequest {
  purpose: ChooserPurpose;
  page?: number;
  bbox?: BBox;
  fieldId?: string | null;
  /** `initials`: the click, PDF points (the box is centred on it). */
  at?: { x: number; y: number };
}

/** Which step of the chooser failed, so a page can word the refusal its way. */
export type ChooserStep = 'place' | 'stored-image' | 'stored-place';

interface Options {
  place: (request: PlacementRequest) => Promise<Signature>;
  reloadLibrary: () => Promise<void>;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  describeError: (cause: unknown, step: ChooserStep) => string;
  /** A `bulk` request: the chosen image, adopted instead of placed. */
  onAdopt?: (dataUrl: string, label: string, previewUrl?: string) => void;
  /**
   * Also save a *stored* signature to the library again when the tick is on.
   * The editor always did; the signing page never did -- so in the workspace
   * it can depend on what the chooser is open for (a free placement: yes).
   */
  saveStoredToLibrary?: boolean | ((request: ChooserRequest) => boolean);
  /**
   * W5: place an `initials` request (the box depends on the image, so the
   * caller builds it). Without it an initials request places nothing.
   */
  placeInitials?: (page: number, at: { x: number; y: number }, dataUrl: string) => Promise<unknown>;
}

/** The label a copy saved from the initials chooser carries in the library (no kind column). */
const LIBRARY_INITIALS_LABEL = 'Initials';

/**
 * The one "choose a signature" flow: draw, upload or pick a saved one, then
 * place it (a slot or a free spot) or adopt it for the bulk run.
 *
 * A base64 PNG data URL goes into the *unchanged* placement endpoint (Option
 * A). A reused library signature and a freshly drawn one are indistinguishable
 * to the server -- both arrive here as `image_data`.
 *
 * Which of the dialog's three tabs is open is the dialog's own business
 * (`SignatureModal`); the chooser keeps the tick that outlives it -- "also save
 * this to my library" -- and resets it every time it opens, so it never
 * carries over.
 */
export function useSignatureChooser({
  place,
  reloadLibrary,
  setBusy,
  setError,
  describeError,
  onAdopt,
  saveStoredToLibrary = false,
  placeInitials,
}: Options) {
  const [request, setRequest] = useState<ChooserRequest | null>(null);
  const [saveToLibrary, setSaveToLibrary] = useState(false);

  const isOpen = request !== null;
  useEffect(() => {
    if (!isOpen) return;
    setSaveToLibrary(false);
  }, [isOpen]);

  const open = useCallback((next: ChooserRequest) => setRequest(next), []);
  const close = useCallback(() => setRequest(null), []);

  /** Place at the open request. False when there is nothing to place at. */
  const placeAt = useCallback(
    async (dataUrl: string): Promise<boolean> => {
      if (!request || request.purpose === 'bulk') return false;
      if (request.purpose === 'initials') {
        if (!placeInitials || !request.at) return false;
        await placeInitials(request.page ?? 1, request.at, dataUrl);
        setRequest(null);
        return true;
      }
      if (!request.bbox) return false;
      await place({
        page: request.page ?? 1,
        bbox: request.bbox,
        fieldId: request.purpose === 'slot' ? (request.fieldId ?? null) : null,
        imageData: dataUrl,
      });
      setRequest(null);
      return true;
    },
    [request, place, placeInitials],
  );

  // The library save is best-effort -- a failure there must not undo a
  // signature that is already on the contract. A copy saved from the
  // initials chooser is labelled "Initials" (W5: the library has no kind).
  const saveCopy = useCallback(
    async (dataUrl: string, asInitials = false) => {
      try {
        await userSignaturesApi.save(
          asInitials ? { image_data: dataUrl, label: LIBRARY_INITIALS_LABEL } : { image_data: dataUrl },
        );
        void reloadLibrary();
      } catch (cause) {
        setError(
          `Signature placed, but it could not be saved to your library: ${describeSaveError(cause)}`,
        );
      }
    },
    [reloadLibrary, setError],
  );

  const adopt = useCallback(
    (dataUrl: string, label: string, previewUrl?: string) => {
      onAdopt?.(dataUrl, label, previewUrl);
      setRequest(null);
    },
    [onAdopt],
  );

  // Draw and upload paths: both pads hand over the same PNG data URL, so one
  // handler serves both. `dataUrl` is always what gets uploaded (an uploaded
  // file's ORIGINAL bytes, or the drawn PNG); `previewUrl` only decorates the
  // bulk panel.
  const confirm = useCallback(
    async (dataUrl: string, previewUrl?: string) => {
      setBusy(true);
      setError(null);
      try {
        let accepted = false;
        if (request?.purpose === 'bulk') {
          adopt(dataUrl, 'New signature', previewUrl);
          accepted = true;
        } else {
          accepted = await placeAt(dataUrl);
        }
        if (accepted && saveToLibrary) await saveCopy(dataUrl, request?.purpose === 'initials');
      } catch (cause) {
        setError(describeError(cause, 'place'));
      } finally {
        setBusy(false);
      }
    },
    [request, adopt, placeAt, saveToLibrary, saveCopy, setBusy, setError, describeError],
  );

  // Reuse path: fetch the chosen library signature's stored PNG (through the
  // ownership-checked image route) and either place it or arm the bulk panel.
  const chooseStored = useCallback(
    async (signature: UserSignature) => {
      setBusy(true);
      setError(null);
      let step: ChooserStep = 'stored-image';
      try {
        const dataUrl = await userSignaturesApi.imageAsDataUrl(signature.id);
        step = 'stored-place';
        if (request?.purpose === 'bulk') {
          adopt(dataUrl, signature.label ?? 'Saved signature');
        } else {
          const resaves =
            typeof saveStoredToLibrary === 'function'
              ? request !== null && saveStoredToLibrary(request)
              : saveStoredToLibrary;
          const accepted = await placeAt(dataUrl);
          if (accepted && resaves && saveToLibrary) await saveCopy(dataUrl);
        }
      } catch (cause) {
        setError(describeError(cause, step));
      } finally {
        setBusy(false);
      }
    },
    [
      request,
      adopt,
      placeAt,
      saveStoredToLibrary,
      saveToLibrary,
      saveCopy,
      setBusy,
      setError,
      describeError,
    ],
  );

  return { request, open, close, saveToLibrary, setSaveToLibrary, confirm, chooseStored };
}

export type SignatureChooser = ReturnType<typeof useSignatureChooser>;
