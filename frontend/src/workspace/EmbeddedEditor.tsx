import { useEffect } from 'react';

interface Props {
  contractId: string;
  /** The editor's «back» link: close the editor and show the viewer again. */
  onClose: () => void;
  /** A new version was saved; the contract page reloads to show it. */
  onSaved: () => void;
}

/**
 * The standalone PDF editor (`/editor/`) shown inside the contract page, in
 * place of the viewer and the old editing tools. The editor talks to this page
 * with `postMessage` (`editor:close`, `editor:saved`); a message is accepted only
 * from this page's own origin.
 */
export function EmbeddedEditor({ contractId, onClose, onSaved }: Props) {
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: unknown } | null;
      if (!data) return;
      if (data.type === 'editor:close') onClose();
      if (data.type === 'editor:saved') onSaved();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onClose, onSaved]);

  return (
    <div style={{ height: '78vh', width: '100%' }}>
      <iframe
        title="PDF editor"
        src={`/editor/index.html?contract=${encodeURIComponent(contractId)}&embedded=1`}
        style={{ width: '100%', height: '100%', border: 0, background: '#fff' }}
      />
    </div>
  );
}
