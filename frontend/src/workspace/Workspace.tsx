import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { LayoutPanel } from '../components/LayoutPanel';
import { PdfViewer } from '../components/PdfViewer';
import { SignatureChooserHost } from '../components/SignatureChooserHost';
import { ApiError, fetchPdf } from '../api/client';
import { OVERRIDE_NOTES_MIN, contractsApi, describeReviewError } from '../api/contracts';
import { useAuth } from '../hooks/useAuth';
import { useAnnotationAssets } from '../hooks/useAnnotationAssets';
import { useAutoFill, useAutoFillRecord } from '../hooks/useAutoFill';
import { useBulkSigning } from '../hooks/useBulkSigning';
import { useComparison } from '../hooks/useComparison';
import { useContractDocument } from '../hooks/useContractDocument';
import { useContractSignatures } from '../hooks/useContractSignatures';
import { useDateValues } from '../hooks/useDateValues';
import { useFinalize } from '../hooks/useFinalize';
import { useInitials } from '../hooks/useInitials';
import { usePdfViewer } from '../hooks/usePdfViewer';
import { useSignatureImages } from '../hooks/useSignatureImages';
import {
  useSignatureChooser,
  type ChooserRequest,
  type ChooserStep,
} from '../hooks/useSignatureChooser';
import { useSignatureLibrary } from '../hooks/useSignatureLibrary';
import { useSignatureSlots } from '../hooks/useSignatureSlots';
import { useSigningState } from '../hooks/useSigningState';
import { useSlotFocus } from '../hooks/useSlotFocus';
import { useSlotLayout } from '../hooks/useSlotLayout';
import { useStoredChoice, useStoredFlag } from '../hooks/useStoredPreference';
import { annotationsForRequest, isImage, type Annotation } from '../lib/annotations';
import { computeSigningGate } from '../lib/gate';
import { fitLayoutToPages, signatureScaleFor, withoutSlotOffsets } from '../lib/layout';
import { computeOverrideState } from '../lib/reviewRules';
import { mapRectPt, normalizeQuarter, swapsAxes, UNROTATED } from '../lib/viewport';
import { toServerSpace } from '../lib/markupFrames';
import {
  AUTOFILL_KEY,
  MODE_KEY,
  SIGN_MODES,
  readLayoutRejection,
  signingErrorText,
  type SignMode,
} from '../lib/signing';
import { readIdenticalTemplate, similarityPercent } from '../lib/warnings';
import {
  PageOverlay,
  type AnnotationShared,
  type FreeSignatureShared,
  type SlotLayerShared,
} from '../overlay/PageOverlay';
import { FinalizeBar } from '../panels/FinalizeBar';
import { FinalizeErrors } from '../panels/FinalizeErrors';
import { ObjectsSection } from '../panels/ObjectsSection';
import { ReviewSection } from '../panels/ReviewSection';
import { SignSection } from '../panels/SignSection';
import type { PageIssue, SignatureSlot } from '../types/comparison';
import { readOverride, type Signature } from '../types/contract';
import type { BBox } from '../types/field';
import { EditorNote } from './EditorNote';
import { EditorTopBar } from './EditorTopBar';
import { PanelSection, usePanelSections } from './PanelSections';
import { useDocumentHistory } from './state/useDocumentHistory';
import { useSelection } from './state/useSelection';
import { PdfTextLayer } from './text/PdfTextLayer';
import { useTextSelection, type TextLayerEntry } from './text/useTextSelection';
import { FloatingToolbar } from './toolbar/FloatingToolbar';
import { INITIALS_LOCK_NOTE } from './tools/initials';
import { toolsFor } from './tools/registry';
import { assetsApi, type AssetOut, type LinkTarget, type Rect as DocRect, type TypedSignatureFont } from '../api/document';
import { userSignaturesApi } from '../api/userSignatures';
import { makeImage, placeImageBox } from '../lib/annotationKinds/image';
import { makeAnnotationId, MAX_ANNOTATIONS } from '../lib/annotations';
import { useEditorFonts } from '../hooks/useEditorFonts';
import type { UserSignature } from '../types/contract';
import { useDocumentOps, toRect } from './session/useDocumentOps';
import { usePageStructure } from './pages/usePageStructure';
import { PageDeleteDialog, type PageDeleteCounts } from './pages/PageDeleteDialog';
import { LinkDialog, LinkLayer, linkChange, linkRemoval, shownLinks, usePageLinks, type LinkEdit, type ShownLink } from './links/links';
import { PageImageLayer, PendingOpsLayer, usePageImages } from './images/pageObjects';
import { AnnotateExtras, ImagesExtras, SignExtras, StampDialog, useGallery } from './menus/MenuExtras';
import { ExportDialog, runExport } from './export/ExportDialog';
import { documentApi } from '../api/document';
import type { Point, TextRun, ToolId } from './tools/types';
import { useAnnotationTools } from './useAnnotationTools';
import { useGestureLayoutLock } from './useGestureLayoutLock';
import { useStableCallback } from './useStableCallback';
import { useWorkspaceRoute } from './useWorkspaceRoute';
import { signaturesApi } from '../api/signatures';
import { editGroups } from '../lib/annotations';
import {
  FINALIZE_WHILE_EDITING,
  SessionBanners,
  SessionDialogs,
  sessionControls,
} from './session/SessionControls';
import { useEditSession, type CommitNotice } from './session/useEditSession';
import { useMarkupSync, type MarkupSync } from './session/useMarkupSync';
import { useTextEditing } from './text/useTextEditing';
// F3: «Share for others to fill» + the submissions it brings.
import { ShareButton, ShareDialog } from './share/ShareDialog';
import { useShare } from './share/useShare';
import { SubmissionsSection } from '../panels/SubmissionsSection';
import '../styles/editor.css';
import '../styles/workspace.css';
// F1a: the Sejda look, scoped to `.workspace` (no other page is touched).
import '../styles/sejda.css';
// F1b: the edit session and the text editor.
import '../styles/editor-v2.css';
// F2: text boxes, links, redaction, page tools, menus' extras, dialogs.
import '../styles/editor-f2.css';
// F2b: interactive PDF form fields.
import { useForms } from './forms/useForms';
import { FORM_TOOL_IDS } from './forms/fieldTools';
import '../styles/forms.css';

/** A free placement re-saves a stored library signature when the tick is on
 *  (the editor always did); a slot never did. */
const resavesStoredSignature = (request: ChooserRequest) => request.purpose === 'free';

/** Group a list by page without losing the order inside a page. */
function byPage<T>(items: readonly T[], pageOf: (item: T) => number | null | undefined) {
  const map = new Map<number, T[]>();
  items.forEach((item) => {
    const page = pageOf(item);
    if (!page) return;
    const list = map.get(page);
    if (list) list.push(item);
    else map.set(page, [item]);
  });
  return map;
}

/**
 * The single contract workspace (W1): review, signing and the editor on one
 * screen, behind `/contracts/:id/review|sign|edit`.
 *
 * WHAT THE URL STILL DECIDES
 * --------------------------
 * Only two things: which section of the right-hand panel is forced open (and
 * scrolled to), and -- once, on the first render -- which tool is armed
 * (`/sign` → Signature, `/review` and `/edit` → Select). Everything else is
 * one state for all three: switching panels is a `replace` navigation inside
 * one route element, so nothing is fetched again and nothing is lost.
 *
 * THE MERGE RULES (plan §1.5, §5 W1)
 * ---------------------------------
 *  * a slot is drawn once, as `.slot` (`SlotLayer`); the review's slot
 *    highlight is gone;
 *  * a free signature is drawn once, as `.swm-sig` (`FreeSignatureLayer`);
 *    `.placed-signature` only ever means "in a slot";
 *  * one finalize (`FinalizeBar`), gated the signing way, sending
 *    `dates + layout (fitted to the pages) + annotations`;
 *  * while a text/date/check/cross tool is armed the slot layer takes no
 *    pointer events (`workspace--placing`), so a click lands on the page;
 *  * on a contract nobody approved yet the Signature tool and finalize are
 *    off and say why; the annotation tools still work (they are local).
 *
 * WHERE THE STATE LIVES
 * ---------------------
 * Here, for everything; the hooks are the W0 ones, unchanged in behaviour.
 * The editor half (annotations, tools, inline box, keyboard) is
 * `useAnnotationTools`; the one selection is `useSelection`.
 *
 * W2
 * --
 *  * the tools are the registry's (`workspace/tools/<id>/index.ts`): the
 *    toolbar, the shortcuts, `workspace--placing`, `interactive` and the page
 *    click all read the armed tool's definition -- no tool id is special-cased
 *    here except Signature's slot press (`pressOpensChooser`);
 *  * the toolbar is `toolbar/FloatingToolbar.tsx` (docked by default, where the
 *    old one was; floats, snaps, folds; `swm.toolbar`);
 *  * ONE undo history (`state/useDocumentHistory.ts`) for the annotations and
 *    the slot layout: a user change of the layout is `commitLayout` (a step),
 *    the app's own corrections -- the page-edge clamp, the fit before
 *    finalize, dropping slot ids the server did not know -- are
 *    `replaceLayout` (never a step). Server writes are never in it;
 *  * a gesture re-renders only its own page (see "WHAT A GESTURE RE-RENDERS").
 *
 * W4
 * --
 *  * a DRAG tool (ink, rect, ellipse, line, arrow) gives every page a
 *    `ToolCaptureLayer` (`capture` in the shared annotation props) and the
 *    root `workspace--drawing`; the live preview lives in that layer's own
 *    state, so a drawing in progress re-renders no `PageOverlay` at all;
 *  * the sticky-note box is page-local like the inline text box (`note`);
 *  * the Image tool's hidden file input lives here (`ann-image-file`); the
 *    pictures are fetched once each (`useAnnotationAssets`).
 *
 * W5
 * --
 *  * Whiteout (W) is a drag tool like a rect; its box is drawn UNDER the slots
 *    and the signatures (`overlay/WhiteoutLayer`), the order the PDF prints;
 *  * Initials (I) open the chooser with `purpose: 'initials'`; the placement,
 *    «Every page» and «Remove all initials» are `useInitials` (server writes,
 *    three at a time, never undoable); `initialIds` tells an initial from a
 *    signature for the page (`.swm-sig--initials`) and the Objects list;
 *  * the stored annotations are clamped onto their pages once the sizes are
 *    known (`useAnnotationTools`, not an undo step).
 */
export function Workspace() {
  const { contractId, panel, setPanel } = useWorkspaceRoute();
  const { isAtLeast } = useAuth();
  const {
    contract,
    result,
    issues,
    processing,
    error: comparisonError,
    refresh,
  } = useComparison(contractId);

  const contractDocument = useContractDocument(contractId);
  const { source, loadError, editor: editorDocument } = contractDocument;
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The document's own load failure shares the workspace's one error line.
  useEffect(() => {
    if (loadError) setError(loadError);
  }, [loadError]);

  // F2: while the draft carries page ops the viewer shows the WHOLE draft (`usePageStructure`).
  const [draftBytes, setDraftBytes] = useState<ArrayBuffer | null>(null);
  const viewer = usePdfViewer(draftBytes ?? source);
  const { scrollToPage } = viewer;
  /** ROTFIX: a box drawn on page `page` as displayed -> the engine's (unrotated) box. */
  const toEngineRect = <T extends { x: number; y: number; w: number; h: number }>(page: number, rect: T): T => {
    const frame = viewer.frameOf(page);
    return frame && frame.rotation !== 0 ? { ...rect, ...mapRectPt(rect, frame, frame.rotation, UNROTATED) } : rect;
  };
  /**
   * ROTFIX: the pages the SERVER's marks (slots, their signatures and dates)
   * are measured on -- the current version's pages as displayed. The same as
   * the viewer's until a draft that moves or turns pages is on screen; then the
   * page-edge clamp must not read the draft's (turned) sizes and rewrite the
   * slot offsets for a page that has not changed yet.
   */
  const slotPageSizes = useMemo(() => {
    if (!draftBytes || editorDocument.pages.length === 0) return viewer.pageSizes;
    const map = new Map<number, { width: number; height: number }>();
    editorDocument.pages.forEach((entry) => {
      const turned = swapsAxes(normalizeQuarter(entry.rotation));
      map.set(entry.position, turned
        ? { width: entry.height_pt, height: entry.width_pt }
        : { width: entry.width_pt, height: entry.height_pt });
    });
    return map;
  }, [draftBytes, editorDocument.pages, viewer.pageSizes]);
  const selection = useSelection();
  const sections = usePanelSections(panel);
  // The ONE undo history (W2): annotations and slot positions, never a server write.
  const history = useDocumentHistory(contractId);
  const stageRef = useRef<HTMLDivElement>(null);
  // Fix 2026-09-27: nothing above the pages may change height while a press
  // on a page is in progress (see `useGestureLayoutLock`).
  const bannersRef = useRef<HTMLDivElement>(null);
  useGestureLayoutLock(stageRef, bannersRef);
  const dismissNote = useCallback(() => setNote(null), []);

  /* -- signing state (W0 hooks) ---------------------------------------------- */

  const contractSignatures = useContractSignatures(contractId);
  const { rows: signatures, loaded: signaturesLoaded, setRows: setSignatures } = contractSignatures;
  // One ISO value per date slot (what finalize sends), kept per contract in
  // `swm.dates.<id>`; `selectedDate` only feeds "apply to all" and the bulk run.
  const dates = useDateValues(contractId);
  const { values: dateValues, setValues: setDateValues, selectedDate } = dates;

  const {
    items: mySignatures,
    loaded: libraryLoaded,
    reload: reloadLibrary,
    defaultSignature: defaultLibrarySignature,
  } = useSignatureLibrary();

  const [signMode, setSignMode] = useStoredChoice<SignMode>(MODE_KEY, SIGN_MODES, 'bulk');
  const [autoFill, setAutoFill] = useStoredFlag(AUTOFILL_KEY, true);
  const autoFillRecord = useAutoFillRecord();

  const slotSet = useSignatureSlots(result);
  const { slots, sigSlots, dateSlots, slotById, sigSlotIds } = slotSet;

  // Where the slot marks sit: ONE layout for the whole workspace (the review's
  // own `useContractLayout` + `useSlotDrag` pair is gone), so the page-edge
  // clamp now runs whichever section is open.
  const {
    layout,
    commit: commitLayout,
    replace: replaceLayout,
    reset: resetLayout,
    pageSizeOf,
    drag,
    resize,
    clampNote,
    rows: layoutRows,
  } = useSlotLayout({
    contractId,
    slotSet,
    pageSizes: viewer.pageSizes,
    slotPageSizes,
    scale: viewer.scale,
    history,
  });

  const signatureIds = useMemo(() => signatures.map((signature) => signature.id), [signatures]);
  const signatureImages = useSignatureImages(contractId, signatureIds);

  const signing = useSigningState(signatures, slotSet, dateValues);
  const {
    signedFieldIds,
    signatureForSlot,
    slotsComplete,
    emptySigSlots,
    emptyDateSlots,
    freeSignatures,
  } = signing;

  // Slot focus reads and writes the workspace's one selection.
  const focus = useSlotFocus({
    slotById,
    scrollToPage,
    focusedSlotId: selection.slotFieldId,
    setFocusedSlotId: selection.selectSlot,
  });
  const { focusedSlotId, hoveredSlotId } = focus;

  // Whether signing may start, and if not, why (see `lib/gate.ts`).
  const gate = useMemo(() => computeSigningGate(contract, result), [contract, result]);
  const { blocked } = gate;
  const blockedReason = !blocked
    ? null
    : gate.notApproved
      ? 'Contract not approved yet'
      : 'Signing is blocked while critical issues remain';

  const bulk = useBulkSigning({
    contractId,
    signatures,
    setSignatures,
    sigSlots,
    dateSlots,
    dateValues,
    setDateValues,
    selectedDate,
    emptySigSlots,
    setBusy,
    setError,
    setAutoFilled: autoFillRecord.setAutoFilled,
    setAutoFillNote: autoFillRecord.setAutoFillNote,
  });
  const { setBulkSignature, setBulkProgress, setBulkFailures, setBulkSkipped, applyToAll } = bulk;

  // The signature dialog: a slot, a free spot on the page, or -- `bulk` --
  // the image for every slot (adopted, not placed: it only arms the panel).
  const adoptBulkSignature = useCallback(
    (dataUrl: string, label: string, previewUrl?: string) => {
      setBulkSignature({ dataUrl, label, previewUrl });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a state setter
    [],
  );
  const describeChooserError = useCallback(
    (cause: unknown, step: ChooserStep) =>
      signingErrorText(
        cause,
        step === 'place' ? 'Could not save the signature' : 'Could not use the stored signature',
      ),
    [],
  );
  // W5: initials -- small free signatures (`lib/initials.ts`), their «every
  // page» and «remove all» runs. Server writes: never in the undo history.
  const describeInitialsError = useCallback(
    (cause: unknown, fallback: string) => signingErrorText(cause, fallback),
    [],
  );
  const initials = useInitials({
    contractId,
    rows: signatures,
    place: contractSignatures.place,
    remove: contractSignatures.remove,
    pageCount: viewer.pageCount,
    pageSizeOf,
    blocked,
    setBusy,
    describeError: describeInitialsError,
  });
  const { placeAt: placeInitialsAt } = initials;
  const placeInitials = useCallback(
    async (page: number, at: { x: number; y: number }, dataUrl: string) => {
      const created = await placeInitialsAt(page, at, dataUrl);
      selection.selectSignature(created.id);
      setNote(`Initials placed. ${INITIALS_LOCK_NOTE} «Every page» in Objects puts them on every page.`);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `selectSignature` is stable
    [placeInitialsAt],
  );

  const chooser = useSignatureChooser({
    place: contractSignatures.place,
    reloadLibrary,
    setBusy,
    setError,
    describeError: describeChooserError,
    onAdopt: adoptBulkSignature,
    saveStoredToLibrary: resavesStoredSignature,
    placeInitials,
  });

  /** Take one signature off the contract. Returns whether the server agreed. */
  const removeSignature = useStableCallback(async (signatureId: string): Promise<boolean> => {
    if (!contractId) return false;
    try {
      await contractSignatures.remove(signatureId);
      return true;
    } catch (cause) {
      setError(signingErrorText(cause, 'Could not remove the signature'));
      return false;
    }
  });

  /** "Remove and place again" on a free signature (the editor's wording). */
  const removeFreeSignature = useStableCallback(async (signatureId: string) => {
    if (!contractId) return;
    setBusy(true);
    setError(null);
    try {
      const wasInitials = initials.initialIds.has(signatureId);
      await contractSignatures.remove(signatureId);
      initials.forgetOne(signatureId);
      // Said out loud, because the user has just been told a placed signature
      // cannot be undone: this is the other half of that bargain.
      setNote(
        wasInitials
          ? 'Initials removed. Arm the Initials tool and click the page to place them again.'
          : 'Signature removed. Arm the Signature tool and click the page to place again.',
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not remove that signature');
    } finally {
      setBusy(false);
    }
  });

  // The first-load automatic fill, at most once per visit -- and only once the
  // signing section is what the URL is about: opening the review of an
  // approved contract must not sign it behind the reviewer's back.
  const { autoFillNote, undoAutoFill } = useAutoFill({
    record: autoFillRecord,
    autoFill,
    contractId,
    result,
    signaturesLoaded,
    libraryLoaded,
    blocked: blocked || panel !== 'sign',
    defaultLibrarySignature,
    emptySigSlots,
    emptyDateSlots,
    applyToAll,
    setBusy,
    setError,
    setBulkSignature,
    setSignatures,
    setDateValues,
    setBulkProgress,
    setBulkFailures,
    setBulkSkipped,
  });

  // A slot without a bbox cannot be signed from here: the placement endpoint
  // still requires one in the payload, even though the server positions the
  // image from the template when `field_id` is given.
  const startSlot = useStableCallback((slot: SignatureSlot) => {
    if (blocked || !slot.bbox || signedFieldIds.has(slot.field_id)) return;
    chooser.open({
      purpose: 'slot',
      page: slot.page ?? viewer.currentPage,
      bbox: slot.bbox,
      fieldId: slot.field_id,
    });
  });

  /**
   * "Replace" on the mini panel: the placement endpoint never overwrites, so
   * the signature already there has to come off first. The chooser only opens
   * once the removal went through.
   */
  const replaceSlot = useStableCallback(async (slot: SignatureSlot) => {
    if (blocked || !slot.bbox) return;
    const placed = signatureForSlot(slot.field_id);
    if (!placed) {
      startSlot(slot);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (!(await removeSignature(placed.id))) return;
      chooser.open({
        purpose: 'slot',
        page: slot.page ?? viewer.currentPage,
        bbox: slot.bbox,
        fieldId: slot.field_id,
      });
    } finally {
      setBusy(false);
    }
  });

  /** Bring a slot into view on the page and in the signing section. */
  const showSlot = useStableCallback((fieldId: string) => {
    const slot = slotById.get(fieldId);
    if (slot?.page) scrollToPage(slot.page);
    if (!sections.open.has('sign')) sections.toggle('sign');
    focus.focusSlot(fieldId);
  });

  /* -- review (was ReviewPage) ----------------------------------------------- */

  const [notes, setNotes] = useState('');
  const [correcting, setCorrecting] = useState(false);

  const goToIssue = useStableCallback((issue: PageIssue) => {
    if (issue.page) scrollToPage(issue.page);
    selection.selectIssue(issue.key, issue.fieldId);
  });

  // Correcting a misread field: write the value, then re-run the rules against
  // it and reload the stored result so can_sign (and the Approve button) reflect
  // the correction. All three steps are needed.
  const correctField = useCallback(
    async (issue: PageIssue, value: string) => {
      if (!contractId || !issue.fieldId) return;
      setCorrecting(true);
      setError(null);
      try {
        await contractsApi.correctField(contractId, issue.fieldId, { value });
        await contractsApi.rerunComparison(contractId);
        await refresh();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Correction failed');
      } finally {
        setCorrecting(false);
      }
    },
    [contractId, refresh],
  );

  const isReviewer = isAtLeast('reviewer');
  const canCorrect = Boolean(isReviewer && contract && contract.review_status === 'pending');
  const overrideState = useMemo(() => computeOverrideState(result), [result]);
  const { criticalMissing, criticalInconsistencies, eligible: overrideEligible } = overrideState;
  const sameAsTemplate = readIdenticalTemplate(result?.template_match);
  const notesSufficient = notes.trim().length >= OVERRIDE_NOTES_MIN;
  const override = readOverride(contract);

  // Approving switches the workspace to signing in place: same element, the
  // URL becomes `/sign`, nothing is reloaded.
  const decide = async (decision: 'approved' | 'rejected') => {
    if (!contractId) return;
    setBusy(true);
    setError(null);
    try {
      await contractsApi.submitReview(contractId, decision, notes || undefined);
      await refresh();
      if (decision === 'approved') setPanel('sign');
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusy(false);
    }
  };

  // Same approve flow, but the request carries `override_missing: true` and the
  // trimmed notes are mandatory. The confirm is deliberate friction.
  const approveWithOverride = async () => {
    if (!contractId || !overrideEligible || !notesSufficient) return;
    const missingCount = criticalMissing.length;
    const mismatches = criticalInconsistencies.length;
    const parts: string[] = [];
    if (missingCount > 0) {
      parts.push(`${missingCount} empty required field${missingCount === 1 ? '' : 's'}`);
    }
    if (mismatches > 0) {
      parts.push(`${mismatches} cross-field inconsistenc${mismatches === 1 ? 'y' : 'ies'}`);
    }
    const confirmed = window.confirm(
      `You are approving a contract with ${parts.join(' and ')}. ` +
        'This is recorded in the audit trail and the final PDF. Continue?',
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await contractsApi.submitReview(contractId, 'approved', notes.trim(), {
        overrideMissing: true,
      });
      await refresh();
      setPanel('sign');
    } catch (cause) {
      setError(describeReviewError(cause, { overrideMissing: true }));
    } finally {
      setBusy(false);
    }
  };

  /* -- the editor half ------------------------------------------------------- */

  // F2: a signature picked in Sign ▾ is placed by the next click (see `onSignatureClickF2`).
  const signatureClickOverride = useRef<(page: number, suggested: BBox) => boolean>(() => false);
  const onSignatureClick = useCallback(
    (page: number, suggested: BBox) => {
      if (blocked) return;
      if (signatureClickOverride.current(page, suggested)) return;
      chooser.open({ purpose: 'free', page, bbox: suggested, fieldId: null });
    },
    [blocked, chooser.open],
  );
  const onInitialsClick = useCallback(
    (page: number, at: Point) => {
      if (blocked) return;
      chooser.open({ purpose: 'initials', page, at });
    },
    [blocked, chooser.open],
  );
  const toolGate = useMemo(() => ({ blocked, reason: blockedReason }), [blocked, blockedReason]);

  /* -- F1b: the edit session (EDITOR-V2-PLAN §7) ----------------------------- */

  const markupRef = useRef<MarkupSync | null>(null);
  const [committed, setCommitted] = useState<CommitNotice | null>(null);
  const reloadDocument = useCallback(
    async (options?: { holdView?: boolean }) => {
      if (options?.holdView) viewer.holdViewForNextSource();
      await contractDocument.reload();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- both are stable callbacks
    [viewer.holdViewForNextSource, contractDocument.reload],
  );
  const onCommitted = useStableCallback((notice: CommitNotice) => {
    setCommitted(notice);
    // The review may have been reset, signatures removed, the contract re-parsed.
    void refresh();
    if (contractId && notice.out.remapped.signatures_removed > 0) {
      signaturesApi.list(contractId).then(setSignatures).catch(() => undefined);
    }
  });
  const pagesEndedRef = useRef<((reason: 'commit' | 'cancel' | 'conflict') => void) | null>(null);
  const onSessionEnded = useStableCallback((reason: 'commit' | 'cancel' | 'conflict') => {
    // ROTFIX: a commit keeps the draft's page view until the new version is loaded.
    pagesEndedRef.current?.(reason);
    // A cancel / a conflict threw the session's markup away: the server's comes back.
    void markupRef.current?.refresh(reason !== 'commit');
  });
  const session = useEditSession({
    contractId,
    editor: editorDocument,
    reloadDocument,
    history,
    markupWire: () => markupRef.current?.currentWire() ?? null,
    markupChanged: () => markupRef.current?.changedSinceServer() ?? false,
    onCommitted,
    onEnded: onSessionEnded,
  });
  const editing = session.mode === 'edit';
  const share = useShare({ contractId, editor: editorDocument });

  /* -- F2: ops that are not text edits, the gallery, the link dialog ---------- */
  const v2 = editorDocument.status === 'ready';
  const visibleTools = useMemo(() => toolsFor(v2), [v2]);
  const shownUidsRef = useRef<readonly string[]>([]);
  const docOps = useDocumentOps({ session, shownUids: () => shownUidsRef.current, setNote });
  const [linkEdit, setLinkEdit] = useState<LinkEdit | null>(null);
  const [galleryPick, setGalleryPick] = useState<AssetOut | null>(null);
  const galleryPickRef = useRef<AssetOut | null>(null);
  galleryPickRef.current = galleryPick;
  const placeFromGalleryRef = useRef<(page: number, at: Point, asset: AssetOut) => void>(() => undefined);

  const tools = useAnnotationTools({
    editor: v2,
    // ROTFIX: a drag tool draws on the page AS DISPLAYED; redactions and links
    // are the engine's (unrotated page) -- one map, `lib/viewport.ts`.
    onRedact: (page, rect) => docOps.redact(page, toEngineRect(page, rect)),
    onOpenLink: (page, rect) => {
      const uid = docOps.uidOf(page);
      if (uid) setLinkEdit({ page, pageUid: uid, rect: toRect(toEngineRect(page, rect)), link: null });
    },
    onPickImage: (page, at) => {
      const asset = galleryPickRef.current;
      if (!asset) return false;
      placeFromGalleryRef.current(page, at, asset);
      return true;
    },
    contractId,
    initialTool: panel === 'sign' ? 'signature' : 'select',
    scale: viewer.scale,
    pageSizeOf,
    selection,
    history,
    gate: toolGate,
    onSignatureClick,
    onInitialsClick,
    setNote,
    // Once the server holds the annotations, this browser's copy is legacy data.
    persistLocal: editorDocument.status !== 'ready',
  });
  const { store, edit, tool, toolId, placing } = tools;

  // F2: the page structure (insert / delete / move / rotate) of the draft.
  const pages = usePageStructure({
    contractId,
    basePages: editorDocument.pages,
    session,
    ops: docOps,
    store,
    setNote,
    currentPage: viewer.currentPage,
    scrollToPage: viewer.scrollToPage,
    pageSizesKey: viewer.pageSizes,
  });
  shownUidsRef.current = pages.shownUids;
  pagesEndedRef.current = pages.sessionEnded;
  useEffect(() => {
    setDraftBytes(pages.draftSource);
  }, [pages.draftSource]);
  /** ROTFIX: the annotations' page N as displayed (the store's numbering), else the viewer's own. */
  const markupFrameOf = useCallback(
    (page: number) => pages.storeFrameOf(page) ?? viewer.frameOf(page),
    [pages.storeFrameOf, viewer.frameOf],
  );
  // The markup wire speaks uids: by the order the annotations are numbered in
  // (a draft may have moved the pages; `storeUids` changes with the renumbering).
  const storeUids = pages.storeUids;
  const baseSame =
    storeUids.length === editorDocument.pages.length &&
    storeUids.every((uid, index) => editorDocument.pages.find((entry) => entry.position === index + 1)?.page_uid === uid);
  const shownEditor = useMemo(
    () =>
      !baseSame && storeUids.length > 0
        ? {
            ...editorDocument,
            pages: storeUids.map((uid, index) => ({
              page_uid: uid,
              position: index + 1,
              origin_page: null,
              rotation: 0,
              width_pt: 0,
              height_pt: 0,
              has_text: true,
              scanned: false,
            })),
          }
        : editorDocument,
    [editorDocument, baseSame, storeUids],
  );

  const markup = useMarkupSync({
    contractId,
    editor: shownEditor,
    store,
    inSession: editing,
    onSessionChange: session.markupTouched,
    setNote,
    frameOf: markupFrameOf,
  });
  markupRef.current = markup;

  const textEditing = useTextEditing({
    // F2: «Link» on the text toolbar -- the link dialog over the block's box.
    onLink: (page, pageUid, box) => setLinkEdit({ page, pageUid, rect: toRect(box), link: null }),
    contractId,
    session,
    editor: editorDocument,
    viewer,
    armed: toolId === 'edit-text',
    pageSizeOf,
    store,
    selectAnnotation: selection.selectAnnotation,
    setNote,
  });
  // F2b: interactive form fields (Forms ▾ fields, fill, tab order, detect) -- `workspace/forms/`.
  const forms = useForms({ contractId, session, editor: editorDocument, viewer, toolId, setTool: tools.setTool,
    shownUids: pages.shownUids, setNote, clearOtherSelection: selection.clear });

  // Signing waits while the document is being edited: a Signature / Initials
  // tool that was armed goes back to Select.
  useEffect(() => {
    if (editing && (toolId === 'signature' || toolId === 'initials')) tools.setTool('select');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on entering the session
  }, [editing]);
  // W4: the pictures the image annotations point at (fetched once each).
  const imageAssetIds = useMemo(
    () => store.items.filter(isImage).map((item) => item.asset_id),
    [store.items],
  );
  const assets = useAnnotationAssets(contractId, imageAssetIds);
  // A tool that writes to the server (`serverWrite`) is off while signing is blocked.
  const disabledTools = useMemo<Partial<Record<ToolId, string>>>(() => {
    const off: Partial<Record<ToolId, string>> = {};
    if (blockedReason) {
      visibleTools.forEach((item) => {
        if (item.serverWrite) off[item.id] = blockedReason;
      });
    }
    // F1b: no signing while the document is being edited (EDITOR-V2-PLAN §7).
    if (editing) {
      off.signature = FINALIZE_WHILE_EDITING;
      off.initials = FINALIZE_WHILE_EDITING;
    }
    // F2: the ops tools need the right to edit the document itself.
    if (v2 && session.capabilities && !session.capabilities.can_edit) {
      const why =
        session.capabilities.reason === 'not_permitted'
          ? 'Only the uploader, a reviewer or an admin can change the document itself'
          : session.capabilities.reason === 'contract_completed'
            ? 'The contract is completed'
            : 'The document cannot be edited right now';
      (['links', 'redact', 'image-remove'] as ToolId[]).forEach((id) => {
        off[id] = why;
      });
      FORM_TOOL_IDS.forEach((id) => { off[id] = why; }); // F2b
    }
    // F2b: the form tools need the version's fields.
    if (v2 && !forms.available) FORM_TOOL_IDS.forEach((id) => { off[id] = forms.loadError ?? 'Reading the form fields…'; });
    // F2: the text model is the saved version's -- not while pages are being rearranged.
    if (pages.hasPageOps) off['edit-text'] = 'Save the page changes first, then edit the text';
    return off;
  }, [blockedReason, editing, visibleTools, v2, session.capabilities, pages.hasPageOps, forms.available, forms.loadError]);
  const interactive = tool.placesOnClick && !(tool.serverWrite && blocked);

  /* -- finalize: ONE, gated the signing way (plan §7-9) ---------------------- */

  // A 422 about `layout` is either stale per-slot keys (dropped here so the
  // next attempt goes through) or a value the server will not take.
  const describeFinalizeError = useCallback(
    (cause: unknown) => {
      if (cause instanceof ApiError && cause.status === 422) {
        const { text, unknownSlots } = readLayoutRejection(cause);
        if (unknownSlots.length > 0) {
          const names = unknownSlots.map((fieldId) => slotById.get(fieldId)?.label ?? fieldId);
          // The app's own correction, not the user's: no undo step.
          replaceLayout((current) => withoutSlotOffsets(current, unknownSlots));
          return (
            `The position settings named slots this contract does not have: ${names.join(', ')}. ` +
            'Those adjustments were removed — generate the final contract again.'
          );
        }
        return (
          `The server refused the position settings: ${text}. ` +
          'Adjust them or use Reset in “Position & appearance”, then try again.'
        );
      }
      return signingErrorText(cause, 'Could not generate the final contract');
    },
    [slotById, replaceLayout],
  );
  const { finalize: sendFinalize, missing: finalizeMissing } = useFinalize({
    contractId,
    setBusy,
    setError,
    describeError: describeFinalizeError,
  });

  const finalize = async () => {
    if (!contractId) return;
    // Last guard that every mark prints inside its page (the clamp in `useSlotLayout`).
    const sendLayout = fitLayoutToPages(layout, slots, pageSizeOf);
    if (sendLayout !== layout) replaceLayout(sendLayout);
    // F2 (EDITOR-V2-PLAN §4, B2 `annotations_in_two_places`): with the markup
    // on the server, finalize draws THAT -- the body carries no annotations
    // (sending both is a 422). Anything not saved yet is saved first.
    const serverMarkup = markup.serverBacked;
    if (serverMarkup && !(await markup.flush())) {
      setError('The annotations could not be saved to the server; nothing was finalized. Try again.');
      return;
    }
    void sendFinalize({
      dates: dateValues,
      layout: sendLayout,
      slotIds: slots.map((slot) => slot.field_id),
      signatureSlotIds: sigSlots.map((slot) => slot.field_id),
      // An empty list leaves the key out (`buildFinalizeBody`).
      // ROTFIX: a v7 entry is drawn by the engine on the UNROTATED page.
      annotations: serverMarkup
        ? undefined
        : annotationsForRequest(store.items).map((entry) => toServerSpace(entry, markupFrameOf(entry.page))),
    });
  };

  /*
   * F1a: «Download PDF» in the top bar -- the document as it is on the server
   * now: the signed final PDF once there is one, else the uploaded source.
   * Both endpoints are authorised (SSNs), so the bytes come through one
   * bearer-token request and are handed to the browser as a blob, the way
   * `FinalContract` does it. The export with the annotations baked in is F3.
   */
  const [downloading, setDownloading] = useState(false);
  const finalAvailable = Boolean(contract?.final_available);
  const downloadPdf = useCallback(async () => {
    if (!contractId) return;
    setDownloading(true);
    try {
      const bytes = await fetchPdf(
        finalAvailable ? `/api/download/${contractId}/final` : `/api/contracts/${contractId}/file`,
      );
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = finalAvailable ? `contract-${contractId}-signed.pdf` : `contract-${contractId}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (cause) {
      setError(cause instanceof Error ? `Download failed: ${cause.message}` : 'Download failed');
    } finally {
      setDownloading(false);
    }
  }, [contractId, finalAvailable]);
  // F2: «Download PDF» with the editor = the export dialog (annotations in or out).
  const [exportOpen, setExportOpen] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const doExport = useCallback(
    async (includeMarkup: boolean) => {
      if (!contractId) return;
      setExportBusy(true);
      setExportError(null);
      try {
        if (markup.serverBacked) await markup.flush();
        const bytes = await runExport(contractId, includeMarkup);
        const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `contract-${contractId}${includeMarkup ? '-annotated' : ''}.pdf`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 2000);
        setExportOpen(false);
      } catch (cause) {
        setExportError(cause instanceof Error ? `The export failed: ${cause.message}` : 'The export failed');
      } finally {
        setExportBusy(false);
      }
    },
    [contractId, markup],
  );
  const onDownload = () => {
    if (v2 && !finalAvailable) {
      setExportError(null);
      setExportOpen(true);
      return;
    }
    void downloadPdf();
  };

  const contractTitle = contract
    ? contract.contractor_name?.trim() || `Contract ${contract.id.slice(0, 8)}`
    : 'Loading…';
  // F1b: the top bar's session group (absent without a document editor).
  const sessionNode = sessionControls(session);
  const sessionSlot = sessionNode
    ? { controls: sessionNode, finalizeBlockedReason: editing ? FINALIZE_WHILE_EDITING : null }
    : undefined;
  const canConvert = Boolean(textEditing.available && session.capabilities?.can_edit);
  const convertGroup = useStableCallback((annotation: Annotation) => {
    const group = editGroups(store.items).find(
      (entry) => entry.whiteout.id === annotation.id || entry.text?.id === annotation.id,
    );
    if (!group) return;
    if (toolId !== 'edit-text') tools.setTool('edit-text');
    void textEditing.convertGroup(group);
  });

  const missingSignatures = useMemo(
    () => sigSlots.filter((slot) => slot.required && !signedFieldIds.has(slot.field_id)),
    [sigSlots, signedFieldIds],
  );
  const missingDates = useMemo(
    () => dateSlots.filter((slot) => slot.required && !dateValues[slot.field_id]),
    [dateSlots, dateValues],
  );

  const pickerTitle =
    chooser.request?.purpose === 'bulk'
      ? 'Signature for every slot'
      : chooser.request?.purpose === 'initials'
        ? 'Place your initials here'
        : chooser.request?.fieldId
        ? `Sign: ${slotById.get(chooser.request.fieldId)?.label ?? chooser.request.fieldId}`
        : 'Place a signature here';

  /* -- the page overlay ------------------------------------------------------ */

  // The slot the mini panel is about: under the pointer, else the last picked
  // one; dropped while that slot is being dragged, and a resize wins over both.
  const toolsSlotId = resize.activeId ?? hoveredSlotId ?? focusedSlotId;
  const toolsSlot = toolsSlotId ? slotById.get(toolsSlotId) : undefined;
  const toolsTarget =
    toolsSlot && toolsSlot.bbox && drag.draggingId !== toolsSlot.field_id ? toolsSlot : null;

  const { displayPage } = pages;
  const issuesByPage = useMemo(
    () =>
      byPage(
        issues.filter((issue) => issue.bbox !== null && issue.kind !== 'signature_slot'),
        (issue) => displayPage(issue.page),
      ),
    [issues, displayPage],
  );
  const slotSignaturesByPage = useMemo(
    () =>
      byPage(
        signatures.filter((row) => row.field_id !== null && sigSlotIds.has(row.field_id)),
        (row) => displayPage(row.page_number),
      ),
    [signatures, sigSlotIds, displayPage],
  );
  const freeByPage = useMemo(
    () => byPage<Signature>(freeSignatures, (row) => displayPage(row.page_number)),
    [freeSignatures, displayPage],
  );
  // F2: the template's slots, on the pages as shown (a moved page takes its slots along).
  const remapSlots = useCallback(
    <T,>(map: Map<number, T[]>): Map<number, T[]> => {
      if (!pages.hasPageOps) return map;
      const next = new Map<number, T[]>();
      map.forEach((list, page) => {
        const at = displayPage(page);
        if (at) next.set(at, list);
      });
      return next;
    },
    [displayPage, pages.hasPageOps],
  );
  const sigSlotsShown = useMemo(() => remapSlots(slotSet.sigByPage), [remapSlots, slotSet.sigByPage]);
  const dateSlotsShown = useMemo(() => remapSlots(slotSet.dateByPage), [remapSlots, slotSet.dateByPage]);
  // A page whose annotations did not change keeps the very same list, so a
  // commit on page 1 does not re-render page 40 (its memo compares identities).
  const previousAnnotationsByPage = useRef(new Map<number, Annotation[]>());
  const annotationsByPage = useMemo(() => {
    const fresh = byPage<Annotation>(store.items, (item) => item.page);
    const previous = previousAnnotationsByPage.current;
    fresh.forEach((list, page) => {
      const old = previous.get(page);
      if (old && old.length === list.length && old.every((item, index) => item === list[index])) {
        fresh.set(page, old);
      }
    });
    previousAnnotationsByPage.current = fresh;
    return fresh;
  }, [store.items]);
  // The live gesture is applied here and nowhere else, so the stored list stays
  // exactly what was committed -- and only the page it happens on gets a new list.
  const resolvedByPage = useMemo(() => {
    const map = new Map(annotationsByPage);
    annotationsByPage.forEach((list, page) => {
      const next = list.map(edit.resolve);
      if (next.some((item, index) => item !== list[index])) map.set(page, next);
    });
    return map;
  }, [annotationsByPage, edit.resolve]);

  // Stable handler identities for the memoised pages.
  const selectIssueOnPage = useStableCallback((issue: PageIssue) =>
    selection.selectIssue(issue.key, issue.fieldId),
  );
  // A group member (WE) selects its whole group, the text first.
  const onSelectAnnotation = useStableCallback((id: string) => tools.selectGroup(id));
  const onEditAnnotation = useStableCallback((annotation: Annotation) =>
    tools.editAnnotation(annotation),
  );
  const onEditorChange = useStableCallback((value: string) => {
    const current = tools.inlineRef.current;
    if (current) tools.openInline({ ...current, value });
  });
  const onEditorCommit = useStableCallback(() => tools.commitInline());
  const onEditorCancel = useStableCallback(() => tools.cancelInline());
  // W4: the sticky-note box and the drag tools' press.
  const onNoteChange = useStableCallback((value: string) => {
    const current = tools.noteRef.current;
    if (current) tools.openNote({ ...current, value });
  });
  const onNoteCommit = useStableCallback(() => tools.commitNote());
  const onNoteCancel = useStableCallback(() => tools.cancelNote());
  // F2: the text box editor.
  const onBoxChange = useStableCallback((value: string) => {
    const current = tools.boxRef.current;
    if (current) tools.openTextbox({ ...current, value, error: null });
  });
  const onBoxCommit = useStableCallback(() => tools.commitTextbox());
  const onBoxCancel = useStableCallback(() => tools.cancelTextbox());
  const beginGesture = useStableCallback((page: number, pt: Point) => tools.beginGesture(page, pt));
  const capture = useMemo(
    () => (tools.drawing ? { cursor: tool.cursor, begin: beginGesture, pageSizeOf } : null),
    [tools.drawing, tool.cursor, beginGesture, pageSizeOf],
  );
  const onRemoveFree = useStableCallback((id: string) => void removeFreeSignature(id));

  // WHAT A GESTURE RE-RENDERS (W2). A drag -- of an annotation, of a slot, or
  // of a signature's corner grip -- changes its preview on every pointer move.
  // Only the page the gesture is on may see that: every other page is handed
  // the RESTING version of the same props (offsets / scales straight from the
  // layout, no dragging id), whose identity does not move with the pointer, so
  // their memoised `PageOverlay` is skipped. Measured by `test_toolbar.py`
  // (`window.__swmOverlayRenders`).
  const restingDrag = useMemo(
    () => ({
      offsetsOf: (fieldId: string) => ({
        dx: layout.slot_offsets_x_pt[fieldId] ?? 0,
        dy: layout.slot_offsets_pt[fieldId] ?? 0,
      }),
      draggingId: null,
      bind: drag.bind,
    }),
    [layout, drag.bind],
  );
  const liveDrag = useMemo(
    () => ({ offsetsOf: drag.offsetsOf, draggingId: drag.draggingId, bind: drag.bind }),
    [drag.offsetsOf, drag.draggingId, drag.bind],
  );
  const restingResize = useMemo(
    () => ({
      scaleOf: (fieldId: string | null | undefined) => signatureScaleFor(layout, fieldId),
      activeId: null,
      boundsOf: resize.boundsOf,
      bind: resize.bind,
      step: resize.step,
      clear: resize.clear,
    }),
    [layout, resize.boundsOf, resize.bind, resize.step, resize.clear],
  );
  const liveResize = useMemo(
    () => resize,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- its parts are what change
    [resize.scaleOf, resize.activeId, resize.boundsOf, resize.bind, resize.step, resize.clear],
  );
  const liveSlotId = drag.draggingId ?? resize.activeId;
  const liveSlotPage = liveSlotId ? (slotById.get(liveSlotId)?.page ?? null) : null;

  const restingEdit = useMemo(
    () => ({
      bindMark: edit.bindMark,
      bindHandle: edit.bindHandle,
      bindShapeHandle: edit.bindShapeHandle,
      stepSize: edit.stepSize,
      draggingId: null,
      resizingId: null,
    }),
    [edit.bindMark, edit.bindHandle, edit.bindShapeHandle, edit.stepSize],
  );
  const liveEdit = useMemo(
    () => ({ ...restingEdit, draggingId: edit.draggingId, resizingId: edit.resizingId }),
    [restingEdit, edit.draggingId, edit.resizingId],
  );
  const liveAnnotationId = edit.draggingId ?? edit.resizingId;
  const liveAnnotationPage = liveAnnotationId ? (store.get(liveAnnotationId)?.page ?? null) : null;

  const pressOpensChooser = toolId === 'signature';
  const { set: setDateValue } = dates;
  const { focusSlot, showSlotTools, hideSlotTools } = focus;
  const slotShared = useMemo<SlotLayerShared>(
    () => ({
      sigSlotsByPage: sigSlotsShown,
      dateSlotsByPage: dateSlotsShown,
      signaturesByPage: slotSignaturesByPage,
      slotById,
      dateValues,
      signedFieldIds,
      signatureForSlot,
      signatureImages,
      layout,
      drag: restingDrag,
      resize: restingResize,
      scale: viewer.scale,
      focusedSlotId,
      // Only the page the mini panel is on gets it (`slotsForPage`).
      toolsTarget: null,
      busy,
      blocked,
      pressOpensChooser,
      onStartSlot: startSlot,
      onReplaceSlot: replaceSlot,
      onRemoveSignature: removeSignature,
      onDateChange: setDateValue,
      onFocusSlot: focusSlot,
      onShowTools: showSlotTools,
      onHideTools: hideSlotTools,
    }),
    [
      sigSlotsShown,
      dateSlotsShown,
      slotSignaturesByPage,
      slotById,
      dateValues,
      signedFieldIds,
      signatureForSlot,
      signatureImages,
      layout,
      restingDrag,
      restingResize,
      viewer.scale,
      focusedSlotId,
      busy,
      blocked,
      pressOpensChooser,
      startSlot,
      replaceSlot,
      removeSignature,
      setDateValue,
      focusSlot,
      showSlotTools,
      hideSlotTools,
    ],
  );
  const initialIds = initials.initialIds;
  // The PDF prints a free signature at the GLOBAL offset and size (contract
  // v1.1), so its preview follows the same two numbers -- and only them, so a
  // slot drag (per-slot maps) does not re-render the free rows' pages.
  const { signature_offset_pt: freeOffset, signature_scale: freeScale } = layout;
  const freeLayout = useMemo(
    () => ({ signature_offset_pt: freeOffset, signature_scale: freeScale }),
    [freeOffset, freeScale],
  );
  const freeShared = useMemo<FreeSignatureShared>(
    () => ({
      signatureImages,
      selectedId: selection.signatureId,
      busy,
      onRemove: onRemoveFree,
      initialIds,
      layout: freeLayout,
    }),
    [signatureImages, selection.signatureId, busy, onRemoveFree, initialIds, freeLayout],
  );
  const slotSharedTools = useMemo<SlotLayerShared>(
    () => ({ ...slotShared, toolsTarget }),
    [slotShared, toolsTarget],
  );
  const slotSharedLive = useMemo<SlotLayerShared>(
    () => ({ ...slotShared, toolsTarget, drag: liveDrag, resize: liveResize }),
    [slotShared, toolsTarget, liveDrag, liveResize],
  );
  const toolsTargetPage = toolsTarget?.page ?? null;
  const slotsForPage = (pageNumber: number) =>
    pageNumber === liveSlotPage
      ? slotSharedLive
      : pageNumber === toolsTargetPage
        ? slotSharedTools
        : slotShared;
  const annotationShared = useMemo<AnnotationShared>(
    () => ({
      edit: restingEdit,
      selectedId: tools.selectedId,
      selectedIds: tools.selectedIds,
      onSelect: onSelectAnnotation,
      onEdit: onEditAnnotation,
      onEditorChange,
      onEditorCommit,
      onEditorCancel,
      assets,
      onNoteChange,
      onNoteCommit,
      onNoteCancel,
      onBoxChange,
      onBoxCommit,
      onBoxCancel,
      capture,
    }),
    [
      restingEdit,
      tools.selectedId,
      tools.selectedIds,
      onSelectAnnotation,
      onEditAnnotation,
      onEditorChange,
      onEditorCommit,
      onEditorCancel,
      assets,
      onNoteChange,
      onNoteCommit,
      onNoteCancel,
      capture,
    ],
  );
  const annotationSharedLive = useMemo<AnnotationShared>(
    () => ({ ...annotationShared, edit: liveEdit }),
    [annotationShared, liveEdit],
  );

  const renderPageOverlay = (pageNumber: number) => {
    // ROTFIX: the page as shown, and the space the server's marks of it are in.
    const frame = viewer.frameOf(pageNumber);
    const uid = pages.shownUids[pageNumber - 1];
    return (
    <PageOverlay
      pageNumber={pageNumber}
      scale={viewer.scale}
      frameRotation={frame?.rotation ?? 0}
      frameWidth={frame?.width ?? 0}
      frameHeight={frame?.height ?? 0}
      serverSpace={uid && editorDocument.pages.length > 0 ? pages.baseRotation(uid) : undefined}
      issues={issuesByPage.get(pageNumber)}
      activeFieldId={selection.slotFieldId}
      onSelectIssue={selectIssueOnPage}
      slots={slotsForPage(pageNumber)}
      freeSignatures={freeByPage.get(pageNumber)}
      free={freeShared}
      annotations={resolvedByPage.get(pageNumber)}
      inline={tools.inline && tools.inline.page === pageNumber ? tools.inline : null}
      note={tools.noteEditor && tools.noteEditor.page === pageNumber ? tools.noteEditor : null}
      annotation={pageNumber === liveAnnotationPage ? annotationSharedLive : annotationShared}
      box={tools.boxEditor && tools.boxEditor.page === pageNumber ? tools.boxEditor : null}
      extra={renderPageExtras(pageNumber)}
    />
    );
  };

  /* -- F2: links, pictures of the PDF, pending redactions -------------------- */

  const currentVersionNo = editorDocument.doc?.current_version.version_no ?? null;
  const windowUids = useMemo(
    () => [...viewer.windowPages].map((page) => pages.shownUids[page - 1]).filter((uid): uid is string => Boolean(uid)),
    [viewer.windowPages, pages.shownUids],
  );
  const serverLinks = usePageLinks(contractId, currentVersionNo, windowUids, v2 && toolId === 'links');
  const pageImages = usePageImages(contractId, currentVersionNo, windowUids, v2 && toolId === 'image-remove');
  const opsByUid = useMemo(() => {
    const map = new Map<string, typeof session.ops>();
    session.ops.forEach((op) => {
      if (op.kind !== 'redact' && op.kind !== 'image.remove') return;
      const list = map.get(op.page_uid);
      if (list) list.push(op);
      else map.set(op.page_uid, [op]);
    });
    return map;
  }, [session.ops]);
  const pageLabel = useCallback(
    (uid: string) => {
      const at = pages.shownUids.indexOf(uid);
      return at < 0 ? '(deleted)' : String(at + 1);
    },
    [pages.shownUids],
  );
  const openLink = useStableCallback((link: ShownLink) => {
    const page = docOps.pageOf(link.pageUid);
    if (page) setLinkEdit({ page, pageUid: link.pageUid, rect: link.rect, link });
  });
  const reshapeLink = useStableCallback((link: ShownLink, rect: DocRect) => {
    if (link.target.type === 'other') {
      setNote('This link points somewhere the editor cannot write back; it can only be deleted.');
      return;
    }
    void linkChange(docOps, { pageUid: link.pageUid, rect, target: link.target, link });
  });
  const renderPageExtras = (pageNumber: number) => {
    if (!v2) return null;
    const uid = pages.shownUids[pageNumber - 1];
    if (!uid) return null;
    const pending = opsByUid.get(uid) ?? [];
    const removed = new Set(pending.flatMap((op) => (op.kind === 'image.remove' ? [op.xref] : [])));
    return (
      <>
        {pending.length > 0 && (
          <PendingOpsLayer
            page={pageNumber}
            scale={viewer.scale}
            frame={viewer.frameOf(pageNumber)}
            ops={pending}
            onDrop={(op) => void docOps.dropOp(op.id, op.kind === 'redact' ? 'Undo redaction' : 'Keep picture')}
          />
        )}
        {toolId === 'links' && (
          <LinkLayer
            page={pageNumber}
            scale={viewer.scale}
            frame={viewer.frameOf(pageNumber)}
            links={shownLinks(serverLinks.get(uid) ?? [], session.ops, uid)}
            pageLabel={pageLabel}
            onOpen={openLink}
            onReshape={reshapeLink}
          />
        )}
        {toolId === 'image-remove' && (
          <PageImageLayer
            page={pageNumber}
            scale={viewer.scale}
            frame={viewer.frameOf(pageNumber)}
            images={pageImages.get(uid) ?? []}
            removed={removed}
            onRemove={(image) => void docOps.removeImage(uid, image.xref, image.bbox)}
          />
        )}
        {forms.renderLayer(pageNumber, uid) /* F2b: the page's form fields, on top */}
      </>
    );
  };

  // Select mode: a click on empty paper lets go of whatever is selected. (The
  // viewer only reports clicks while a placing tool is armed.)
  const onStageClick = (event: MouseEvent<HTMLDivElement>) => {
    if (toolId !== 'select') return;
    const target = event.target as HTMLElement | null;
    if (!target?.closest('.pdf-page') || target.closest('.pdf-overlay')) return;
    selection.clear();
  };

  // The page's own text, for a tool that works on it (edit-text, WE): only on
  // the render window's pages and never on a turned one (`PdfViewer` decides).
  const onTextLine = useStableCallback((page: number, line: TextRun) =>
    tools.handleTextRun(page, line),
  );
  const { renderTextLayer: drawTextLayer } = viewer;
  // W3: the drawn layers, by page, for the text-selection tools
  // (highlight / underline / strikeout) -- `useTextSelection` reads a
  // selection over them into boxes. Mutated in place: no render needs it.
  const textLayers = useRef(new Map<number, TextLayerEntry>());
  const onTextLayerReady = useStableCallback((page: number, layer: TextLayerEntry | null) => {
    if (layer) textLayers.current.set(page, layer);
    else textLayers.current.delete(page);
  });
  useTextSelection({
    active: tools.textSelect,
    layers: textLayers.current,
    pageSizeOf,
    onSelect: tools.handleTextSelection,
  });
  // F1b: Edit text on a server that has the document editor draws the
  // SERVER's blocks (`TextBlockLayer`); anywhere else the pdf.js layer.
  const renderTextLayer = tools.textTools
    ? toolId === 'edit-text' && textEditing.available
      ? (pageNumber: number) => textEditing.renderLayer(pageNumber)
      : (pageNumber: number) => (
        <PdfTextLayer
          pageNumber={pageNumber}
          scale={viewer.scale}
          renderTextLayer={drawTextLayer}
          mode={tools.textSelect ? 'select' : 'lines'}
          onLine={onTextLine}
          onReady={onTextLayerReady}
        />
      )
    : undefined;

  const showAnnotation = (annotation: Annotation) => {
    scrollToPage(annotation.page);
    if (toolId !== 'select') tools.setTool('select');
    tools.selectGroup(annotation.id);
  };
  /** «Revert» on an edited line: the whole group goes, one undo step. */
  const revertGroup = (annotation: Annotation) => {
    tools.store.remove(annotation.id, 'Revert edited text');
    selection.selectAnnotation(null);
  };
  const showFreeSignature = (signature: Signature) => {
    scrollToPage(signature.page_number);
    selection.selectSignature(signature.id);
  };

  const issueCount = issues.filter((issue) => issue.kind !== 'signature_slot').length;

  /* -- F2: menus' extras, stamps, typed signatures, page tools ---------------- */

  const [markupHidden, setMarkupHidden] = useState(false);
  const [stampOpen, setStampOpen] = useState(false);
  const gallery = useGallery(v2);
  const signatureFonts = useEditorFonts(v2, 'signature');
  const typedFonts = useMemo(
    () =>
      (signatureFonts ?? [])
        .filter((font) => font.category === 'signature')
        .map((font) => ({ family: font.family as TypedSignatureFont, label: font.label })),
    [signatureFonts],
  );
  const [pickedSignature, setPickedSignature] = useState<UserSignature | null>(null);

  // A gallery picture: POST …/annotation-assets {from_asset_id} COPIES it onto
  // this contract (removing it from the gallery later leaves the placement).
  placeFromGalleryRef.current = (page, at, asset) => {
    setGalleryPick(null);
    if (!contractId) return;
    if (store.items.length >= MAX_ANNOTATIONS) {
      setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
      return;
    }
    setNote('Placing the picture…');
    void assetsApi
      .placeOnContract(contractId, asset.asset_id)
      .then((copy) => {
        const size = pageSizeOf(page);
        const id = makeAnnotationId(store.items.map((item) => item.id));
        const made = makeImage(
          {
            id,
            page,
            asset_id: copy.asset_id,
            box: placeImageBox({ width: copy.width_px || asset.width_px, height: copy.height_px || asset.height_px }, at, size),
          },
          size,
        );
        if (!made) return;
        store.add(made);
        selection.selectAnnotation(id);
        setNote(null);
      })
      .catch((cause) => setNote(cause instanceof Error ? `The picture could not be placed: ${cause.message}` : 'The picture could not be placed'));
  };

  // A saved signature picked in Sign ▾: the next click on the page places it (no dialog).
  const onSignatureClickF2 = (page: number, suggested: BBox) => {
    const picked = pickedSignature;
    if (!picked || !contractId) return false;
    setPickedSignature(null);
    setBusy(true);
    void userSignaturesApi
      .imageAsDataUrl(picked.id)
      .then((dataUrl) => contractSignatures.place({ page, bbox: suggested, fieldId: null, imageData: dataUrl }))
      .then((row) => selection.selectSignature(row.id))
      .catch((cause) => setError(signingErrorText(cause, 'Could not place the signature')))
      .finally(() => setBusy(false));
    return true;
  };
  signatureClickOverride.current = onSignatureClickF2;

  /** Type tab: the server renders the PNG and saves it; then it is placed like a stored one. */
  const placeTyped = async (text: string, font: TypedSignatureFont) => {
    setBusy(true);
    setError(null);
    try {
      const saved = await userSignaturesApi.saveTyped({ typed: { text, font } });
      await reloadLibrary();
      setBusy(false);
      await chooser.chooseStored(saved);
    } catch (cause) {
      setError(signingErrorText(cause, 'Could not make the typed signature'));
    } finally {
      setBusy(false);
    }
  };

  const menuExtras = useMemo(() => {
    if (!v2) return undefined;
    return {
      images: (close: () => void) => (
        <ImagesExtras
          gallery={gallery}
          close={close}
          onPick={(asset) => {
            if (blockedReason) {
              setNote(blockedReason);
              return;
            }
            setGalleryPick(asset);
            tools.setTool('image');
            setNote(`Click the page to place ${asset.label ?? 'the picture'}.`);
          }}
          onNewStamp={() => setStampOpen(true)}
        />
      ),
      sign: (close: () => void) => (
        <SignExtras
          library={mySignatures}
          blockedReason={editing ? FINALIZE_WHILE_EDITING : blockedReason}
          close={close}
          onPick={(signature) => {
            setPickedSignature(signature);
            tools.setTool('signature');
            setNote('Click the page where the signature goes.');
          }}
          onNew={() => {
            setPickedSignature(null);
            tools.setTool('signature');
            setNote('Click the page where the new signature goes.');
          }}
          onManage={() => window.open('/signatures', '_blank', 'noopener')}
        />
      ),
      annotate: (close: () => void) => (
        <AnnotateExtras
          hidden={markupHidden}
          count={store.items.length}
          close={close}
          onToggle={() => setMarkupHidden((value) => !value)}
        />
      ),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the menus read the latest state when opened
  }, [v2, gallery, mySignatures, markupHidden, store.items.length, blockedReason, editing]);

  // Page tools on the strip: with the editor, for someone who may edit the document.
  const [pageDelete, setPageDelete] = useState<{ page: number; counts: PageDeleteCounts; refusal: string | null } | null>(null);
  const askDeletePage = (position: number) => {
    const uid = pages.shownUids[position - 1];
    const signaturesOn = signatures.filter((row) => displayPage(row.page_number) === position).length;
    const slotsOn = (sigSlotsShown.get(position)?.length ?? 0) + (dateSlotsShown.get(position)?.length ?? 0);
    const annotationsOn = store.items.filter((item) => item.page === position).length;
    const refusal =
      slotsOn > 0 && !isAtLeast('reviewer')
        ? 'This page carries signature / date slots of the template: only a reviewer or an admin can delete it ' +
          '(the review then flags the removed slots).'
        : null;
    const counts: PageDeleteCounts = { signatures: signaturesOn, slots: slotsOn, links: null, annotations: annotationsOn };
    setPageDelete({ page: position, counts, refusal });
    if (contractId && uid && currentVersionNo !== null && editorDocument.pages.some((page) => page.page_uid === uid)) {
      documentApi
        .pageLinks(contractId, currentVersionNo, uid)
        .then((list) =>
          setPageDelete((current) =>
            current && current.page === position
              ? { ...current, counts: { ...current.counts, links: shownLinks(list, session.ops, uid).length } }
              : current,
          ),
        )
        .catch(() => undefined);
    } else {
      setPageDelete((current) => (current ? { ...current, counts: { ...current.counts, links: 0 } } : current));
    }
  };
  const pageTools =
    v2 && session.capabilities?.can_edit
      ? {
          onInsertAfter: pages.insertAfter,
          onDelete: askDeletePage,
          onRotate: pages.rotate,
          onMove: pages.move,
          rotationOf: (position: number) => pages.entries[position - 1]?.rotation ?? 0,
          busy: pages.loading || session.status === 'committing',
          error: pages.error,
        }
      : null;

  return (
    <div
      className={`review workspace editor--${toolId}${placing ? ' workspace--placing' : ''}${
        tools.textTools ? ' workspace--text-tools' : ''
      }${tools.textSelect ? ' workspace--text-select' : ''}${tools.drawing ? ' workspace--drawing' : ''}${
        editing ? ' workspace--editing' : ''
      }${markupHidden ? ' workspace--markup-hidden' : ''}`}
      data-testid="workspace"
      data-panel={panel}
      data-tool={toolId}
      data-session={session.mode}
      data-editor={editorDocument.status}
      data-markup-sync={markup.status}
      data-editor-v2={v2 ? 'true' : undefined}
      data-markup-hidden={markupHidden ? 'true' : undefined}
      data-pages={pages.shownUids.join(',') || undefined}
      style={{ '--tool-cursor': tool.cursor } as CSSProperties}
    >
      <div className="review__document workspace__stage" ref={stageRef} onClick={onStageClick}>
        {/* F1a: the editor's top bar (name, Download PDF, Generate final
            contract). Fixed height, so it never moves a page under a press. */}
        <EditorTopBar
          title={contractTitle}
          finalize={{
            busy,
            blocked,
            blockedReason,
            signatureCount: signatures.length,
            slotsComplete,
            onFinalize: finalize,
          }}
          download={{ finalAvailable, busy: downloading || exportBusy, onDownload, exportDialog: v2 && !finalAvailable }}
          session={sessionSlot}
          share={<ShareButton share={share} />}
        />
        {/* The banners that stay until something answers them. They sit in
            the document column (above the pages); while a press on a page is
            in progress their height is frozen (`useGestureLayoutLock`), so
            one appearing or going mid-drag cannot slide the page under the
            pointer. The passing notes are NOT here: see `floating` below. */}
        <div className="workspace__banners" ref={bannersRef} data-testid="workspace-banners">
          <FinalizeErrors error={error} missing={finalizeMissing} onShow={showSlot} />
          <SessionBanners
            session={session}
            markup={markup}
            committed={committed}
            onDismissCommitted={() => setCommitted(null)}
          />
          {comparisonError && <p className="alert alert--error">{comparisonError}</p>}
          {sameAsTemplate && (
            <p className="alert alert--warn" role="status" data-testid="template-match-warning">
              This contract appears to be the template file itself (page similarity{' '}
              {similarityPercent(sameAsTemplate)}%). Check that the correct file was uploaded.
            </p>
          )}
        </div>

        <FloatingToolbar
          stageRef={stageRef}
          tools={visibleTools}
          menuExtras={menuExtras}
          toolId={toolId}
          onTool={tools.setTool}
          disabledTools={disabledTools}
          properties={{
            kind: tools.activeKind,
            font: tools.activeFont,
            bold: tools.activeBold,
            italic: tools.activeItalic,
            onStyle: tools.changeStyle,
            size: tools.activeSize,
            onFont: tools.changeFont,
            onSize: tools.changeSize,
            date: tools.props.date,
            onDate: tools.setDate,
            color: tools.activeColor,
            colorDisabled: tools.colorDisabled,
            onColor: tools.changeColor,
            stroke: tools.activeStroke,
            onStroke: tools.changeStroke,
            fill: tools.activeFill,
            onFill: tools.changeFill,
            hasSelection: Boolean(tools.selected || tools.inline?.edit),
            onDelete: tools.removeSelected,
            canUndo: history.canUndo,
            canRedo: history.canRedo,
            undoDepth: history.undoDepth,
            redoDepth: history.redoDepth,
            undoLabel: history.undoLabel,
            redoLabel: history.redoLabel,
            onUndo: history.undo,
            onRedo: history.redo,
            count: store.items.length,
            signatureCount: freeSignatures.length,
            editor: v2,
            boxFont: tools.activeBoxFont,
            onBoxFont: tools.changeBoxFont,
            v7: tools.activeV7,
            onV7: tools.changeV7,
            onDuplicate: tools.duplicateSelected,
          }}
        />
        <PdfViewer
          viewer={viewer}
          renderPageOverlay={renderPageOverlay}
          renderTextLayer={renderTextLayer}
          interactive={interactive}
          onCanvasClick={tools.handleCanvasClick}
          onHighlightMatches={tools.highlightMatches}
          floating={<EditorNote note={note} onDismiss={dismissNote} />}
          pageTools={pageTools}
        />
        {forms.panel /* F2b: form field properties / tab order / detect */}
        {/* The Image tool's file dialog (W4): opened by a click on the page. */}
        <input
          ref={tools.imageInputRef}
          type="file"
          accept="image/png,image/jpeg"
          hidden
          data-testid="ann-image-file"
          aria-label="Choose a picture to place"
          onChange={(event) => void tools.onImageFile(event)}
        />
      </div>

      <aside className="review__panel workspace__panel" aria-label="Contract panel">
        <div className="workspace__sections">
          <PanelSection
            id="review"
            sections={sections}
            badge={
              result ? (
                <span data-testid="section-review-badge">
                  {issueCount} issue{issueCount === 1 ? '' : 's'}
                </span>
              ) : null
            }
          >
            <ReviewSection
              contract={contract}
              result={result}
              issues={issues}
              currentPage={viewer.currentPage}
              processing={processing}
              activeFieldId={selection.slotFieldId}
              onNavigateToIssue={goToIssue}
              onCorrectField={correctField}
              canCorrect={canCorrect}
              correcting={correcting}
              isReviewer={isReviewer}
              overrideActive={override.active}
              overrideState={overrideState}
              notes={notes}
              onNotesChange={setNotes}
              busy={busy}
              onDecide={(decision) => void decide(decision)}
              onApproveWithOverride={approveWithOverride}
              onContinueToSigning={() => setPanel('sign')}
            />
          </PanelSection>

          <PanelSection
            id="sign"
            sections={sections}
            badge={
              <span data-testid="section-sign-badge">
                {blocked
                  ? blockedReason
                  : `${signedFieldIds.size}/${sigSlots.length} signed`}
              </span>
            }
          >
            <SignSection
              contract={contract}
              result={result}
              gate={gate}
              signMode={signMode}
              onSignModeChange={setSignMode}
              autoFill={autoFill}
              onAutoFillChange={setAutoFill}
              autoFillNote={autoFillNote}
              onUndoAutoFill={undoAutoFill}
              slotSet={slotSet}
              signing={signing}
              dates={dates}
              bulk={bulk}
              focus={focus}
              onPickBulkSignature={() => chooser.open({ purpose: 'bulk' })}
              libraryEmpty={libraryLoaded && mySignatures.length === 0}
              onStartSlot={startSlot}
              onRemoveSignature={removeSignature}
              busy={busy}
              onGoToReview={() => setPanel('review')}
            />
          </PanelSection>

          <PanelSection id="layout" sections={sections}>
            {clampNote && (
              <p className="alert alert--warn" role="status" data-testid="clamp-note">
                {clampNote}
              </p>
            )}
            <LayoutPanel
              layout={layout}
              onChange={(next, meta) =>
                commitLayout(next, meta ?? { label: 'Position & appearance' })
              }
              onReset={resetLayout}
              rows={layoutRows}
              disabled={busy}
              onSlotScaleStep={(fieldId, direction, large) => {
                const slot = slotById.get(fieldId);
                if (slot) resize.step(slot, direction, large);
              }}
              onSlotScaleClear={(fieldId) => resize.clear(fieldId)}
            />
          </PanelSection>

          <PanelSection
            id="objects"
            sections={sections}
            badge={
              <span data-testid="section-objects-badge">
                {store.items.length + freeSignatures.length}
              </span>
            }
          >
            <ObjectsSection
              annotations={store.items}
              freeSignatures={freeSignatures}
              selectedAnnotationId={tools.selectedId}
              selectedSignatureId={selection.signatureId}
              busy={busy}
              onShowAnnotation={showAnnotation}
              onRevertGroup={revertGroup}
              onConvertGroup={canConvert ? convertGroup : undefined}
              serverBacked={markup.serverBacked}
              onShowSignature={showFreeSignature}
              onRemoveSignature={(id) => void removeFreeSignature(id)}
              initialIds={initialIds}
              initials={{
                blockedReason,
                progress: initials.progress,
                report: initials.report,
                onEveryPage: (signature) => void initials.putOnEveryPage(signature),
                onRemoveAll: () => void initials.removeAll(),
              }}
            />
          </PanelSection>

          <SubmissionsSection share={share} session={session} />
        </div>

        <FinalizeBar
          blocked={blocked}
          blockedReason={blockedReason}
          signatureCount={signatures.length}
          slotsComplete={slotsComplete}
          missingSignatures={missingSignatures}
          missingDates={missingDates}
          onShowMissing={showSlot}
        />
      </aside>

      <SignatureChooserHost
        chooser={chooser}
        title={pickerTitle}
        library={mySignatures}
        libraryLoaded={libraryLoaded}
        busy={busy}
        typedFonts={typedFonts}
        onTyped={v2 ? (text, font) => void placeTyped(text, font) : undefined}
      />
      <SessionDialogs session={session} markup={markup} />
      <ShareDialog share={share} />
      {linkEdit && (
        <LinkDialog
          edit={linkEdit}
          pageCount={pages.shownUids.length}
          uidAt={(position) => pages.shownUids[position - 1] ?? null}
          pageOfUid={(uid) => docOps.pageOf(uid)}
          onSave={(target: LinkTarget) => {
            const current = linkEdit;
            setLinkEdit(null);
            void linkChange(docOps, { pageUid: current.pageUid, rect: current.rect, target, link: current.link });
          }}
          onDelete={
            linkEdit.link
              ? () => {
                  const current = linkEdit.link as ShownLink;
                  setLinkEdit(null);
                  void linkRemoval(docOps, current);
                }
              : null
          }
          onClose={() => setLinkEdit(null)}
        />
      )}
      {stampOpen && (
        <StampDialog
          initial={tools.props.stamp}
          onSave={(spec) => {
            setStampOpen(false);
            tools.setStamp(spec);
            tools.setTool('stamp');
            setNote('Click the page to place the stamp.');
          }}
          onPicture={(file) => {
            setStampOpen(false);
            void gallery.add(file, 'stamp').then((made) => {
              if (!made) return;
              setGalleryPick(made);
              tools.setTool('image');
              setNote('Click the page to place the stamp picture.');
            });
          }}
          onClose={() => setStampOpen(false)}
        />
      )}
      {pageDelete && (
        <PageDeleteDialog
          page={pageDelete.page}
          counts={pageDelete.counts}
          refusal={pageDelete.refusal}
          onConfirm={() => {
            const target = pageDelete.page;
            setPageDelete(null);
            pages.remove(target);
          }}
          onClose={() => setPageDelete(null)}
        />
      )}
      {exportOpen && (
        <ExportDialog
          count={store.items.length}
          hiddenOnScreen={markupHidden}
          unsavedEdits={editing && session.ops.length > 0}
          busy={exportBusy}
          error={exportError}
          onDownload={(include) => void doExport(include)}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  );
}
