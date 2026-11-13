import {
  ANNOTATION_COLOR_HEX,
  isDraw,
  isImage,
  isInk,
  isMarkup,
  isNote,
  isShape,
  isTextual,
  isWhiteout,
  type Annotation,
} from '../lib/annotations';
import type { InitialsProgress, InitialsReport } from '../hooks/useInitials';
import type { Signature } from '../types/contract';

interface Props {
  annotations: Annotation[];
  freeSignatures: Signature[];
  selectedAnnotationId: string | null;
  selectedSignatureId: string | null;
  busy: boolean;
  /** Bring the page to it and select it. */
  onShowAnnotation: (annotation: Annotation) => void;
  /** «Revert» on an edited line: remove its whole group (one undo step). */
  onRevertGroup: (annotation: Annotation) => void;
  /**
   * F1b: «Convert to real edit» on a legacy edited line (whiteout + text):
   * the text becomes a `text.replace` of the document itself and the white
   * box goes. Absent where the document editor is not available.
   */
  onConvertGroup?: (annotation: Annotation) => void;
  /** F1b: the annotations are kept on the server (`useMarkupSync`), not only here. */
  serverBacked?: boolean;
  onShowSignature: (signature: Signature) => void;
  onRemoveSignature: (signatureId: string) => void;
  /** W5: which free rows are initials. */
  initialIds?: ReadonlySet<string>;
  /** W5: «Every page» / «Remove all initials», their progress and report. */
  initials?: {
    /** Why the server writes are off (an unapproved contract), else `null`. */
    blockedReason: string | null;
    progress: InitialsProgress | null;
    report: InitialsReport | null;
    onEveryPage: (signature: Signature) => void;
    onRemoveAll: () => void;
  };
}

const NO_INITIALS: ReadonlySet<string> = new Set();
const NO_INITIALS_API: NonNullable<Props['initials']> = {
  blockedReason: null,
  progress: null,
  report: null,
  onEveryPage: () => undefined,
  onRemoveAll: () => undefined,
};

const KIND_LABEL: Record<Annotation['kind'], string> = {
  text: 'Text',
  date: 'Date',
  check: 'Check mark',
  cross: 'Cross mark',
  whiteout: 'Whiteout',
  highlight: 'Highlight',
  underline: 'Underline',
  strikeout: 'Strikeout',
  ink: 'Freehand',
  rect: 'Rectangle',
  ellipse: 'Ellipse',
  line: 'Line',
  arrow: 'Arrow',
  note: 'Note',
  image: 'Image',
  textbox: 'Text box',
  stamp: 'Stamp',
  square_mark: 'Filled square',
};

/** A note's text on one line, cut short. */
const noteSummary = (text: string) => {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > 40 ? `${flat.slice(0, 39)}…` : flat;
};

/** What follows the kind on a row: a run's text, a markup's line count, a note's text, a drawing's detail. */
function detailOf(item: Annotation): string {
  if (isTextual(item)) return `: ${item.text}`;
  if (isMarkup(item)) return ` (${item.rects.length} line${item.rects.length === 1 ? '' : 's'})`;
  if (isNote(item)) return `: ${noteSummary(item.text)}`;
  if (isInk(item)) return ` (${item.paths.length} stroke${item.paths.length === 1 ? '' : 's'})`;
  if (isShape(item)) return item.fill ? ' (filled)' : '';
  if (isImage(item)) return ` (${Math.round(item.w_pt)} × ${Math.round(item.h_pt)} pt)`;
  return '';
}

/** One row of the list: an annotation, or an edited line (a whole group). */
type Row =
  | { type: 'item'; item: Annotation }
  | { type: 'group'; groupId: string; primary: Annotation; members: Annotation[] };

function rowsOf(annotations: readonly Annotation[]): Row[] {
  const rows: Row[] = [];
  const seen = new Set<string>();
  annotations.forEach((item) => {
    if (!item.group_id) {
      rows.push({ type: 'item', item });
      return;
    }
    if (seen.has(item.group_id)) return;
    seen.add(item.group_id);
    const members = annotations.filter((entry) => entry.group_id === item.group_id);
    const primary = members.find((entry) => !isWhiteout(entry)) ?? members[0];
    rows.push({ type: 'group', groupId: item.group_id, primary, members });
  });
  return rows;
}

/**
 * "Objects": everything placed on the page that is not a template slot -- the
 * annotations (kept in this browser, on the undo history) and the free
 * signatures (server rows, not on it). One row per object, in the order the
 * finalize body sends them, each with its page and a way to jump to it.
 *
 * An edited line (WE: whiteout + text sharing a `group_id`) is ONE row,
 * «Edited text», with «Show» and «Revert» -- revert takes the whole group
 * away, and the original line is visible again.
 */
export function ObjectsSection({
  annotations,
  freeSignatures,
  selectedAnnotationId,
  selectedSignatureId,
  busy,
  onShowAnnotation,
  onRevertGroup,
  onConvertGroup,
  serverBacked = false,
  onShowSignature,
  onRemoveSignature,
  initialIds = NO_INITIALS,
  initials = NO_INITIALS_API,
}: Props) {
  const rows = rowsOf(annotations);
  const initialCount = freeSignatures.filter((signature) => initialIds.has(signature.id)).length;
  const running = initials.progress !== null;
  return (
    <div className="card" data-testid="objects-section">
      {/* Only the browser-only case needs saying: it is the surprising one. */}
      {!serverBacked && (
        <p className="hint" data-testid="editor-hint" data-storage="browser">
          Saved in this browser only until the final contract is generated.
        </p>
      )}

      <h3 className="objects__heading">
        Annotations <span className="objects__count">({annotations.length})</span>
      </h3>
      {annotations.length === 0 ? (
        <p className="hint" data-testid="objects-empty">
          Nothing placed yet.
        </p>
      ) : (
        <ul className="list objects__list" data-testid="objects-list">
          {rows.map((row) => {
            if (row.type === 'group') {
              const { primary, members, groupId } = row;
              const selected = members.some((member) => member.id === selectedAnnotationId);
              return (
                <li
                  key={`group-${groupId}`}
                  className={`list__item objects__item objects__item--edited${selected ? ' is-selected' : ''}`}
                  data-testid={`object-group-${groupId}`}
                  data-kind="edited-text"
                  data-page={primary.page}
                  data-members={members.length}
                >
                  <span className="objects__label">
                    <span className="objects__badge">Edited text</span>
                    {isTextual(primary) ? `: ${primary.text}` : ' (erased)'}
                    <span className="objects__page"> · p.{primary.page}</span>
                  </span>
                  <span className="objects__actions">
                    <button
                      type="button"
                      className="button button--link"
                      data-testid={`object-show-${groupId}`}
                      onClick={() => onShowAnnotation(primary)}
                    >
                      Show
                    </button>
                    <button
                      type="button"
                      className="button button--link"
                      data-testid={`object-revert-${groupId}`}
                      title="Remove this edit — the original line shows again"
                      onClick={() => onRevertGroup(primary)}
                    >
                      Revert
                    </button>
                    {onConvertGroup && isTextual(primary) && (
                      <button
                        type="button"
                        className="button button--link"
                        data-testid={`object-convert-${groupId}`}
                        title="Make this a real edit of the document's text (no white box)"
                        onClick={() => onConvertGroup(primary)}
                      >
                        Convert to real edit
                      </button>
                    )}
                  </span>
                </li>
              );
            }
            const { item } = row;
            return (
              <li
                key={item.id}
                className={`list__item objects__item${item.id === selectedAnnotationId ? ' is-selected' : ''}`}
                data-testid={`object-ann-${item.id}`}
                data-kind={item.kind}
                data-page={item.page}
              >
                <span className="objects__label">
                  {(isMarkup(item) || (isDraw(item) && !isImage(item))) && 'color' in item && (
                    <span
                      className={`objects__swatch objects__swatch--${item.kind}`}
                      style={{ background: ANNOTATION_COLOR_HEX[item.color] }}
                      aria-hidden="true"
                    />
                  )}
                  {KIND_LABEL[item.kind]}
                  {detailOf(item)}
                  <span className="objects__page"> · p.{item.page}</span>
                </span>
                <button
                  type="button"
                  className="button button--link"
                  onClick={() => onShowAnnotation(item)}
                >
                  Show
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <h3 className="objects__heading">
        Free signatures <span className="objects__count">({freeSignatures.length})</span>
      </h3>
      {freeSignatures.length === 0 ? (
        <p className="hint">None.</p>
      ) : (
        <ul className="list objects__list" data-testid="objects-signatures">
          {freeSignatures.map((signature) => {
            const isInitials = initialIds.has(signature.id);
            return (
              <li
                key={signature.id}
                className={`list__item objects__item${isInitials ? ' objects__item--initials' : ''}${
                  signature.id === selectedSignatureId ? ' is-selected' : ''
                }`}
                data-testid={`object-sig-${signature.id}`}
                data-kind={isInitials ? 'initials' : 'signature'}
                data-page={signature.page_number}
              >
                <span className="objects__label">
                  {isInitials ? 'Initials' : 'Signature'}
                  <span className="objects__page"> · p.{signature.page_number}</span>
                </span>
                <span className="objects__actions">
                  <button
                    type="button"
                    className="button button--link"
                    onClick={() => onShowSignature(signature)}
                  >
                    Show
                  </button>
                  {isInitials && (
                    <button
                      type="button"
                      className="button button--link"
                      data-testid={`initials-every-page-${signature.id}`}
                      disabled={busy || running || Boolean(initials.blockedReason)}
                      title={
                        initials.blockedReason ??
                        'Put these initials on every other page, in the same corner (one server placement per page — not undoable)'
                      }
                      onClick={() => initials.onEveryPage(signature)}
                    >
                      Every page
                    </button>
                  )}
                  <button
                    type="button"
                    className="button button--link"
                    disabled={busy}
                    onClick={() => onRemoveSignature(signature.id)}
                  >
                    Remove
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {/* W5: the initials' bulk actions -- server writes, three at a time, never undoable. */}
      {(initialCount > 0 || initials.progress || initials.report) && (
        <div className="objects__initials" data-testid="initials-panel">
          {initialCount > 0 && (
            <button
              type="button"
              className="button button--ghost"
              data-testid="initials-remove-all"
              disabled={busy || running}
              title="Delete every initial on this contract (not the signatures) — cannot be undone"
              onClick={initials.onRemoveAll}
            >
              Remove all initials ({initialCount})
            </button>
          )}
          {initials.progress && (
            <p className="hint" role="status" data-testid="initials-progress">
              {initials.progress.kind === 'every-page' ? 'Placing initials…' : 'Removing initials…'}{' '}
              {initials.progress.done} / {initials.progress.total}
            </p>
          )}
          {initials.report && !initials.progress && (
            <div
              className={`alert ${initials.report.failures.length > 0 ? 'alert--warn' : 'alert--good'}`}
              role="status"
              data-testid="initials-report"
            >
              <p>{initials.report.text}</p>
              {initials.report.failures.length > 0 && (
                <ul className="objects__failures" data-testid="initials-failures">
                  {initials.report.failures.map((failure) => (
                    <li key={`${failure.page}-${failure.reason}`}>
                      p.{failure.page}: {failure.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
