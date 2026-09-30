import { api, ApiError } from './client';
import type { DraftOut, FieldOut, Rect } from './document';

/**
 * Share-for-fill links (EDITOR-V2-PLAN §8, F3) -- LAN only, fill only.
 *
 * Mirror of `backend/app/schemas/share.py`.
 *
 * INTERNAL (logged-in editor: admin, reviewer or uploader):
 *   POST   /api/contracts/{cid}/share-links            -> ShareLinkCreatedOut (token ONCE)
 *   GET    /api/contracts/{cid}/share-links            -> ShareLinkOut[]
 *   DELETE /api/contracts/{cid}/share-links/{id}       -> 204 (idempotent)
 *   GET    /api/contracts/{cid}/submissions[?status=]  -> SubmissionOut[]
 *   POST   /api/contracts/{cid}/submissions/{id}/accept {client_session_id} -> AcceptOut
 *   POST   /api/contracts/{cid}/submissions/{id}/reject {reason}            -> SubmissionOut
 *
 * PUBLIC (no account; page `/s#t=<token>`): the token is read from
 * `location.hash` and sent ONLY in the `X-Share-Token` header -- never in a
 * URL, never in localStorage. Clear it from the address bar after reading
 * (`history.replaceState`). Every refusal is the same 404; 429 after too many
 * failures from one address.
 *   GET  /api/share/session      -> ShareSessionOut
 *   GET  /api/share/file         -> the pinned version's PDF
 *   POST /api/share/submissions  {values, submitter_name?, submitter_email?} -> 201
 *
 * Accept puts the values into a DRAFT owned by the accepting user under the
 * given `client_session_id`: resume it with `POST .../document/draft`
 * `{base_version_no: draft.base_version_no, client_session_id}` (200, ops
 * included), then Save (commit) as usual -- a `field.value` commit re-opens a
 * past review and needs `discard_signatures` when the contract is signed.
 */

export const MAX_SHARED_FIELDS = 200;
export const MAX_SUBMISSIONS_PER_LINK = 100;
export const SHARE_DEFAULT_TTL_HOURS = 72;
export const SHARE_MAX_TTL_HOURS = 14 * 24;
export const MAX_REJECT_REASON = 1000;
export const SHARE_TOKEN_RE = /^[A-Za-z0-9_-]{32,128}$/;

export type ShareStatus = 'active' | 'expired' | 'revoked' | 'used_up';
export type SubmissionStatus = 'received' | 'accepted' | 'rejected';
export type FieldValue = boolean | string | string[];
export type SharedFieldType = 'text' | 'multiline' | 'dropdown' | 'radio' | 'checkbox';

// -- internal -----------------------------------------------------------------

export interface ShareLinkCreateBody {
  allowed_fields: string[]; // names from GET .../document/versions/{current}/fields
  recipient_label?: string | null; // <= 120
  expires_in_hours?: number | null; // 1..336, default 72
  max_submissions?: number; // 1..100, default 1
}

export interface ShareLinkOut {
  id: string;
  version_no: number;
  allowed_fields: string[];
  recipient_label: string | null;
  expires_at: string;
  max_submissions: number;
  submission_count: number;
  submissions_pending: number;
  status: ShareStatus;
  revoked_at: string | null;
  last_used_at: string | null;
  created_by_name: string | null;
  created_at: string;
}

/** Only in the create response. Show `url` once (copy button); never store it. */
export interface ShareLinkCreatedOut extends ShareLinkOut {
  token: string;
  url: string; // `${share_base_url}/s#t=${token}`
}

export interface SubmissionOut {
  id: string;
  share_link_id: string;
  recipient_label: string | null;
  version_no: number | null;
  status: SubmissionStatus;
  submitter_name: string | null;
  submitter_email: string | null;
  field_count: number;
  /** Sensitive names (ssn, dob, bank...) masked; null when unreadable. */
  values: Record<string, FieldValue> | null;
  values_readable: boolean;
  ip_address: string | null;
  created_at: string;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
}

export interface AcceptOut {
  submission: SubmissionOut;
  draft: DraftOut;
}

/**
 * Error codes (`ApiError.code`):
 *  create: 422 `fields_not_shareable` {fields:[{index, reason: unknown_field|field_not_fillable|
 *          field_readonly|field_hidden}]}, 422 `expiry_too_long` {max_hours}, 409 `contract_completed`
 *  accept: 409 `submission_not_pending` {status}, 409 `draft_locked` {owner_name, lease_expires_at,
 *          is_mine, lease_active}, 409 `submission_unreadable`, 409 `contract_completed` / `contract_busy`,
 *          422 `invalid_ops` {fields:[{name, reason}]} (the values no longer fit the current version)
 *  reject: 409 `submission_not_pending`
 */
const contractBase = (contractId: string) => `/api/contracts/${contractId}`;

export const shareApi = {
  create: (contractId: string, body: ShareLinkCreateBody) =>
    api.post<ShareLinkCreatedOut>(`${contractBase(contractId)}/share-links`, body),
  list: (contractId: string) => api.get<ShareLinkOut[]>(`${contractBase(contractId)}/share-links`),
  revoke: (contractId: string, linkId: string) =>
    api.del<void>(`${contractBase(contractId)}/share-links/${linkId}`),
  submissions: (contractId: string, status?: SubmissionStatus) =>
    api.get<SubmissionOut[]>(
      `${contractBase(contractId)}/submissions${status ? `?status=${status}` : ''}`,
    ),
  accept: (contractId: string, submissionId: string, clientSessionId: string) =>
    api.post<AcceptOut>(`${contractBase(contractId)}/submissions/${submissionId}/accept`, {
      client_session_id: clientSessionId,
    }),
  reject: (contractId: string, submissionId: string, reason: string) =>
    api.post<SubmissionOut>(`${contractBase(contractId)}/submissions/${submissionId}/reject`, {
      reason,
    }),
};

// -- public -------------------------------------------------------------------

/** One widget the recipient may fill (radio group = one entry per button). */
export interface SharedFieldOut {
  name: string;
  type: SharedFieldType;
  page_uid: string;
  rect: Rect; // pt, top-left, unrotated page space
  required: boolean;
  tooltip: string | null;
  options: string[] | null; // dropdown
  export_value: string | null; // radio button / checkbox "on" value
  font: string | null;
  size_pt: number | null;
  max_len: number | null;
  tab_index: number | null;
  value: FieldValue | null; // value in the pinned version (prefill)
}

export interface SharedPageOut {
  page_uid: string;
  position: number; // 1-based page of GET /api/share/file
  width_pt: number;
  height_pt: number;
  rotation: number;
}

export interface ShareSessionOut {
  document_title: string;
  version_no: number;
  fields: SharedFieldOut[];
  pages: SharedPageOut[];
  expires_at: string;
  submissions_left: number;
}

/**
 * text/multiline/dropdown/radio: a string ("" = empty; radio = the chosen
 * export_value); checkbox: a boolean. Only allowed names.
 * 422 `invalid_submission` {fields:[{name, reason: required|invalid_field_value|
 * undrawable_character|field_readonly|...}], not_allowed: n}; 422 `empty_submission`.
 * Values must be WinAnsi (cp1252): Cyrillic etc. -> `undrawable_character`.
 */
export interface SubmissionBody {
  values: Record<string, FieldValue>;
  submitter_name?: string | null; // <= 120
  submitter_email?: string | null; // <= 254
}

export interface SubmissionReceivedOut {
  status: 'received';
  submissions_left: number;
}

/** The token from `#t=<token>` (null when absent / malformed). */
export function readShareToken(hash: string = window.location.hash): string | null {
  const match = /(?:^#|&)t=([^&]+)/.exec(hash);
  const token = match ? decodeURIComponent(match[1]) : null;
  return token && SHARE_TOKEN_RE.test(token) ? token : null;
}

async function publicRequest<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...(init.headers as Record<string, string>), 'X-Share-Token': token },
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
    cache: 'no-store',
  });
  const isJson = response.headers.get('content-type')?.includes('application/json');
  if (!response.ok) {
    const data = (isJson ? await response.json() : {}) as {
      error?: string;
      message?: string;
      details?: Record<string, unknown>;
    };
    throw new ApiError(response.status, data.error ?? 'http_error', data.message ?? `HTTP ${response.status}`, data.details ?? {});
  }
  return (isJson ? response.json() : response.arrayBuffer()) as Promise<T>;
}

// -- F3-UI helpers ---------------------------------------------------------------

// The field rows are B3's `FieldOut` (`formsApi.fields`, api/document.ts).
type DocumentFieldOut = Pick<FieldOut, 'type' | 'readonly' | 'hidden'>;

const SHAREABLE_TYPES: ReadonlySet<string> = new Set(['text', 'multiline', 'dropdown', 'radio', 'checkbox']);

/** Mirror of `services/share.shareable_problem`: why the widgets of ONE name cannot be shared (null = they can). */
export function shareableProblem(rows: readonly DocumentFieldOut[]): string | null {
  if (rows.length === 0) return 'unknown_field';
  if (rows.some((row) => !SHAREABLE_TYPES.has(row.type))) return 'field_not_fillable';
  if (rows.some((row) => row.readonly)) return 'field_readonly';
  if (rows.some((row) => row.hidden)) return 'field_hidden';
  return null;
}

export const SHARE_PROBLEM_TEXT: Record<string, string> = {
  unknown_field: 'no longer in the document',
  field_not_fillable: 'a signature or button field cannot be filled from a link',
  field_readonly: 'read-only',
  field_hidden: 'hidden',
};

/** Mirror of `core/pii.SENSITIVE_TOKENS`: the server masks these names' values. */
const SENSITIVE_TOKENS = [
  'ssn', 'social_security', 'ein', 'tax_id', 'date_of_birth', 'dob', 'birth', 'bank', 'routing',
  'account_number', 'passport',
];
export const isSensitiveName = (name: string): boolean => {
  const lowered = name.toLowerCase();
  return SENSITIVE_TOKENS.some((token) => lowered.includes(token));
};

// WinAnsi (cp1252) beyond Latin-1: what a form value may hold (B3 checks the glyphs).
const CP1252_EXTRA = new Set('€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ');
/** The first character a WinAnsi form cannot print (null = all printable). Newlines/tabs pass. */
export function firstNonWinAnsi(text: string): string | null {
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (char === '\n' || char === '\r' || char === '\t') continue;
    if ((code >= 0x20 && code <= 0x7e) || (code >= 0xa0 && code <= 0xff) || CP1252_EXTRA.has(char)) continue;
    return char;
  }
  return null;
}

export const sharePublicApi = {
  session: (token: string) => publicRequest<ShareSessionOut>(token, '/api/share/session'),
  file: (token: string) => publicRequest<ArrayBuffer>(token, '/api/share/file'),
  submit: (token: string, body: SubmissionBody) =>
    publicRequest<SubmissionReceivedOut>(token, '/api/share/submissions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
};
