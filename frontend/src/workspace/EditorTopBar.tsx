import type { ReactNode } from 'react';
import { Icon } from './toolbar/icons';

/**
 * F1a: the editor's top bar (EDITOR-V2-PLAN §7 «Top bar»), a SHELL.
 *
 *     [contract name]            [ session slot ]  [Download PDF] [Generate final contract]
 *
 * What it does today:
 *  * the contract's name (`editor-topbar-title`);
 *  * «Download PDF» (`editor-download`): the document as it exists on the
 *    server right now -- the signed final PDF once there is one, else the
 *    uploaded source. The export engine (markup baked in, the «Include
 *    annotations (N)» dialog) is F3 / B2; until then this button never
 *    pretends to export the annotations;
 *  * «Generate final contract» (`editor-finalize`, moved here from the
 *    panel's `FinalizeBar`, which keeps the readiness line and the reason).
 *    The gate is the same one expression (`finalizeDisabled`).
 *
 * What it does NOT do yet, on purpose: «Edit PDF» / «Save changes» / «Cancel
 * editing» and the save status need the draft session of B1 / F1b. The place
 * for them is the `session` slot: F1b renders its controls there. While it is
 * `undefined` nothing is drawn -- no dead button, no placeholder.
 */

export interface FinalizeGate {
  busy: boolean;
  blocked: boolean;
  signatureCount: number;
  slotsComplete: boolean;
}

/** The one rule for «Generate final contract» being off (the server decides for real). */
export const finalizeDisabled = ({ busy, blocked, signatureCount, slotsComplete }: FinalizeGate): boolean =>
  busy || blocked || signatureCount === 0 || !slotsComplete;

/**
 * F1b's hook-up point (plan §7 «Sessiya»): the Edit PDF / Save / Cancel group
 * and `editor-save-status`. Declared now so F1b fills it without reshaping
 * the bar; never rendered while absent.
 */
export interface EditorSessionSlot {
  /** The controls themselves (F1b: `[Edit PDF]` or `[Save changes] [Cancel editing]` + status). */
  controls: ReactNode;
  /**
   * While an edit session is open the plan turns «Generate final contract»
   * off with this reason ("Save or cancel your edits first").
   */
  finalizeBlockedReason?: string | null;
}

interface Props {
  /** The contract's name, as the rest of the app shows it. */
  title: string;
  /** A second, quieter line (the template), if known. */
  subtitle?: string | null;
  finalize: FinalizeGate & { onFinalize: () => void; blockedReason: string | null };
  download: {
    /** `true` once the signed final PDF exists (the button then downloads that). */
    finalAvailable: boolean;
    busy: boolean;
    onDownload: () => void;
    /** F2: the button opens the export dialog (the server's current version, annotations in or out). */
    exportDialog?: boolean;
  };
  /** F1b (see `EditorSessionSlot`); nothing is drawn while it is absent. */
  session?: EditorSessionSlot;
  /** F3: «Share for others to fill» (`share/ShareDialog.tsx`); nothing is drawn while absent. */
  share?: ReactNode;
}

export function EditorTopBar({ title, subtitle, finalize, download, session, share }: Props) {
  const sessionReason = session?.finalizeBlockedReason ?? null;
  const off = finalizeDisabled(finalize) || Boolean(sessionReason);
  const finalizeTitle =
    sessionReason ??
    (finalize.blocked && finalize.blockedReason
      ? finalize.blockedReason
      : finalize.signatureCount === 0 || !finalize.slotsComplete
        ? 'Sign every required slot and choose each required date first'
        : 'Build the final signed PDF');
  const downloadTitle = download.finalAvailable
    ? 'Download the signed final PDF'
    : download.exportDialog
      ? 'Download the document as saved, with or without its annotations'
      : 'Download the uploaded PDF as it is on the server (annotations are not included yet)';

  return (
    <header className="sj-topbar" data-testid="editor-topbar" aria-label="Document">
      <div className="sj-topbar__name">
        <span className="sj-topbar__title" data-testid="editor-topbar-title" title={title}>
          {title}
        </span>
        {subtitle && (
          <span className="sj-topbar__subtitle" title={subtitle}>
            {subtitle}
          </span>
        )}
      </div>

      {session && (
        <div className="sj-topbar__session" data-testid="editor-session">
          {session.controls}
        </div>
      )}

      <div className="sj-topbar__actions">
        {share}
        <button
          type="button"
          className="sj-btn"
          data-testid="editor-download"
          data-source={download.finalAvailable ? 'final' : download.exportDialog ? 'export' : 'original'}
          aria-label={
            download.finalAvailable
              ? 'Download PDF — the signed final PDF'
              : download.exportDialog
                ? 'Download PDF — the saved document, annotations optional'
                : 'Download PDF — the uploaded PDF'
          }
          title={downloadTitle}
          disabled={download.busy}
          onClick={download.onDownload}
        >
          <Icon name="download" />
          <span className="sj-btn__label">{download.busy ? 'Downloading…' : 'Download PDF'}</span>
        </button>
        <button
          type="button"
          className="sj-btn sj-btn--primary"
          data-testid="editor-finalize"
          title={finalizeTitle}
          disabled={off}
          onClick={() => finalize.onFinalize()}
        >
          <Icon name="finalize" />
          <span className="sj-btn__label">{finalize.busy ? 'Generating…' : 'Generate final contract'}</span>
        </button>
      </div>
    </header>
  );
}
