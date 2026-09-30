import { api, ApiError, fetchPdf } from './client';

/**
 * PDF editor v2 -- API contract (EDITOR-V2-PLAN §3, §6). FROZEN at B1-0.
 *
 * Mirror of `backend/app/schemas/document.py`; a change there is a change here.
 *
 * Geometry: PDF points, top-left origin, UNROTATED page space, relative to the
 * CropBox's top-left corner (same as `signatures.bbox`, annotations v5).
 * `revision` always travels in the BODY (`?revision=` for DELETE / preview).
 * B1: `text.replace`, `text.delete`. B2: `redact`, `image.remove`, `page.*`,
 * `link.*` (+ markup v7 kinds, exports, gallery, fonts, typed signatures).
 * `field.*` arrive in B3 (until then an unknown kind is 422). `layer` is never
 * sent: the server derives it from `kind`.
 */

// -- limits (§3) ---------------------------------------------------------------

export const MAX_OPS = 2000;
export const MAX_BODY_BYTES = 2 * 1024 * 1024; // 413 above this
export const MAX_TEXT_CHARS = 5000; // per text op, all runs together
export const MAX_MARKUP = 500;
export const TEXT_SIZE_MIN_PT = 4;
export const TEXT_SIZE_MAX_PT = 144;
export const LEASE_SECONDS = 120;
export const LEASE_RENEW_SECONDS = 45;

export const OP_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
export const PAGE_UID_RE = /^pg_[0-9a-f]{12}$/;
export const BLOCK_ID_RE = /^b[0-9]{1,6}$/;
export const LINE_ID_RE = /^b[0-9]{1,6}l[0-9]{1,6}$/;
export const SESSION_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;
export const REQUEST_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

// -- primitives ------------------------------------------------------------------

export type Hex = `#${string}`;
/** B1 families -- what `TextRunStyle.font` / `TextSpanOut.family` carry
 * (unchanged, so B1 code keeps compiling and never meets an unknown family). */
export type FontFamilyId = 'LiberationSans' | 'LiberationSerif' | 'LiberationMono';
/** B2: every embedded family the server can set text in (`GET /api/fonts`).
 * The server ACCEPTS all of these in `TextRunStyle.font` already; widen
 * `TextRunStyle.font` to this type when the font picker (F2) ships. */
export type EditorFontFamilyId =
  | FontFamilyId
  | 'Arimo'
  | 'Carlito'
  | 'Caladea'
  | 'DejaVuSans'
  | 'DejaVuSerif'
  | 'Amiri';
export type FitMode = 'wrap' | 'shrink' | 'overflow';
export type Align = 'left' | 'center' | 'right' | 'justify';

export interface Rect {
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
}

// -- ops ----------------------------------------------------------------------------

export interface TextRunStyle {
  text: string;
  /** F2: every embedded family of `GET /api/fonts` (B2 accepts them all). */
  font: EditorFontFamilyId;
  size_pt: number;
  color: Hex;
  bold?: boolean;
  italic?: boolean;
}

/** What the op edits, as the client saw it. If the block/lines no longer hold
 * `expect_text` (NFC) -> 409 `stale_target` at commit; `bbox` is the fallback
 * locator. */
export interface TextTarget {
  block_id: string;
  line_ids: string[];
  expect_text: string;
  bbox: Rect;
}

export interface TextReplaceOp {
  id: string;
  kind: 'text.replace';
  page_uid: string;
  target: TextTarget;
  runs: TextRunStyle[];
  /** Absent = the target's own bbox. Moving it moves the first baseline by the same dy. */
  box?: Rect;
  align?: Align; // default 'left'
  /** Multiple of the largest run size; absent = the original line spacing (or 1.2). */
  line_height?: number;
  fit?: FitMode; // default 'wrap'
}

export interface TextDeleteOp {
  id: string;
  kind: 'text.delete';
  page_uid: string;
  target: TextTarget;
}

// -- B2 ops ---------------------------------------------------------------------------

export const MAX_REDACT_RECTS = 200;
export const LINK_ID_RE = /^lk[0-9]{1,9}$/;

/** REAL redaction: text, image pixels and covered line art under the rects are
 * removed from the file. `fill` = what is left there (default black). */
export interface RedactOp {
  id: string;
  kind: 'redact';
  page_uid: string;
  rects: Rect[];
  fill?: 'black' | 'white' | 'none';
}

/** `xref` + `bbox` from `documentApi.pageImages`; a mismatch -> 409 stale_target. */
export interface ImageRemoveOp {
  id: string;
  kind: 'image.remove';
  page_uid: string;
  xref: number;
  bbox: Rect;
}

export interface PageInsertOp {
  id: string;
  kind: 'page.insert';
  /** Client-generated `pg_` + 12 hex; must not exist yet. */
  new_page_uid: string;
  /** null = before the first page. */
  after_page_uid: string | null;
  size?: 'same_as_previous' | { w_pt: number; h_pt: number };
}

export interface PageDeleteOp {
  id: string;
  kind: 'page.delete';
  page_uid: string;
}

export interface PageMoveOp {
  id: string;
  kind: 'page.move';
  page_uid: string;
  after_page_uid: string | null;
}

/** ABSOLUTE rotation. */
export interface PageRotateOp {
  id: string;
  kind: 'page.rotate';
  page_uid: string;
  rotation: 0 | 90 | 180 | 270;
}

/** http(s) with a host, or mailto: one address. Anything else is 422. */
export type LinkTarget = { type: 'uri'; uri: string } | { type: 'page'; page_uid: string; y_pt?: number | null };

export interface LinkAddOp {
  id: string;
  kind: 'link.add';
  page_uid: string;
  rect: Rect;
  target: LinkTarget;
}

/** `link_id` from `documentApi.pageLinks` (`lk<xref>`). */
export interface LinkUpdateOp {
  id: string;
  kind: 'link.update';
  page_uid: string;
  link_id: string;
  rect: Rect;
  target: LinkTarget;
}

export interface LinkRemoveOp {
  id: string;
  kind: 'link.remove';
  page_uid: string;
  link_id: string;
}

/** The B1 (text) ops -- the name B1 code uses; unchanged. */
export type ContentOp = TextReplaceOp | TextDeleteOp;
/** B2: every op a draft may carry (`DraftPutBody.ops`). */
export type DocumentOp =
  | TextReplaceOp
  | TextDeleteOp
  | RedactOp
  | ImageRemoveOp
  | PageInsertOp
  | PageDeleteOp
  | PageMoveOp
  | PageRotateOp
  | LinkAddOp
  | LinkUpdateOp
  | LinkRemoveOp;
export type OpKind = DocumentOp['kind'];
/** Narrowing helper: a text op (has `target`). */
export const isTextOp = (op: DocumentOp): op is ContentOp =>
  op.kind === 'text.replace' || op.kind === 'text.delete';

export type Layer = 'content' | 'structure' | 'markup-content' | 'markup';
/** Server-derived; shown for UI decisions only (never sent). `content` ops
 * re-open review and (with signatures) need `discard_signatures`;
 * `structure` ops make a version but keep review and signatures. */
export const OP_LAYERS: Record<OpKind, Layer> = {
  'text.replace': 'content',
  'text.delete': 'content',
  redact: 'content',
  'image.remove': 'content',
  'page.insert': 'content',
  'page.delete': 'content',
  'page.move': 'content',
  'page.rotate': 'structure',
  'link.add': 'structure',
  'link.update': 'structure',
  'link.remove': 'structure',
};

export interface PageImageOut {
  xref: number;
  bbox: Rect;
}

export interface PageLinkOut {
  link_id: string;
  rect: Rect;
  target:
    | { type: 'uri'; uri: string }
    | { type: 'page'; page_uid: string | null; position: number | null }
    | { type: 'other' };
}
/** Markup kinds whose change re-opens review (§11 Q1). */
export const MARKUP_CONTENT_KINDS: ReadonlySet<string> = new Set([
  'textbox', 'whiteout', 'stamp', 'image', 'image2', 'strikeout', 'text', 'date',
]);

// -- markup -----------------------------------------------------------------------

/** A legacy annotation element (v4-v6 wire, `lib/annotations.ts`
 * `AnnotationRequest`) with `page_uid` INSTEAD of `page` -- a `page` key is a 422.
 * Stored and returned exactly as sent. */
export type MarkupElement = { id: string; kind: string; page_uid: string } & Record<string, unknown>;

export interface MarkupOut {
  revision: number;
  elements: MarkupElement[];
}

export interface MarkupSavedOut {
  revision: number;
  /** True when a review-relevant element changed on a contract past review. */
  review_reset: boolean;
}

// -- document / versions / pages -------------------------------------------------

export interface VersionOut {
  version_no: number;
  kind: 'original' | 'edit' | 'submission';
  file_hash: string;
  page_count: number;
  content_changed: boolean;
  review_reset: boolean;
  created_by_name: string | null;
  created_at: string;
  /** Counts only: `{total, by_kind, by_layer}` ({} for the original). */
  op_summary: { total?: number; by_kind?: Record<string, number>; by_layer?: Record<string, number> };
}

export interface DraftSummary {
  owner_name: string | null;
  is_mine: boolean;
  lease_expires_at: string;
  revision: number;
}

export interface Capabilities {
  can_edit: boolean;
  can_edit_markup: boolean;
  /** 'not_permitted' | 'contract_completed' | 'contract_busy' | null */
  reason: string | null;
}

export interface DocumentOut {
  document_id: string;
  current_version: VersionOut;
  versions_count: number;
  markup_revision: number;
  draft: DraftSummary | null;
  capabilities: Capabilities;
  warnings: Array<Record<string, unknown>>;
}

/** `ContractDetail.document` (GET /api/contracts/{id}); null = no editor document yet. */
export interface DocumentSummaryOut {
  document_id: string;
  current_version_no: number;
  versions_count: number;
  draft_active: boolean;
}

export interface PageOut {
  page_uid: string;
  position: number;
  /** Page N of the original upload (= template page N); null for an inserted page. */
  origin_page: number | null;
  rotation: number;
  width_pt: number;
  height_pt: number;
  has_text: boolean;
  scanned: boolean;
}

/** [char, x0, y0, x1, y1] */
export type TextChar = [string, number, number, number, number];

export interface TextSpanOut {
  font: string; // original font NAME (subset prefix stripped)
  family: FontFamilyId; // the substitute new text in this span is set in
  size_pt: number;
  color: Hex;
  bold: boolean;
  italic: boolean;
  baseline: number;
  /** True: a space the server put between two joined pieces (B1.1). */
  synthetic?: boolean;
  /** B2: the substitution map's family (Calibri -> Carlito, Arial -> Arimo). */
  substitute_family?: EditorFontFamilyId | null;
  chars: TextChar[];
}

export interface TextLineOut {
  id: string;
  bbox: Rect;
  text: string;
  spans: TextSpanOut[];
}

export interface TextBlockOut {
  id: string;
  bbox: Rect;
  editable: boolean;
  /** 'scanned_page' | 'type3_font' | 'rtl_text' | 'vertical_or_rotated_text' | null */
  reason: string | null;
  lines: TextLineOut[];
}

export interface TextModelOut {
  page_uid: string;
  version_no: number;
  file_hash: string;
  width_pt: number;
  height_pt: number;
  rotation: number;
  has_text: boolean;
  scanned: boolean;
  blocks: TextBlockOut[];
}

// -- drafts ------------------------------------------------------------------------

export interface DraftStartBody {
  base_version_no: number;
  client_session_id: string;
  take_over?: boolean;
}

export interface DraftOut {
  revision: number;
  base_version_no: number;
  /** F2: any `DocumentOp` (the B1 text ops are a subset). */
  ops: DocumentOp[];
  markup: MarkupElement[] | null;
  lease_expires_at: string;
  is_mine: boolean;
  owner_name: string | null;
}

export interface DraftPutBody {
  revision: number;
  client_session_id: string;
  /** B2: may carry any `DocumentOp` (`ContentOp[]` is assignable). */
  ops: DocumentOp[];
  /** null/absent = the draft carries no markup (the document's stays as saved). */
  markup?: MarkupElement[] | null;
}

export interface EngineWarning {
  code: string; // 'text_overflow' | 'text_shrunk' | 'font_substituted' | 'neighbor_glyphs_redrawn' | 'pdf_repaired' | 'replayed' | ...
  op_id?: string;
  [key: string]: unknown;
}

export interface DraftSavedOut {
  revision: number;
  saved_at: string;
  lease_expires_at: string;
  warnings: EngineWarning[];
}

export interface CommitBody {
  revision: number;
  commit_request_id: string;
  discard_signatures?: boolean;
}

export interface CommitOut {
  version: VersionOut;
  content_changed: boolean;
  review_reset: boolean;
  reparse_started: boolean;
  remapped: { signatures_kept: number; signatures_removed: number };
  warnings: EngineWarning[];
}

// -- errors ------------------------------------------------------------------------

/** `ApiError.code` values of the editor endpoints ({error, message, details}). */
export type DocumentErrorCode =
  | 'revision_conflict' // details.current_revision
  | 'draft_locked' // details.owner_name, details.lease_expires_at
  | 'draft_taken_over'
  | 'draft_not_found'
  | 'draft_active' // markup PUT while a draft is leased
  | 'base_outdated' // details.current_version_no (on POST draft)
  | 'signatures_present' // details.count -> resend commit with discard_signatures
  | 'stale_target' // details.op_id
  | 'target_not_found'
  | 'contract_completed'
  | 'contract_busy'
  | 'invalid_ops' // details.ops: [{id, reason}]
  | 'invalid_markup' // details.elements: [{id, reason}]
  | 'text_overflow' // details.op_id, lines_over, needed_h_pt, reason?: 'collision'
  | 'undrawable_character' // details.op_id, run, position, codepoint, font
  | 'scanned_page_not_editable'
  | 'unsupported_span'
  | 'duplicate_target'
  // QA 2026-09-30: a ✥ Move the engine could not carry out is refused, never dropped.
  | 'text_move_not_applied' // details.op_id, dx, dy
  // 2026-09-30: a glyph of the replaced/deleted text was still on the page after
  // the redaction -- refused so the old text never shows next to the new one.
  | 'old_text_not_removed' // details.op_id, glyphs
  | 'unknown_page'
  | 'pdf_encrypted'
  | 'pdf_corrupt'
  | 'version_not_found'
  | 'commit_request_id_reused'
  | 'payload_too_large'
  // B2
  | 'op_outside_page' // details.op_id
  | 'duplicate_page_uid'
  | 'cannot_delete_last_page'
  | 'unknown_link' // 409, details.op_id
  | 'page_has_slots' // 403, details.pages (template pages) -- reviewer/admin only
  | 'export_not_ready'; // 409, details.status

export function isDocumentError(cause: unknown, code: DocumentErrorCode): cause is ApiError {
  return cause instanceof ApiError && cause.code === code;
}

/** 404 on GET /document (server without v2) or 422 pdf_encrypted/pdf_corrupt:
 * fall back to the legacy `/file` viewer. */
export function isEditorUnavailable(cause: unknown): boolean {
  return (
    cause instanceof ApiError &&
    (cause.status === 404 || cause.code === 'pdf_encrypted' || cause.code === 'pdf_corrupt')
  );
}

// -- calls -------------------------------------------------------------------------

const base = (contractId: string) => `/api/contracts/${contractId}/document`;

export const documentApi = {
  get: (contractId: string) => api.get<DocumentOut>(base(contractId)),
  versions: (contractId: string) => api.get<VersionOut[]>(`${base(contractId)}/versions`),
  versionFile: (contractId: string, versionNo: number) =>
    fetchPdf(`${base(contractId)}/versions/${versionNo}/file`),
  pages: (contractId: string, versionNo: number) =>
    api.get<PageOut[]>(`${base(contractId)}/versions/${versionNo}/pages`),
  pageText: (contractId: string, versionNo: number, pageUid: string, signal?: AbortSignal) =>
    api.get<TextModelOut>(`${base(contractId)}/versions/${versionNo}/pages/${pageUid}/text`, signal),

  markup: (contractId: string) => api.get<MarkupOut>(`${base(contractId)}/markup`),
  saveMarkup: (contractId: string, revision: number, elements: MarkupElement[]) =>
    api.put<MarkupSavedOut>(`${base(contractId)}/markup`, { revision, elements }),

  startDraft: (contractId: string, body: DraftStartBody) =>
    api.post<DraftOut>(`${base(contractId)}/draft`, body),
  draft: (contractId: string) => api.get<DraftOut>(`${base(contractId)}/draft`),
  saveDraft: (contractId: string, body: DraftPutBody) =>
    api.put<DraftSavedOut>(`${base(contractId)}/draft`, body),
  renewLease: (contractId: string, revision: number, clientSessionId?: string) =>
    api.post<{ lease_expires_at: string }>(`${base(contractId)}/draft/lease`, {
      revision,
      ...(clientSessionId ? { client_session_id: clientSessionId } : {}),
    }),
  discardDraft: (contractId: string, revision: number) =>
    api.del<void>(`${base(contractId)}/draft?revision=${revision}`),
  /** One-page PDF: base page + the draft's ops of that page, `hide` block's glyphs removed. */
  pagePreview: (contractId: string, pageUid: string, revision: number, hideBlockId?: string) =>
    fetchPdf(
      `${base(contractId)}/draft/pages/${pageUid}/preview?revision=${revision}` +
        (hideBlockId ? `&hide=${encodeURIComponent(hideBlockId)}` : ''),
    ),
  commit: (contractId: string, body: CommitBody) =>
    api.post<CommitOut>(`${base(contractId)}/draft/commit`, body),

  // -- B2 --
  pageImages: (contractId: string, versionNo: number, pageUid: string) =>
    api.get<PageImageOut[]>(`${base(contractId)}/versions/${versionNo}/pages/${pageUid}/images`),
  pageLinks: (contractId: string, versionNo: number, pageUid: string) =>
    api.get<PageLinkOut[]>(`${base(contractId)}/versions/${versionNo}/pages/${pageUid}/links`),
  /** The WHOLE draft (every op applied) -- the pages panel after page ops.
   * The response header `X-Page-Uids` lists the page uids in order. */
  draftPdf: (contractId: string, revision: number) =>
    fetchPdf(`${base(contractId)}/draft/preview.pdf?revision=${revision}`),
  createExport: (contractId: string, body: ExportBody = {}) =>
    api.post<ExportJobOut>(`${base(contractId)}/exports`, body),
  export: (contractId: string, exportId: string) =>
    api.get<ExportJobOut>(`${base(contractId)}/exports/${exportId}`),
  exportFile: (contractId: string, exportId: string) =>
    fetchPdf(`${base(contractId)}/exports/${exportId}/file`),
  cancelExport: (contractId: string, exportId: string) =>
    api.del<void>(`${base(contractId)}/exports/${exportId}`),
};

// -- exports (B2) -----------------------------------------------------------------

export interface ExportBody {
  version_no?: number; // absent = current
  /** Default true: ALL saved markup, hidden-on-screen elements included. */
  include_markup?: boolean;
  flatten_forms?: boolean; // B3; no-op until then
}

export interface ExportJobOut {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  version_no: number;
  include_markup: boolean;
  flatten_forms: boolean;
  result_sha256: string | null;
  result_bytes: number | null;
  error: string | null;
  warnings: EngineWarning[];
  created_at: string;
  finished_at: string | null;
}

// -- gallery (B2): /api/assets ---------------------------------------------------------

export interface AssetOut {
  asset_id: string; // 32 hex
  kind: 'image' | 'stamp';
  label: string | null;
  width_px: number;
  height_px: number;
  byte_size: number;
  sha256: string;
  created_at: string;
}

export const assetsApi = {
  list: () => api.get<AssetOut[]>('/api/assets'),
  /** `image_data`: PNG/JPEG data URL or base64 (multipart `file` also works). */
  add: (body: { image_data: string; kind?: 'image' | 'stamp'; label?: string }) =>
    api.post<AssetOut>('/api/assets', body),
  imageUrl: (assetId: string) => `/api/assets/${assetId}/image`,
  remove: (assetId: string) => api.del<void>(`/api/assets/${assetId}`),
  /** COPY a gallery image onto a contract -> an ordinary `image`/`image2` asset id. */
  placeOnContract: (contractId: string, assetId: string) =>
    api.post<{ asset_id: string; width_px: number; height_px: number }>(
      `/api/contracts/${contractId}/annotation-assets`,
      { from_asset_id: assetId },
    ),
};

// -- fonts (B2): /api/fonts ------------------------------------------------------------

export interface FontOut {
  family: string;
  label: string;
  styles: string[]; // 'regular' | 'bold' | 'italic' | 'bold_italic'
  license: string; // 'OFL-1.1' | 'Bitstream-Vera' | 'base14'
  substitutes_for: string[]; // e.g. ['Calibri'] -> "Calibri -> Carlito (substitute)"
  scripts: string[]; // 'latin' | 'cyrillic' | 'greek'
  embedded: boolean;
  category: 'text' | 'signature';
}

export const fontsApi = {
  list: (category: 'text' | 'signature' = 'text') => api.get<FontOut[]>(`/api/fonts?category=${category}`),
};

// -- typed signatures (B2): POST /api/signatures {typed} ------------------------------------

export type TypedSignatureFont = 'GreatVibes' | 'Allura' | 'Sacramento';
export const TYPED_SIGNATURE_MAX_CHARS = 64;
export interface TypedSignatureBody {
  label?: string;
  is_default?: boolean;
  typed: { text: string; font: TypedSignatureFont };
}

// -- B3: interactive form fields ---------------------------------------------------------
// Additive: the B1/B2 types above are unchanged. A draft's `ops` may carry
// `FieldOp`s next to `DocumentOp`s (use `DocumentOpB3` for such a list).

export const FIELD_NAME_RE = /^[A-Za-z0-9_.\-]{1,64}$/;
export const EXPORT_VALUE_RE = /^[A-Za-z0-9_.\-]{1,64}$/;
export const MAX_FIELD_OPTIONS = 200;
export const MAX_OPTION_CHARS = 128;
export const MAX_TOOLTIP_CHARS = 256;
export const MAX_FIELD_VALUE_CHARS = 5000;
export const FIELD_SIZE_MIN_PT = 4;
export const FIELD_SIZE_MAX_PT = 72;
export const MAX_TAB_INDEX = 100000;
export const MAX_DETECT_PAGES = 100;

export type FieldType = 'text' | 'multiline' | 'dropdown' | 'radio' | 'checkbox' | 'signature';

/** One widget; a radio GROUP = several FieldDefs with the same `name`, each its
 * own `export_value`. Rules (422 otherwise):
 * - `default`: text/multiline/dropdown -> string (dropdown: one of `options`);
 *   checkbox/radio -> boolean (radio: THIS button is the default); signature -> absent;
 * - `options`: dropdown only, required there (unique, 1..200, each 1..128 chars);
 * - `export_value`: radio (required, unique in the group) / checkbox (optional, default "Yes");
 * - `max_len`: text/multiline only; `font`/`size_pt`: text/multiline/dropdown (ignored on
 *   the other types). Widgets are drawn in the metric-compatible standard face
 *   (Sans -> Helvetica, Serif -> Times, Mono -> Courier); `size_pt` absent = auto;
 * - values are WinAnsi (cp1252) only -> 422 `undrawable_character`. */
export interface FieldDef {
  name: string; // FIELD_NAME_RE
  type: FieldType;
  rect: Rect; // pt, top-left, UNROTATED page space
  required?: boolean;
  readonly?: boolean;
  tooltip?: string | null;
  default?: string | boolean | null;
  options?: string[] | null;
  export_value?: string | null;
  font?: EditorFontFamilyId | null;
  size_pt?: number | null;
  max_len?: number | null;
  tab_index: number; // 0..100000, lower first
}

/** LAYER structure. Name in use -> 409 `field_name_taken`, except a radio button
 * joining the radio group of that name (its export_value new: else 409 `radio_value_taken`). */
export interface FieldAddOp {
  id: string;
  kind: 'field.add';
  page_uid: string;
  field: FieldDef;
}

/** LAYER structure. Replaces the definition (and position: `page_uid` + `field.rect`).
 * `name` = CURRENT name (absent = `field.name`, i.e. no rename); `export_value` picks the
 * radio button (required when the group has >1 button). The value is kept when still valid. */
export interface FieldUpdateOp {
  id: string;
  kind: 'field.update';
  page_uid: string;
  name?: string | null;
  export_value?: string | null;
  field: FieldDef;
}

/** LAYER structure. With `export_value`: only that radio button. */
export interface FieldRemoveOp {
  id: string;
  kind: 'field.remove';
  name: string;
  export_value?: string | null;
}

/** LAYER content (re-opens a past review; with signatures -> 409 `signatures_present`).
 * text/multiline: string (multiline may hold "\n"); dropdown: an option or "" (clear);
 * checkbox: boolean; radio: a button's export_value or "" (none). Signature -> 422
 * `field_not_fillable`; read-only -> 422 `field_readonly`. */
export interface FieldValueOp {
  id: string;
  kind: 'field.value';
  name: string;
  value: string | boolean | string[];
}

export type FieldOp = FieldAddOp | FieldUpdateOp | FieldRemoveOp | FieldValueOp;
export type FieldOpKind = FieldOp['kind'];
/** Every op a B3 draft may carry. */
export type DocumentOpB3 = DocumentOp | FieldOp;
export const isFieldOp = (op: DocumentOpB3): op is FieldOp => op.kind.startsWith('field.');
export const FIELD_OP_LAYERS: Record<FieldOpKind, Layer> = {
  'field.add': 'structure',
  'field.update': 'structure',
  'field.remove': 'structure',
  'field.value': 'content',
};

/** `GET .../versions/{no}/fields`: one row per WIDGET (a radio group = one row per
 * button, same name), in tab order. `source: 'original'` = an AcroForm field the upload
 * already had (every Docusign package has a hidden zero-size envelope signature field:
 * `hidden: true` -- do not draw it). `type: 'other'` = push button / list box. */
export interface FieldOut {
  name: string;
  type: FieldType | 'other';
  page_uid: string;
  rect: Rect;
  required: boolean;
  readonly: boolean;
  tooltip: string | null;
  default: string | boolean | null;
  options: string[] | null;
  export_value: string | null;
  font: string | null;
  size_pt: number | null;
  max_len: number | null;
  tab_index: number | null; // null: an original field the editor never ordered
  /** text/multiline/dropdown: string ("" empty); checkbox: boolean; radio: the group's
   * chosen export_value ("" none); signature: null. */
  value: string | boolean | string[] | null;
  source: 'editor' | 'original';
  hidden: boolean;
}

/** `DraftPutBody` whose ops may include field ops (same endpoint). */
export interface DraftPutBodyB3 extends Omit<DraftPutBody, 'ops'> {
  ops: DocumentOpB3[];
}

export interface DetectFieldsBody {
  page_uids?: string[]; // absent = every page of the CURRENT version (max 100)
}

export interface FieldSuggestionOut {
  suggestion_id: string; // stable for the same page/type/rect
  page_uid: string;
  type: FieldType;
  rect: Rect;
  label: string;
  name: string; // a free, valid field name
  confidence: number; // 0..1
}

export interface DetectFieldsOut {
  version_no: number;
  suggestions: FieldSuggestionOut[];
}

/** `Capabilities` + B3 `can_fill`: may start a draft that only FILLS fields
 * (every editor, and the contractor who sees the contract by e-mail). A fill-only
 * user's draft may hold `field.value` ops only (else 403 `fill_only`), and never
 * `discard_signatures`. `DocumentOut.capabilities` carries it. */
export interface CapabilitiesB3 extends Capabilities {
  can_fill: boolean;
}

export type FormErrorCode =
  | 'field_name_taken' // 409, details.op_id
  | 'radio_value_taken' // 409, details.op_id
  | 'unknown_field' // 409, details.op_id
  | 'invalid_field_value' // 422, details.op_id, details.reason:
  //   expected_text | newline_in_single_line | too_long | expected_option | not_an_option |
  //   expected_boolean | expected_export_value | not_a_button_of_the_group
  | 'field_readonly' // 422, details.op_id
  | 'field_not_fillable' // 422 (signature), details.op_id
  | 'unsupported_field' // 422 (push button), details.op_id
  | 'fill_only'; // 403, details.ops: [{id, reason: 'fill_only'}]

export function isFormError(cause: unknown, code: FormErrorCode): cause is ApiError {
  return cause instanceof ApiError && cause.code === code;
}

export const formsApi = {
  fields: (contractId: string, versionNo: number) =>
    api.get<FieldOut[]>(`${base(contractId)}/versions/${versionNo}/fields`),
  detect: (contractId: string, body: DetectFieldsBody = {}) =>
    api.post<DetectFieldsOut>(`${base(contractId)}/detect-fields`, body),
  /** `documentApi.saveDraft` typed for ops that include field ops. */
  saveDraft: (contractId: string, body: DraftPutBodyB3) =>
    api.put<DraftSavedOut>(`${base(contractId)}/draft`, body),
};
