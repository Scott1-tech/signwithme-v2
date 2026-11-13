import { memo, type ComponentProps, type ReactNode } from 'react';
import type { TextboxEditorState } from '../components/V7Drawings';
import { AnnotationLayer, type InlineEditor, type NoteEditor } from '../components/AnnotationLayer';
import type { AnnotationEdit } from '../hooks/useAnnotationEdit';
import type { AssetImageEntry } from '../hooks/useAnnotationAssets';
import type { SignatureImageEntry } from '../hooks/useSignatureImages';
import type { Annotation } from '../lib/annotations';
import type { FinalizeLayout, PageSizePt } from '../lib/layout';
import type { PageIssue } from '../types/comparison';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';
import type { Gesture, Point } from '../workspace/tools/types';
import { frameLayerStyle, normalizeQuarter } from '../lib/viewport';
import { FreeSignatureLayer } from './FreeSignatureLayer';
import { IssueLayer } from './IssueLayer';
import { SlotLayer } from './SlotLayer';
import { ToolCaptureLayer } from './ToolCaptureLayer';
import { WhiteoutLayer } from './WhiteoutLayer';

/** Everything `SlotLayer` needs except the page, shared by every page. */
export type SlotLayerShared = Omit<ComponentProps<typeof SlotLayer>, 'pageNumber'>;

/** The annotation handlers, shared by every page. */
export interface AnnotationShared {
  /**
   * The gesture bindings; `draggingId` / `resizingId` are set ONLY in the
   * object handed to the page the gesture is on (W2 memo rule).
   */
  edit: Pick<
    AnnotationEdit,
    'bindMark' | 'bindHandle' | 'bindShapeHandle' | 'stepSize' | 'draggingId' | 'resizingId'
  >;
  selectedId: string | null;
  /** Every selected id: a group (WE) selects its whiteout and its text. */
  selectedIds: readonly string[];
  onSelect: (id: string) => void;
  onEdit: (annotation: Annotation) => void;
  onEditorChange: (value: string) => void;
  onEditorCommit: () => void;
  onEditorCancel: () => void;
  /** W4: the pictures `image` annotations point at. */
  assets: ReadonlyMap<string, AssetImageEntry>;
  onNoteChange: (value: string) => void;
  onNoteCommit: () => void;
  onNoteCancel: () => void;
  /** F2: the text box editor's handlers (the editor itself is per page). */
  onBoxChange?: (value: string) => void;
  onBoxCommit?: () => void;
  onBoxCancel?: () => void;
  /**
   * W4: while a DRAG tool is armed, every page gets a `ToolCaptureLayer`
   * (`null` otherwise). `begin` is the armed tool's `beginGesture`.
   */
  capture: {
    cursor: string;
    begin: (page: number, pt: Point) => Gesture | null;
    pageSizeOf: (page: number | null) => PageSizePt | null;
  } | null;
}

/** The free signatures' shared bits. */
export interface FreeSignatureShared {
  signatureImages: ReadonlyMap<string, SignatureImageEntry>;
  selectedId: string | null;
  busy: boolean;
  onRemove: (signatureId: string) => void;
  /** The corner handle: a free signature's new box (saved on the server). */
  onResize?: (signatureId: string, bbox: BBox) => void;
  /** W5: the free rows that are initials (`lib/initials.ts`). */
  initialIds: ReadonlySet<string>;
  /** The contract-wide signature offset / size, which the PDF applies to free rows too. */
  layout: Pick<FinalizeLayout, 'signature_offset_pt' | 'signature_scale'>;
}

interface Props {
  pageNumber: number;
  scale: number;
  /** This page's positioned issues (slots are skipped by `IssueLayer`). */
  issues: PageIssue[] | undefined;
  activeFieldId: string | null;
  onSelectIssue: (issue: PageIssue) => void;
  slots: SlotLayerShared;
  /** This page's free signatures. */
  freeSignatures: Signature[] | undefined;
  free: FreeSignatureShared;
  /** This page's annotations, with any live gesture already applied. */
  annotations: Annotation[] | undefined;
  /** The inline box, only when it is on this page. */
  inline: InlineEditor | null;
  /** The note box (W4), only when it is on this page. */
  note?: NoteEditor | null;
  annotation: AnnotationShared;
  /** F2: the text box editor, only when it is on this page. */
  box?: TextboxEditorState | null;
  /** F2: the page's own objects while a tool edits them (links, pictures of the PDF). */
  extra?: ReactNode;
  /**
   * ROTFIX: the page as the viewer shows it -- its own `/Rotate` and its
   * unrotated size (pt). Primitives, so the memo still holds.
   */
  frameRotation?: number;
  frameWidth?: number;
  frameHeight?: number;
  /**
   * ROTFIX: the rotation the SERVER's marks of this page are measured in
   * (review issues, slots, signatures: the current version's page as
   * displayed). Differs from `frameRotation` only while the edit session
   * shows a draft that turns the page: they are then drawn turned with it and
   * are inert (signing waits for the session anyway).
   */
  serverSpace?: number;
}

/**
 * Everything drawn on ONE page of the workspace, as direct children of that
 * page's `.pdf-overlay` (plan §2.4), bottom to top:
 *
 *   0. `ToolCaptureLayer`    W4: the drag tools' pointer surface (`.ws-capture`),
 *                            only while one is armed
 *   ½. `WhiteoutLayer`       W5: the whiteouts' drawing (no pointer events) --
 *                            under the slots and every signature, as printed
 *   1. `IssueLayer`        review issues that are not slots (`.highlight`)
 *   2. `SlotLayer`           slot boxes, slot signatures, slot dates, the mini
 *                            panel and the corner grip (`.slot`,
 *                            `.placed-signature`, `.placed-dates`)
 *   3. `FreeSignatureLayer`  free signatures (`.swm-sig`)
 *   4. `AnnotationLayer`     the annotation drawing (`.swm-ann-layer`, no pointer
 *                            events), their hit boxes, the selected one's grip,
 *                            the inline box
 *
 * The annotations sit last, which is also the order the PDF builder prints in
 * (signatures, then the other annotations; plan §7-4), and it keeps an
 * annotation dropped on top of a slot selectable. `PdfViewer` appends the
 * rotation shield after all of this, so a turned page stays inert.
 *
 * Memoised, and fed per-page slices (`issues`, `freeSignatures`,
 * `annotations`, `inline`) that keep their identity on pages nothing happened
 * to, so a change on one page does not have to re-render the others. A live
 * gesture (annotation or slot drag, corner resize) reaches only its own page:
 * the others get the resting `slots` / `annotation` objects (W2).
 *
 * In development every render is counted in `window.__swmOverlayRenders[page]`
 * -- the evidence `test_toolbar.py` reads to prove the memo holds.
 */
export const PageOverlay = memo(function PageOverlay({
  pageNumber,
  scale,
  issues,
  activeFieldId,
  onSelectIssue,
  slots,
  freeSignatures,
  free,
  annotations,
  inline,
  note = null,
  annotation,
  box = null,
  extra = null,
  frameRotation = 0,
  frameWidth = 0,
  frameHeight = 0,
  serverSpace,
}: Props) {
  if (import.meta.env.DEV) countRender(pageNumber);
  const { capture } = annotation;
  const own = normalizeQuarter(frameRotation);
  const server = serverSpace === undefined ? own : normalizeQuarter(serverSpace);
  const serverStyle =
    server !== own && frameWidth > 0 && frameHeight > 0
      ? frameLayerStyle({ rotation: own, width: frameWidth, height: frameHeight }, server, scale)
      : undefined;
  return (
    <>
      {capture && (
        <ToolCaptureLayer
          pageNumber={pageNumber}
          scale={scale}
          pageSize={capture.pageSizeOf(pageNumber)}
          cursor={capture.cursor}
          begin={capture.begin}
        />
      )}
      {/* W5: the whiteouts, under the slots and every signature -- the print order. */}
      <WhiteoutLayer pageNumber={pageNumber} items={annotations} scale={scale} />
      {wrapServer(
        serverStyle,
        server,
        <>
          {issues && issues.length > 0 && (
            <IssueLayer
              issues={issues}
              scale={scale}
              activeFieldId={activeFieldId}
              onSelect={onSelectIssue}
            />
          )}
          <SlotLayer pageNumber={pageNumber} {...slots} />
          {freeSignatures && freeSignatures.length > 0 && (
            <FreeSignatureLayer
              signatures={freeSignatures}
              signatureImages={free.signatureImages}
              scale={scale}
              selectedId={free.selectedId}
              busy={free.busy}
              onRemove={free.onRemove}
              onResize={free.onResize}
              initialIds={free.initialIds}
              layout={free.layout}
            />
          )}
        </>,
      )}
      {((annotations && annotations.length > 0) || inline || note || box) && (
        <AnnotationLayer
          pageNumber={pageNumber}
          items={annotations ?? EMPTY}
          scale={scale}
          selectedId={annotation.selectedId}
          selectedIds={annotation.selectedIds}
          editor={inline}
          noteEditor={note}
          assets={annotation.assets}
          edit={annotation.edit}
          onSelect={annotation.onSelect}
          onEdit={annotation.onEdit}
          onEditorChange={annotation.onEditorChange}
          onEditorCommit={annotation.onEditorCommit}
          onEditorCancel={annotation.onEditorCancel}
          onNoteChange={annotation.onNoteChange}
          onNoteCommit={annotation.onNoteCommit}
          onNoteCancel={annotation.onNoteCancel}
          boxEditor={box}
          onBoxChange={annotation.onBoxChange}
          onBoxCommit={annotation.onBoxCommit}
          onBoxCancel={annotation.onBoxCancel}
        />
      )}
      {extra}
    </>
  );
});

const EMPTY: Annotation[] = [];

/**
 * ROTFIX: the server's marks, as they are (direct children of `.pdf-overlay`,
 * every rule written against that stays true) -- or, while a draft turns the
 * page, inside ONE turned, inert wrapper (`frameLayerStyle`: the same map as
 * every other layer).
 */
function wrapServer(style: ReturnType<typeof frameLayerStyle>, space: number, children: ReactNode): ReactNode {
  if (!style) return children;
  return (
    <div className="pdf-overlay__frame" data-testid="server-marks-turned" data-space={space} style={style}>
      {children}
    </div>
  );
}

/** Dev-only render counter per page (see the header). */
function countRender(pageNumber: number): void {
  const host = window as unknown as { __swmOverlayRenders?: Record<number, number> };
  const counts = (host.__swmOverlayRenders ??= {});
  counts[pageNumber] = (counts[pageNumber] ?? 0) + 1;
}
