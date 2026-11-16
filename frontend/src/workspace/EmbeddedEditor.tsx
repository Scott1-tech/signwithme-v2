import { useEffect, useRef } from 'react';

interface Props {
  contractId: string;
  /** Move the editor to this page (an issue in the review was clicked). */
  focus: { page: number; nonce: number } | null;
  /** A new version was saved; the contract page reloads to show it. */
  onSaved: () => void;
}

/**
 * The PDF editor (`/editor/`) inside the review panel: the reviewer fixes the
 * document where the issues are, with no separate step. The editor talks to this
 * page with `postMessage`: it sends `editor:saved` after a save, and this page
 * sends `editor:goto` to move the editor to a page. Messages are accepted from
 * this page's own origin only.
 */
export function EmbeddedEditor({ contractId, focus, onSaved }: Props) {
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: unknown } | null;
      if (data && data.type === 'editor:saved') onSaved();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onSaved]);

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
      />
    </div>
  );
}
