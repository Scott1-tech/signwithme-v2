import { useMemo } from 'react';
import { DatePicker, formatForContract } from './DatePicker';
import type { SignatureSlot } from '../types/comparison';
import type { Signature } from '../types/contract';
import '../styles/signing.css';

/**
 * The slot list in the side panel, grouped by the page the slot is on.
 *
 * It used to be one flat run of cards: on a fourteen-page contract the list was
 * longer than the screen and nothing said which part of the document a card
 * belonged to. Now every page is its own collapsible group with a "how many are
 * done" tally, and each entry carries a button that scrolls the viewer to it.
 *
 * The anchors the rest of the page relies on are unchanged: every field id
 * still has an element with `id = cardId(fieldId)`, so `focusSlot` keeps
 * working, and a collapsed group is hidden with `hidden` rather than unmounted
 * so that anchor never disappears from the document.
 */

/** One line of the list: a signature (with its own date) or a lone date. */
interface Entry {
  key: string;
  page: number | null;
  signature: SignatureSlot | null;
  date: SignatureSlot | null;
}

interface Group {
  page: number | null;
  entries: Entry[];
  done: number;
  total: number;
}

interface Props {
  mode: 'bulk' | 'slot';
  sigSlots: SignatureSlot[];
  unpairedDateSlots: SignatureSlot[];
  pairedDateFor: (slot: SignatureSlot) => SignatureSlot | null;
  signatureForSlot: (fieldId: string) => Signature | null;
  dateValues: Record<string, string>;
  focusedSlotId: string | null;
  collapsedPages: ReadonlySet<number>;
  onTogglePage: (page: number) => void;
  /** Scroll the viewer to this slot's page and mark it as the active one. */
  onJump: (slot: SignatureSlot) => void;
  onSign: (slot: SignatureSlot) => void;
  onRemove: (signatureId: string) => void;
  onDateChange: (fieldId: string, value: string) => void;
  busy: boolean;
  blocked: boolean;
  cardId: (fieldId: string) => string;
}

const pageLabel = (page: number | null) => (page ? `Page ${page}` : 'Page unknown');

export function SlotCardList({
  mode,
  sigSlots,
  unpairedDateSlots,
  pairedDateFor,
  signatureForSlot,
  dateValues,
  focusedSlotId,
  collapsedPages,
  onTogglePage,
  onJump,
  onSign,
  onRemove,
  onDateChange,
  busy,
  blocked,
  cardId,
}: Props) {
  const groups = useMemo<Group[]>(() => {
    const entries: Entry[] = [
      ...sigSlots.map((signature) => ({
        key: signature.field_id,
        page: signature.page,
        signature,
        date: pairedDateFor(signature),
      })),
      ...unpairedDateSlots.map((date) => ({
        key: date.field_id,
        page: date.page,
        signature: null,
        date,
      })),
    ];

    // Insertion order is the template's order; `Map` keeps it, so page 3 never
    // jumps above page 1 just because its first slot was listed later.
    const byPage = new Map<number | null, Entry[]>();
    entries.forEach((entry) => {
      const list = byPage.get(entry.page);
      if (list) list.push(entry);
      else byPage.set(entry.page, [entry]);
    });

    return [...byPage.entries()]
      .sort((a, b) => (a[0] ?? Number.MAX_SAFE_INTEGER) - (b[0] ?? Number.MAX_SAFE_INTEGER))
      .map(([page, list]) => {
        let done = 0;
        let total = 0;
        list.forEach((entry) => {
          if (entry.signature) {
            total += 1;
            if (signatureForSlot(entry.signature.field_id)) done += 1;
          }
          if (entry.date) {
            total += 1;
            if (dateValues[entry.date.field_id]) done += 1;
          }
        });
        return { page, entries: list, done, total };
      });
  }, [sigSlots, unpairedDateSlots, pairedDateFor, signatureForSlot, dateValues]);

  if (groups.length === 0) return null;

  const jumpButton = (slot: SignatureSlot) =>
    slot.page ? (
      <button
        type="button"
        className="slot-card__jump"
        aria-label={`${slot.label}: show on page ${slot.page}`}
        onClick={() => onJump(slot)}
      >
        Show
      </button>
    ) : null;

  return (
    <div className="slot-groups">
      {groups.map((group) => {
        const collapsed = group.page !== null && collapsedPages.has(group.page);
        const bodyId = `slot-group-${group.page ?? 'none'}`;
        return (
          <section className="slot-group" key={group.page ?? 'none'}>
            <button
              type="button"
              className="slot-group__header"
              aria-expanded={!collapsed}
              aria-controls={bodyId}
              onClick={() => group.page !== null && onTogglePage(group.page)}
            >
              <span className="slot-group__caret" aria-hidden="true">
                ▾
              </span>
              {pageLabel(group.page)}
              <span
                className={`slot-group__tally${
                  group.done === group.total ? ' slot-group__tally--done' : ''
                }`}
              >
                {group.done}/{group.total} filled
              </span>
            </button>

            <div className="slot-group__body" id={bodyId} hidden={collapsed}>
              {group.entries.map((entry) => {
                const { signature: sigSlot, date } = entry;
                const placed = sigSlot ? signatureForSlot(sigSlot.field_id) : null;
                const dateValue = date ? (dateValues[date.field_id] ?? '') : '';

                // --- compact status list (the "one for all" mode) -------------
                if (mode === 'bulk') {
                  return (
                    <div className="slot-row" key={entry.key}>
                      <span className="slot-row__name">
                        <span id={sigSlot ? cardId(sigSlot.field_id) : undefined}>
                          {(sigSlot ?? date)?.label}
                        </span>
                        {date && (
                          <em className="slot-row__meta" id={cardId(date.field_id)}>
                            {sigSlot ? 'Date: ' : ''}
                            {dateValue ? formatForContract(dateValue) : 'empty'}
                          </em>
                        )}
                      </span>
                      <span
                        className={`slot-row__status${
                          (sigSlot ? Boolean(placed) : Boolean(dateValue))
                            ? ' slot-row__status--done'
                            : ''
                        }`}
                      >
                        {sigSlot
                          ? placed
                            ? 'Signed ✓'
                            : 'Not signed'
                          : dateValue
                            ? 'Filled ✓'
                            : 'Empty'}
                      </span>
                      {jumpButton((sigSlot ?? date) as SignatureSlot)}
                    </div>
                  );
                }

                // --- full card (the "each one separately" mode) ---------------
                if (!sigSlot && date) {
                  return (
                    <div className="slot-card" key={entry.key}>
                      <div className="slot-card__head">
                        <div
                          id={cardId(date.field_id)}
                          className={`slot-card__date${
                            focusedSlotId === date.field_id ? ' slot-card--focused' : ''
                          }`}
                        >
                          <DatePicker
                            label={`${date.label}${date.page ? ` · p.${date.page}` : ''}${
                              date.required ? '' : ' (optional)'
                            }`}
                            value={dateValue}
                            onChange={(value) => onDateChange(date.field_id, value)}
                          />
                        </div>
                        {jumpButton(date)}
                      </div>
                    </div>
                  );
                }

                if (!sigSlot) return null;

                return (
                  <div
                    key={entry.key}
                    id={cardId(sigSlot.field_id)}
                    className={`slot-card${
                      focusedSlotId === sigSlot.field_id ? ' slot-card--focused' : ''
                    }`}
                  >
                    <div className="slot-card__row">
                      <div>
                        <div className="slot-card__title">{sigSlot.label}</div>
                        <div className="slot-card__meta">
                          {pageLabel(sigSlot.page)}
                          {sigSlot.required ? '' : ' · optional'}
                          {sigSlot.bbox ? '' : ' · no position on page'}
                        </div>
                      </div>
                      <span
                        className={`slot-card__status${placed ? ' slot-card__status--done' : ''}`}
                      >
                        {placed ? 'Signed ✓' : 'Not signed'}
                      </span>
                    </div>
                    <div className="slot-card__actions">
                      {placed ? (
                        <button
                          type="button"
                          className="button button--link"
                          disabled={busy}
                          onClick={() => onRemove(placed.id)}
                        >
                          Remove
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="button button--primary button--small"
                          disabled={busy || blocked || !sigSlot.bbox}
                          onClick={() => onSign(sigSlot)}
                        >
                          Sign
                        </button>
                      )}
                      {jumpButton(sigSlot)}
                    </div>
                    {date && (
                      <div
                        id={cardId(date.field_id)}
                        className={`slot-card__date${
                          focusedSlotId === date.field_id ? ' slot-card--focused' : ''
                        }`}
                      >
                        <DatePicker
                          label={`${date.label}${date.page ? ` · p.${date.page}` : ''}${
                            date.required ? '' : ' (optional)'
                          }`}
                          value={dateValue}
                          onChange={(value) => onDateChange(date.field_id, value)}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
