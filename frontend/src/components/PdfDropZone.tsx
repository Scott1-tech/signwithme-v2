import { useCallback, useState, type DragEvent } from 'react';

interface PdfDropZoneProps {
  /** The first dropped file, whatever its type -- the caller validates it. */
  onFile: (file: File) => void;
  /** Opens the file picker the caller owns (so "Replace file" can reuse it). */
  onBrowse: () => void;
  /** Ids of the hint and, when there is one, the error message. */
  describedBy?: string;
  disabled?: boolean;
}

const hasFiles = (event: DragEvent<HTMLElement>) =>
  Array.from(event.dataTransfer?.types ?? []).includes('Files');

/**
 * Drag-and-drop target for one PDF, which is also a plain button: the same
 * thing happens on Enter, Space and a click, so nothing here is mouse-only.
 */
export function PdfDropZone({ onFile, onBrowse, describedBy, disabled = false }: PdfDropZoneProps) {
  const [over, setOver] = useState(false);

  const enter = useCallback((event: DragEvent<HTMLElement>) => {
    if (disabled || !hasFiles(event)) return;
    // Both are needed: without `preventDefault` on dragover the browser refuses
    // the drop and opens the file in the tab instead.
    event.preventDefault();
    event.stopPropagation();
    setOver(true);
  }, [disabled]);

  const leave = useCallback((event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    // `relatedTarget` inside the zone means the pointer only crossed a child.
    const next = event.relatedTarget as Node | null;
    if (next && event.currentTarget.contains(next)) return;
    setOver(false);
  }, []);

  const drop = useCallback(
    (event: DragEvent<HTMLElement>) => {
      event.preventDefault();
      event.stopPropagation();
      setOver(false);
      if (disabled) return;
      const file = event.dataTransfer?.files?.[0];
      if (file) onFile(file);
    },
    [disabled, onFile],
  );

  return (
    <button
      type="button"
      className={`upload-drop${over ? ' is-over' : ''}`}
      data-testid="upload-dropzone"
      onClick={onBrowse}
      onDragEnter={enter}
      onDragOver={enter}
      onDragLeave={leave}
      onDrop={drop}
      aria-describedby={describedBy}
      disabled={disabled}
    >
      <span className="upload-drop__icon" aria-hidden="true">
        PDF
      </span>
      <span className="upload-drop__title">
        {over ? 'Drop the contract to attach it' : 'Drag the completed contract here'}
      </span>
      <span className="upload-drop__hint">or press to choose a file from this computer</span>
    </button>
  );
}
