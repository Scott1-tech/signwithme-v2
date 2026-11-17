import { useCallback, useEffect, useRef } from 'react';
import type { EditorOverlay } from './editorOverlays';
import type { SignHistory } from './useSignHistory';

interface Props {
  contractId: string;
  /** Move the editor to this page (an issue in the review was clicked). */
  focus: { page: number; nonce: number } | null;
  /** A new version was saved; the contract page reloads to show it. */
  onSaved: () => void;
  /** Placed signatures and filled dates, drawn over the editor's pages. */
  overlays?: readonly EditorOverlay[];
  /** The signing steps the editor's Undo / Redo can take back. */
  signHistory?: SignHistory;
  /** The editor's Sign tool was clicked on a page (points, top-left origin). */
  onSignAt?: (page: number, x: number, y: number) => void;
  /** A signature or date was dragged in the editor (points, right / down positive). */
  onOverlayMove?: (key: string, dx: number, dy: number) => void;
}

/**
 * The PDF editor (`/editor/`) inside the contract page: the reviewer fixes the
 * document where the issues are, and the signer sees the same pages with the
 * signatures and dates on them. The editor talks to this page with
 * `postMessage`:
 *
 *  * editor → page: `editor:saved` after a save, `editor:ready` once loaded,
 *    `editor:sign-undo` / `editor:sign-redo` from its Undo / Redo buttons, and
 *    `editor:sign-redo-clear` when a newer text edit was made;
 *  * page → editor: `editor:goto` to move to a page, `editor:overlays` with
 *    what to draw and the state of the signing history.
 *
 * Messages are accepted from this page's own origin only.
 */
export function EmbeddedEditor({ contractId, focus, onSaved, overlays, signHistory, onSignAt, onOverlayMove }: Props) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const history = useRef(signHistory);
  history.current = signHistory;
  const handlers = useRef({ onSignAt, onOverlayMove });
  handlers.current = { onSignAt, onOverlayMove };

  const sendOverlays = useCallback(() => {
    const target = frameRef.current?.contentWindow;
    if (!target) return;
    target.postMessage(
      {
        type: 'editor:overlays',
        overlays: overlays ?? [],
        lastAt: signHistory?.lastAt ?? -1,
        canRedo: signHistory?.canRedo ?? false,
        busy: signHistory?.busy ?? false,
      },
      window.location.origin,
    );
  }, [overlays, signHistory?.lastAt, signHistory?.canRedo, signHistory?.busy]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: unknown; [key: string]: unknown } | null;
      if (!data) return;
      const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
      if (data.type === 'editor:sign-at') {
        const page = num(data.page);
        const x = num(data.x);
        const y = num(data.y);
        if (page !== null && x !== null && y !== null) handlers.current.onSignAt?.(page, x, y);
        return;
      }
      if (data.type === 'editor:overlay-move') {
        const dx = num(data.dx);
        const dy = num(data.dy);
        if (typeof data.key === 'string' && dx !== null && dy !== null) handlers.current.onOverlayMove?.(data.key, dx, dy);
        return;
      }
      if (data.type === 'editor:saved') onSaved();
      else if (data.type === 'editor:ready') sendOverlays();
      else if (data.type === 'editor:sign-undo') void history.current?.undo();
      else if (data.type === 'editor:sign-redo') void history.current?.redo();
      else if (data.type === 'editor:sign-redo-clear') history.current?.clearRedo();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onSaved, sendOverlays]);

  useEffect(() => {
    sendOverlays();
  }, [sendOverlays]);

  useEffect(() => {
    const target = frameRef.current?.contentWindow;
    if (!focus || !target) return;
    target.postMessage({ type: 'editor:goto', page: focus.page }, window.location.origin);
  }, [focus]);

  return (
    <div style={{ height: '80vh', width: '100%' }}>
      <iframe
        ref={frameRef}
        title="PDF editor"
        src={`/editor/index.html?contract=${encodeURIComponent(contractId)}&embedded=1`}
        style={{ width: '100%', height: '100%', border: 0, background: '#fff' }}
        onLoad={sendOverlays}
      />
    </div>
  );
}
