import { useState } from 'react';
import { FIELD_TYPE_LABEL, type FieldRecord } from './fieldModel';

/**
 * F2b: «Tab order» (EDITOR-V2-PLAN §7.4): the fields of each page in the
 * order the Tab key visits them. A row is dragged to its new place (or moved
 * with ↑ / ↓); the page's new order is ONE change (`tab_index` 10, 20, … of
 * that page's fields, `field.update` / the draft's `field.add`). The PDF
 * orders widgets per page (`/Tabs /S`), so a row moves inside its page only.
 */

interface Props {
  /** Every visible field, already in tab order (page by page). */
  records: readonly FieldRecord[];
  pageLabel: (pageUid: string) => string;
  onReorder: (keys: readonly string[]) => void;
  onClose: () => void;
}

export function TabOrderPanel({ records, pageLabel, onReorder, onClose }: Props) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const pages: Array<{ uid: string; rows: FieldRecord[] }> = [];
  records.forEach((record) => {
    const last = pages[pages.length - 1];
    if (last && last.uid === record.pageUid) last.rows.push(record);
    else pages.push({ uid: record.pageUid, rows: [record] });
  });

  const move = (rows: FieldRecord[], from: number, to: number) => {
    if (from === to || to < 0 || to >= rows.length) return;
    const next = [...rows];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onReorder(next.map((row) => row.key));
  };

  return (
    <section className="fm-tabs" data-testid="tab-order-panel" aria-label="Tab order">
      <header className="fm-props__head">
        <strong className="fm-props__title">Tab order</strong>
        <button type="button" className="fm-props__close" aria-label="Close the tab order" title="Close" onClick={onClose}>
          ×
        </button>
      </header>
      {records.length === 0 && <p className="fm-dock__hint">This document has no form fields yet.</p>}
      {pages.map(({ uid, rows }) => (
        <div key={uid} className="fm-tabs__page" data-testid="tab-order-page" data-page-uid={uid}>
          <h4 className="fm-tabs__heading">Page {pageLabel(uid)}</h4>
          <ol className="fm-tabs__list">
            {rows.map((record, index) => (
              <li
                key={record.key}
                className={`fm-tabs__row${dragging === record.key ? ' is-dragging' : ''}${over === record.key ? ' is-over' : ''}${
                  record.lockedReason ? ' is-locked' : ''
                }`}
                data-testid="tab-row"
                data-key={record.key}
                data-name={record.def.name}
                draggable={!record.lockedReason}
                title={record.lockedReason ?? 'Drag to change the order (or use ↑ ↓)'}
                onDragStart={(event) => {
                  setDragging(record.key);
                  event.dataTransfer.effectAllowed = 'move';
                  event.dataTransfer.setData('text/plain', record.key);
                }}
                onDragEnd={() => {
                  setDragging(null);
                  setOver(null);
                }}
                onDragOver={(event) => {
                  if (!dragging || !rows.some((row) => row.key === dragging)) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = 'move';
                  setOver(record.key);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const key = dragging ?? event.dataTransfer.getData('text/plain');
                  setDragging(null);
                  setOver(null);
                  const from = rows.findIndex((row) => row.key === key);
                  if (from < 0) return;
                  move(rows, from, index);
                }}
              >
                <span className="fm-tabs__grip" aria-hidden="true">
                  ⠿
                </span>
                <span className="fm-tabs__index">{index + 1}</span>
                <span className="fm-tabs__name">
                  {record.def.name}
                  {record.def.type === 'radio' && record.def.export_value ? ` = ${record.def.export_value}` : ''}
                </span>
                <span className="fm-tabs__type">{FIELD_TYPE_LABEL[record.def.type]}</span>
                <button
                  type="button"
                  className="fm-props__mini"
                  data-testid="tab-up"
                  aria-label={`Move ${record.def.name} earlier`}
                  title="Earlier"
                  disabled={index === 0 || Boolean(record.lockedReason)}
                  onClick={() => move(rows, index, index - 1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="fm-props__mini"
                  data-testid="tab-down"
                  aria-label={`Move ${record.def.name} later`}
                  title="Later"
                  disabled={index === rows.length - 1 || Boolean(record.lockedReason)}
                  onClick={() => move(rows, index, index + 1)}
                >
                  ↓
                </button>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}
