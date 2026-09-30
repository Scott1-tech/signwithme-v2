interface Props {
  /** The workspace's one passing note (`setNote` / a tool's `ctx.notify`), or nothing. */
  note: string | null;
  onDismiss: () => void;
}

/**
 * The workspace's passing note ("Initials placed…", the whiteout notice, a
 * refusal, "Uploading the image…"), floated OVER the top of the pages
 * (`PdfViewer`'s `floating` slot) instead of standing in the document column.
 *
 * Why (fix 2026-09-27, confirmed live twice): in the column the note pushed
 * the pages down; a new drag clears it (`startGesture` → `ctx.notify(null)`),
 * so the pages slid up 74 px under a pointer that had not moved and the drag
 * was saved 41.5 pt taller than drawn. Floating, the note appearing or going
 * can never move a page -- at any time, from any tool or async result.
 *
 * The live region is always in the document (a region inserted together with
 * its text is not reliably announced); `data-testid="editor-note"` keeps the
 * old contract: the element exists only while there is a note, and its text
 * is exactly the note.
 */
export function EditorNote({ note, onDismiss }: Props) {
  return (
    <div className="ws-note-host" role="status" aria-live="polite">
      {note && (
        <div className="ws-note alert alert--warn" data-testid="editor-note-box">
          <p className="ws-note__text" data-testid="editor-note">
            {note}
          </p>
          <button
            type="button"
            className="ws-note__close"
            data-testid="editor-note-dismiss"
            aria-label="Dismiss this note"
            title="Dismiss"
            onClick={onDismiss}
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
