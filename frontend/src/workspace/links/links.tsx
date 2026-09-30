import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  documentApi,
  type DocumentOp,
  type LinkAddOp,
  type LinkTarget,
  type LinkUpdateOp,
  type PageLinkOut,
  type Rect,
} from '../../api/document';
import { frameLayerStyle, ptToPx, unrotateVector, UNROTATED, type PageFrame } from '../../lib/viewport';
import type { DocumentOps } from '../session/useDocumentOps';
import { newOpId } from '../session/useEditSession';

/**
 * F2 «Links» (EDITOR-V2-PLAN §7.2, B2 `link.add` / `link.update` /
 * `link.remove`): the page's links as the draft will make them -- the
 * server's (`GET …/versions/{n}/pages/{uid}/links`) with the session's link
 * ops applied -- framed while the tool is armed. A frame is a link being
 * EDITED: a click opens its dialog, a drag moves it, the corner resizes it,
 * and it is never followed. Every change is one op (one undo step).
 */

export interface ShownLink {
  /** `lk<xref>` for a link of the file, the op id for a new one. */
  key: string;
  linkId: string | null;
  opId: string | null;
  pageUid: string;
  rect: Rect;
  target: LinkTarget | { type: 'other' };
}

const URL_RE = /^(https?:\/\/[^\s/$.?#][^\s]*|mailto:[^\s@]+@[^\s@]+\.[^\s@]+)$/i;

/** Why a URL is refused (the server takes http(s) with a host, or mailto: one address). */
export function urlProblem(value: string): string | null {
  const url = value.trim();
  if (!url) return 'Type the address the link opens.';
  if (/^(javascript|file|data):/i.test(url)) return 'Only web (http / https) and e-mail (mailto:) links are allowed.';
  if (!URL_RE.test(url)) return 'Start the address with https://, http:// or mailto: (one e-mail address).';
  return null;
}

function targetOut(link: PageLinkOut['target']): ShownLink['target'] {
  if (link.type === 'uri') return { type: 'uri', uri: link.uri };
  if (link.type === 'page' && link.page_uid) return { type: 'page', page_uid: link.page_uid };
  return { type: 'other' };
}

/** The links of one page with the ops applied. */
export function shownLinks(server: readonly PageLinkOut[], ops: readonly DocumentOp[], uid: string): ShownLink[] {
  const removed = new Set<string>();
  const updated = new Map<string, LinkUpdateOp>();
  const added: LinkAddOp[] = [];
  ops.forEach((op) => {
    if (op.kind === 'link.remove' && op.page_uid === uid) removed.add(op.link_id);
    if (op.kind === 'link.update' && op.page_uid === uid) updated.set(op.link_id, op);
    if (op.kind === 'link.add' && op.page_uid === uid) added.push(op);
  });
  const list: ShownLink[] = [];
  server.forEach((link) => {
    if (removed.has(link.link_id)) return;
    const update = updated.get(link.link_id);
    list.push({
      key: link.link_id,
      linkId: link.link_id,
      opId: update?.id ?? null,
      pageUid: uid,
      rect: update?.rect ?? link.rect,
      target: update?.target ?? targetOut(link.target),
    });
  });
  added.forEach((op) =>
    list.push({ key: op.id, linkId: null, opId: op.id, pageUid: uid, rect: op.rect, target: op.target }),
  );
  return list;
}

/** The server's links of the pages on screen, fetched once per version and page. */
export function usePageLinks(contractId: string | undefined, versionNo: number | null, uids: readonly string[], active: boolean) {
  const [links, setLinks] = useState<Map<string, PageLinkOut[]>>(new Map());
  const asked = useRef(new Set<string>());
  useEffect(() => {
    asked.current = new Set();
    setLinks(new Map());
  }, [contractId, versionNo]);
  useEffect(() => {
    if (!active || !contractId || versionNo === null) return;
    uids.forEach((uid) => {
      const key = `${versionNo}|${uid}`;
      if (asked.current.has(key)) return;
      asked.current.add(key);
      documentApi
        .pageLinks(contractId, versionNo, uid)
        .then((list) => setLinks((current) => new Map(current).set(uid, Array.isArray(list) ? list : [])))
        .catch(() => setLinks((current) => new Map(current).set(uid, [])));
    });
  }, [active, contractId, versionNo, uids]);
  return links;
}

/** The link dialog's request: a new link over `rect`, or an existing one. */
export interface LinkEdit {
  page: number;
  pageUid: string;
  rect: Rect;
  link: ShownLink | null;
}

/** Put a link change into the op list (same op replaced on a re-edit). */
export function linkChange(
  ops: DocumentOps,
  edit: { pageUid: string; rect: Rect; target: LinkTarget; link: ShownLink | null },
) {
  const { pageUid, rect, target, link } = edit;
  if (!link) {
    return ops.change('Add link', (list) => [
      ...list,
      { id: newOpId(), kind: 'link.add', page_uid: pageUid, rect, target },
    ]);
  }
  if (link.linkId === null && link.opId) {
    // A new link edited again: the same `link.add`, replaced.
    return ops.change('Edit link', (list) =>
      list.map((op) => (op.id === link.opId && op.kind === 'link.add' ? { ...op, rect, target } : op)),
    );
  }
  const linkId = link.linkId as string;
  return ops.change('Edit link', (list) => [
    ...list.filter((op) => !(op.kind === 'link.update' && op.link_id === linkId)),
    { id: link.opId ?? newOpId(), kind: 'link.update', page_uid: pageUid, link_id: linkId, rect, target },
  ]);
}

export function linkRemoval(ops: DocumentOps, link: ShownLink) {
  if (link.linkId === null) return ops.change('Delete link', (list) => list.filter((op) => op.id !== link.opId));
  const linkId = link.linkId;
  return ops.change('Delete link', (list) => [
    ...list.filter((op) => !(op.kind === 'link.update' && op.link_id === linkId)),
    { id: newOpId(), kind: 'link.remove', page_uid: link.pageUid, link_id: linkId },
  ]);
}

/* -- the layer --------------------------------------------------------------------- */

interface LayerProps {
  page: number;
  scale: number;
  /** ROTFIX: the page's own `/Rotate` + unrotated size -- link rects are the engine's (unrotated). */
  frame?: PageFrame | null;
  links: ShownLink[];
  pageLabel: (uid: string) => string;
  onOpen: (link: ShownLink) => void;
  onReshape: (link: ShownLink, rect: Rect) => void;
}

const MIN_LINK_PT = 4;

export function LinkLayer({ page, scale, frame = null, links, pageLabel, onOpen, onReshape }: LayerProps) {
  const [live, setLive] = useState<{ key: string; rect: Rect } | null>(null);
  const drag = useRef<{ key: string; mode: 'move' | 'size'; x: number; y: number; rect: Rect; moved: boolean } | null>(null);

  const begin = (link: ShownLink, mode: 'move' | 'size') => (event: ReactPointerEvent<HTMLElement>) => {
    event.stopPropagation();
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    drag.current = { key: link.key, mode, x: event.clientX, y: event.clientY, rect: link.rect, moved: false };
  };
  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current) return;
    // ROTFIX: the pointer's travel, turned back into the (unrotated) layer.
    const { x: dx, y: dy } = unrotateVector(
      (event.clientX - current.x) / scale,
      (event.clientY - current.y) / scale,
      frame?.rotation ?? 0,
    );
    if (!current.moved && Math.hypot(dx, dy) * scale < 3) return;
    current.moved = true;
    const base = current.rect;
    const rect =
      current.mode === 'move'
        ? { ...base, x_pt: Math.max(0, base.x_pt + dx), y_pt: Math.max(0, base.y_pt + dy) }
        : { ...base, w_pt: Math.max(MIN_LINK_PT, base.w_pt + dx), h_pt: Math.max(MIN_LINK_PT, base.h_pt + dy) };
    setLive({ key: current.key, rect });
  };
  const end = (link: ShownLink) => () => {
    const current = drag.current;
    drag.current = null;
    if (!current) return;
    if (!current.moved) {
      if (current.mode === 'move') onOpen(link);
      setLive(null);
      return;
    }
    const rect = live && live.key === link.key ? live.rect : null;
    setLive(null);
    if (rect) {
      onReshape(link, {
        x_pt: Math.round(rect.x_pt * 100) / 100,
        y_pt: Math.round(rect.y_pt * 100) / 100,
        w_pt: Math.round(rect.w_pt * 100) / 100,
        h_pt: Math.round(rect.h_pt * 100) / 100,
      });
    }
  };

  return (
    <div
      className="ws-links"
      data-testid={`link-layer-${page}`}
      data-count={links.length}
      data-space="unrotated"
      style={frameLayerStyle(frame, UNROTATED, scale)}
    >
      {links.map((link) => {
        const rect = live && live.key === link.key ? live.rect : link.rect;
        const target =
          link.target.type === 'uri'
            ? link.target.uri
            : link.target.type === 'page'
              ? `Page ${pageLabel(link.target.page_uid)}`
              : 'Another kind of link';
        return (
          <button
            key={link.key}
            type="button"
            className={`ws-link${link.linkId === null ? ' ws-link--new' : ''}${link.opId ? ' ws-link--edited' : ''}`}
            data-testid={`link-${link.key}`}
            data-link-id={link.linkId ?? undefined}
            data-target={target}
            data-rect-pt={`${rect.x_pt},${rect.y_pt},${rect.w_pt},${rect.h_pt}`}
            aria-label={`Link to ${target} — click to edit, drag to move`}
            title={`${target} — click to edit, drag to move, the corner resizes (never followed while editing)`}
            style={{
              left: ptToPx(rect.x_pt, scale),
              top: ptToPx(rect.y_pt, scale),
              width: ptToPx(rect.w_pt, scale),
              height: ptToPx(rect.h_pt, scale),
            }}
            onPointerDown={begin(link, 'move')}
            onPointerMove={move}
            onPointerUp={end(link)}
            onClick={(event) => {
              // Never navigate while editing; the dialog opens on pointer-up.
              event.preventDefault();
              event.stopPropagation();
            }}
          >
            <span
              className="ws-link__grip"
              data-testid={`link-grip-${link.key}`}
              aria-hidden="true"
              onPointerDown={begin(link, 'size')}
              onPointerMove={move}
              onPointerUp={end(link)}
            />
          </button>
        );
      })}
    </div>
  );
}

/* -- the dialog --------------------------------------------------------------------- */

interface DialogProps {
  edit: LinkEdit;
  pageCount: number;
  uidAt: (position: number) => string | null;
  pageOfUid: (uid: string) => number | null;
  onSave: (target: LinkTarget) => void;
  onDelete: (() => void) | null;
  onClose: () => void;
}

export function LinkDialog({ edit, pageCount, uidAt, pageOfUid, onSave, onDelete, onClose }: DialogProps) {
  const initial = edit.link?.target;
  const [kind, setKind] = useState<'uri' | 'page'>(initial?.type === 'page' ? 'page' : 'uri');
  const [url, setUrl] = useState(initial?.type === 'uri' ? initial.uri : 'https://');
  const [page, setPage] = useState(
    initial?.type === 'page' ? (pageOfUid(initial.page_uid) ?? 1) : Math.min(pageCount, edit.page === 1 ? 2 : 1),
  );
  const [error, setError] = useState<string | null>(null);
  const pages = useMemo(() => Array.from({ length: pageCount }, (_, index) => index + 1), [pageCount]);

  const save = () => {
    if (kind === 'uri') {
      const problem = urlProblem(url);
      if (problem) {
        setError(problem);
        return;
      }
      onSave({ type: 'uri', uri: url.trim() });
      return;
    }
    const uid = uidAt(page);
    if (!uid) {
      setError('That page is not in the document.');
      return;
    }
    onSave({ type: 'page', page_uid: uid });
  };

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label={edit.link ? 'Edit link' : 'New link'}
      data-testid="link-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') onClose();
        if (event.key === 'Enter' && (event.target as HTMLElement).tagName !== 'SELECT') {
          event.preventDefault();
          save();
        }
      }}
    >
      <div className="modal__panel ws-dialog">
        <h2 className="modal__title">{edit.link ? 'Edit link' : 'New link'}</h2>
        <p className="modal__hint">
          The box on page {edit.page} opens this when the PDF is read. While editing, clicking it never follows it.
        </p>
        <div className="ws-dialog__choices" role="radiogroup" aria-label="Link to">
          <label>
            <input
              type="radio"
              name="link-kind"
              data-testid="link-kind-uri"
              checked={kind === 'uri'}
              onChange={() => setKind('uri')}
            />{' '}
            A web or e-mail address
          </label>
          <label>
            <input
              type="radio"
              name="link-kind"
              data-testid="link-kind-page"
              checked={kind === 'page'}
              onChange={() => setKind('page')}
            />{' '}
            A page of this document
          </label>
        </div>
        {kind === 'uri' ? (
          <input
            className="ws-dialog__input"
            data-testid="link-url"
            aria-label="Address (https://, http:// or mailto:)"
            value={url}
            autoFocus
            onChange={(event) => {
              setUrl(event.target.value);
              setError(null);
            }}
          />
        ) : (
          <select
            className="ws-dialog__input"
            data-testid="link-page"
            aria-label="Page"
            value={String(page)}
            onChange={(event) => setPage(Number(event.target.value))}
          >
            {pages.map((number) => (
              <option key={number} value={String(number)}>
                Page {number}
              </option>
            ))}
          </select>
        )}
        {error && (
          <p className="alert alert--error" role="alert" data-testid="link-error">
            {error}
          </p>
        )}
        <div className="modal__actions">
          {onDelete && (
            <button type="button" className="button button--danger" data-testid="link-delete" onClick={onDelete}>
              Delete link
            </button>
          )}
          <span className="modal__spacer" />
          <button type="button" className="button" data-testid="link-cancel" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="button button--primary" data-testid="link-save" onClick={save}>
            {edit.link ? 'Save link' : 'Add link'}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Stable memo of `shownLinks` per page. */
export function useShownLinks(server: Map<string, PageLinkOut[]>, ops: readonly DocumentOp[]) {
  return useCallback(
    (uid: string | null) => (uid ? shownLinks(server.get(uid) ?? [], ops, uid) : []),
    [server, ops],
  );
}
