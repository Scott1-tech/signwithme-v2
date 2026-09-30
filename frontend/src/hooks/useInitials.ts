import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { signaturesApi } from '../api/signatures';
import {
  blobToDataUrl,
  fitInitialsBox,
  hasInitialAt,
  initialsBoxOnPage,
  isInitialsRow,
  loadInitialIds,
  saveInitialIds,
  storedImageSizeOf,
} from '../lib/initials';
import type { PageSizePt } from '../lib/layout';
import { runPool } from '../lib/pool';
import { BULK_CONCURRENCY } from '../lib/signing';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';
import type { PlacementRequest } from './useContractSignatures';

/** A run in flight: «Put on every page» or «Remove all initials». */
export interface InitialsProgress {
  kind: 'every-page' | 'remove-all';
  done: number;
  total: number;
}

/** What the last run did, in words, plus what failed (page and reason). */
export interface InitialsReport {
  kind: 'every-page' | 'remove-all';
  text: string;
  failures: Array<{ page: number; reason: string }>;
}

interface Options {
  contractId: string | undefined;
  /** Every signature row of the contract (slot and free), as the server holds them. */
  rows: readonly Signature[];
  place: (request: PlacementRequest) => Promise<Signature>;
  remove: (signatureId: string) => Promise<void>;
  pageCount: number;
  pageSizeOf: (page: number | null) => PageSizePt | null;
  /** Signing is blocked (a server write would be refused): nothing runs. */
  blocked: boolean;
  setBusy: (busy: boolean) => void;
  describeError: (cause: unknown, fallback: string) => string;
}

/**
 * Initials on one contract (W5, plan §7-7): which free rows ARE initials
 * (`lib/initials.ts`: the 60 × 24 pt box, or an id this browser recorded),
 * placing one, «Put on every page» and «Remove all initials».
 *
 * Everything here is a SERVER WRITE through the unchanged endpoints -- one
 * POST per placement (`field_id: null`), one DELETE per removal -- so nothing
 * is on the undo history, and the list only follows what the server answered.
 * The bulk runs go through `runPool` with `BULK_CONCURRENCY` (3) lanes, like
 * the bulk signing fallback; a failure on one page never stops the others and
 * is reported by page, with the server's reason.
 */
export function useInitials({
  contractId,
  rows,
  place,
  remove,
  pageCount,
  pageSizeOf,
  blocked,
  setBusy,
  describeError,
}: Options) {
  const [recorded, setRecorded] = useState<Set<string>>(() => loadInitialIds(contractId));
  const loadedFor = useRef(contractId);
  useEffect(() => {
    if (loadedFor.current === contractId) return;
    loadedFor.current = contractId;
    setRecorded(loadInitialIds(contractId));
  }, [contractId]);

  const record = useCallback(
    (id: string) =>
      setRecorded((current) => {
        if (current.has(id)) return current;
        const next = new Set(current);
        next.add(id);
        saveInitialIds(contractId, next);
        return next;
      }),
    [contractId],
  );
  const forget = useCallback(
    (ids: readonly string[]) =>
      setRecorded((current) => {
        if (!ids.some((id) => current.has(id))) return current;
        const next = new Set(current);
        ids.forEach((id) => next.delete(id));
        saveInitialIds(contractId, next);
        return next;
      }),
    [contractId],
  );

  const isInitial = useCallback((row: Signature) => isInitialsRow(row, recorded), [recorded]);
  /** The ids of the rows that are initials, in the server's order. */
  const initialIds = useMemo(
    () => new Set(rows.filter((row) => isInitialsRow(row, recorded)).map((row) => row.id)),
    [rows, recorded],
  );

  // The async runs read the rows as they are NOW, not as they were when the
  // button was pressed.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  const [progress, setProgress] = useState<InitialsProgress | null>(null);
  const [report, setReport] = useState<InitialsReport | null>(null);
  const running = progress !== null;

  /**
   * One initial from the chooser: fitted inside 60 × 24 pt, centred on `at`,
   * at the ratio of the image the SERVER will keep (cropped to its ink), not
   * of the raw pad -- see `storedImageSizeOf`.
   */
  const placeAt = useCallback(
    async (page: number, at: { x: number; y: number }, dataUrl: string): Promise<Signature> => {
      const natural = await storedImageSizeOf(dataUrl);
      const bbox = fitInitialsBox(natural, at, pageSizeOf(page));
      const created = await place({ page, bbox, fieldId: null, imageData: dataUrl });
      record(created.id);
      return created;
    },
    [place, pageSizeOf, record],
  );

  /** «Put on every page»: the source's corner on every other page, one POST each, three at a time. */
  const putOnEveryPage = useCallback(
    async (source: Signature) => {
      if (!contractId || blocked || running) return;
      const from = pageSizeOf(source.page_number);
      const targets: Array<{ page: number; bbox: BBox }> = [];
      let skipped = 0;
      for (let page = 1; page <= pageCount; page += 1) {
        if (page === source.page_number) continue;
        const bbox = initialsBoxOnPage(source.bbox, from, pageSizeOf(page) ?? from);
        if (hasInitialAt(rowsRef.current, page, bbox, (row) => isInitialsRow(row, recorded))) {
          skipped += 1;
          continue;
        }
        targets.push({ page, bbox });
      }
      if (targets.length === 0) {
        setReport({
          kind: 'every-page',
          text: pageCount <= 1 ? 'This document has one page.' : 'Every page already has these initials.',
          failures: [],
        });
        return;
      }
      setReport(null);
      setBusy(true);
      setProgress({ kind: 'every-page', done: 0, total: targets.length });
      const failures: InitialsReport['failures'] = [];
      let placed = 0;
      try {
        let imageData: string;
        try {
          imageData = await blobToDataUrl(await signaturesApi.imageBlob(contractId, source.id));
        } catch (cause) {
          setReport({
            kind: 'every-page',
            text: `Nothing was placed: ${describeError(cause, 'the initials image could not be read')}`,
            failures: [],
          });
          return;
        }
        let done = 0;
        await runPool(targets, BULK_CONCURRENCY, async (target) => {
          try {
            const created = await place({ page: target.page, bbox: target.bbox, fieldId: null, imageData });
            record(created.id);
            placed += 1;
          } catch (cause) {
            failures.push({ page: target.page, reason: describeError(cause, 'Could not place the initials') });
          } finally {
            done += 1;
            setProgress({ kind: 'every-page', done, total: targets.length });
          }
        });
        failures.sort((a, b) => a.page - b.page);
        const parts = [`Initials placed on ${placed} of ${targets.length} page${targets.length === 1 ? '' : 's'}`];
        if (skipped > 0) parts.push(`${skipped} already had them`);
        setReport({
          kind: 'every-page',
          text:
            parts.join(' — ') +
            (failures.length > 0
              ? `. Not placed on ${failures.map((failure) => `p.${failure.page}`).join(', ')}.`
              : '.'),
          failures,
        });
      } finally {
        setProgress(null);
        setBusy(false);
      }
    },
    [contractId, blocked, running, pageSizeOf, pageCount, recorded, setBusy, describeError, place, record],
  );

  /** «Remove all initials»: one DELETE per initial (asked first -- it cannot be undone). */
  const removeAll = useCallback(async () => {
    if (!contractId || running) return;
    const targets = rowsRef.current.filter((row) => isInitialsRow(row, recorded));
    if (targets.length === 0) return;
    const count = targets.length;
    if (
      !window.confirm(
        `Remove all ${count} initials from this contract? They are stored on the server — this cannot be undone.`,
      )
    ) {
      return;
    }
    setReport(null);
    setBusy(true);
    setProgress({ kind: 'remove-all', done: 0, total: count });
    const failures: InitialsReport['failures'] = [];
    const removed: string[] = [];
    let done = 0;
    try {
      await runPool(targets, BULK_CONCURRENCY, async (row) => {
        try {
          await remove(row.id);
          removed.push(row.id);
        } catch (cause) {
          failures.push({ page: row.page_number, reason: describeError(cause, 'Could not remove the initials') });
        } finally {
          done += 1;
          setProgress({ kind: 'remove-all', done, total: count });
        }
      });
      forget(removed);
      failures.sort((a, b) => a.page - b.page);
      setReport({
        kind: 'remove-all',
        text:
          `Removed ${removed.length} of ${count} initials` +
          (failures.length > 0
            ? `. Still there: ${failures.map((failure) => `p.${failure.page}`).join(', ')}.`
            : '.'),
        failures,
      });
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }, [contractId, running, recorded, setBusy, remove, describeError, forget]);

  /** A single initial removed with its own button: forget its id too. */
  const forgetOne = useCallback((id: string) => forget([id]), [forget]);

  return {
    initialIds,
    isInitial,
    placeAt,
    putOnEveryPage,
    removeAll,
    forgetOne,
    progress,
    report,
    clearReport: () => setReport(null),
  };
}

export type InitialsApi = ReturnType<typeof useInitials>;
