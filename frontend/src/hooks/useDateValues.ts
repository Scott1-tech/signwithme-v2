import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * One ISO date (`yyyy-mm-dd`) per template date slot, for one contract.
 *
 * These are what finalize sends as `dates`; `selectedDate` is only the
 * convenience source for "apply to all" and the bulk run. The values are kept
 * in `localStorage` under `swm.dates.<contractId>` so a reload (or a later
 * visit) finds them again. Storage is a preference, never authority: anything
 * stored that is not a plain ISO date under a plain key is dropped in silence,
 * and nothing is filled in on its own -- a contract with no stored dates opens
 * with none.
 */

export const datesStorageKey = (contractId: string) => `swm.dates.${contractId}`;

/** More than any template has; a bigger stored map is not ours. */
const MAX_STORED_DATES = 500;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export type DateValues = Record<string, string>;

export function sanitizeDateValues(raw: unknown): DateValues {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const clean: DateValues = {};
  let kept = 0;
  for (const [fieldId, value] of Object.entries(raw as Record<string, unknown>)) {
    if (kept >= MAX_STORED_DATES) break;
    if (!fieldId || fieldId.length > 200) continue;
    if (typeof value !== 'string' || !ISO_DATE.test(value)) continue;
    if (Number.isNaN(Date.parse(value))) continue;
    clean[fieldId] = value;
    kept += 1;
  }
  return clean;
}

export function loadDateValues(contractId: string | undefined): DateValues {
  if (!contractId) return {};
  try {
    const raw = localStorage.getItem(datesStorageKey(contractId));
    return raw ? sanitizeDateValues(JSON.parse(raw)) : {};
  } catch {
    // Unreadable JSON or no storage at all (private mode): start empty.
    return {};
  }
}

function saveDateValues(contractId: string | undefined, values: DateValues): void {
  if (!contractId) return;
  try {
    const clean = sanitizeDateValues(values);
    if (Object.keys(clean).length === 0) localStorage.removeItem(datesStorageKey(contractId));
    else localStorage.setItem(datesStorageKey(contractId), JSON.stringify(clean));
  } catch {
    /* private mode: the values simply do not persist */
  }
}

export function useDateValues(contractId: string | undefined) {
  const [values, setValuesState] = useState<DateValues>(() => loadDateValues(contractId));
  // The global date is only a convenience source for "apply to all"; what is
  // actually sent at finalize is `values`, one ISO value per date slot. It
  // starts EMPTY on every visit: no automatic date (see `lib/signing.ts`).
  const [selectedDate, setSelectedDate] = useState('');
  const loadedFor = useRef(contractId);

  useEffect(() => {
    if (loadedFor.current === contractId) return;
    loadedFor.current = contractId;
    setValuesState(loadDateValues(contractId));
  }, [contractId]);

  /** Same contract as `useState`'s setter; every change is written through. */
  const setValues = useCallback(
    (next: DateValues | ((current: DateValues) => DateValues)) => {
      setValuesState((current) => {
        const resolved = typeof next === 'function' ? next(current) : next;
        saveDateValues(contractId, resolved);
        return resolved;
      });
    },
    [contractId],
  );

  const set = useCallback(
    (fieldId: string, value: string) =>
      setValues((current) => ({ ...current, [fieldId]: value })),
    [setValues],
  );

  /** Every listed slot gets `value`; anything else is dropped (as before). */
  const fillAll = useCallback(
    (fieldIds: readonly string[], value: string) =>
      setValues(Object.fromEntries(fieldIds.map((fieldId) => [fieldId, value]))),
    [setValues],
  );

  const remove = useCallback(
    (fieldIds: readonly string[]) =>
      setValues((current) => {
        const next = { ...current };
        fieldIds.forEach((fieldId) => {
          delete next[fieldId];
        });
        return next;
      }),
    [setValues],
  );

  return { values, setValues, set, fillAll, remove, selectedDate, setSelectedDate };
}

export type DateValuesState = ReturnType<typeof useDateValues>;
