/*
 * The contract the editor works on, and the server calls that read it and save
 * a new version of it. Every call sends the signed-in user's token (`csa.token`
 * in localStorage, the same one the app sends), so there is no second sign-in.
 */

/** A failed server call. `code` is the server's machine code (`base_outdated`, ...). */
class EditorApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.name = 'EditorApiError';
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
}

async function editorRequest(path, options) {
  let token = null;
  try {
    token = window.localStorage.getItem('csa.token');
  } catch (error) {
    token = null;
  }
  const headers = Object.assign(
    {},
    token ? { Authorization: `Bearer ${token}` } : {},
    (options && options.headers) || {},
  );
  let response;
  try {
    response = await fetch(path, Object.assign({}, options, { headers }));
  } catch (error) {
    throw new EditorApiError(0, 'network', 'Serverga ulanib bo‘lmadi. Internetni tekshirib, qayta urinib ko‘ring.', {});
  }
  if (response.ok) return response;
  if (response.status === 401) {
    throw new EditorApiError(401, 'unauthorized', 'Sessiya tugagan. Ilovaga qayta kiring, so‘ng bu sahifani yangilang.', {});
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  throw new EditorApiError(
    response.status,
    (body && body.error) || 'http_error',
    (body && body.message) || `Server xatosi (${response.status}).`,
    (body && body.details) || {},
  );
}

/** The contract's CURRENT version and its file: the one reviewers and signers see. */
async function loadContractVersion(contractId) {
  const state = await (await editorRequest(`/api/contracts/${contractId}/document`)).json();
  const versionNo = state.current_version.version_no;
  const file = await editorRequest(`/api/contracts/${contractId}/document/versions/${versionNo}/file`);
  return { versionNo, bytes: await file.arrayBuffer() };
}

/** A request id for one save: 8 to 64 letters, digits, `-` or `_`, as the server requires. */
function newCommitRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Saves an edited PDF as the contract's next version. Resolves with the server's answer. */
async function saveContractVersion(contractId, baseVersionNo, pdfBytes, commitRequestId, discardSignatures) {
  const form = new FormData();
  form.append('file', new Blob([pdfBytes], { type: 'application/pdf' }), 'edited.pdf');
  form.append('base_version_no', String(baseVersionNo));
  form.append('commit_request_id', commitRequestId);
  form.append('discard_signatures', discardSignatures ? 'true' : 'false');
  const response = await editorRequest(`/api/contracts/${contractId}/document/upload`, {
    method: 'POST',
    body: form,
  });
  return response.json();
}

/** What a successful save says on the page. */
function describeSavedVersion(saved) {
  const unchanged = (saved.warnings || []).some((warning) => warning.code === 'unchanged');
  const parts = [
    unchanged
      ? `Fayl o‘zgarmagan; versiya ${saved.version.version_no} qoldi.`
      : `Saqlandi: versiya ${saved.version.version_no}.`,
  ];
  if (saved.remapped && saved.remapped.signatures_removed) {
    parts.push(`Imzolar o‘chirildi: ${saved.remapped.signatures_removed}.`);
  }
  if (saved.review_reset) parts.push('Shartnoma qayta ko‘rib chiqishga yuborildi.');
  return parts.join(' ');
}

/** What a failed save says on the page. */
function describeSaveFailure(error) {
  if (!(error instanceof EditorApiError)) return 'Saqlab bo‘lmadi. Qayta urinib ko‘ring.';
  if (error.status === 403) return 'Bu hujjatni tahrirlash huquqingiz yo‘q.';
  switch (error.code) {
    case 'base_outdated':
      return 'Shartnomaning yangi versiyasi bor. Sahifani yangilang va o‘zgarishlarni qayta kiriting.';
    case 'draft_locked':
      return 'Hujjat hozir ish oynasida tahrirlanmoqda. Keyinroq qayta urinib ko‘ring.';
    case 'contract_busy':
      return 'Shartnoma hozir tahlil qilinmoqda. Tugagach qayta urinib ko‘ring.';
    case 'contract_completed':
      return 'Shartnoma yakunlangan; hujjatni o‘zgartirib bo‘lmaydi.';
    case 'page_count_changed':
      return 'Sahifalar soni o‘zgargan. Barcha sahifalari bilan saqlang.';
    default:
      return error.message;
  }
}

/** True when the editor runs inside the contract page (an iframe), not on its own. */
function editorIsEmbedded() {
  return new URLSearchParams(window.location.search).get('embedded') === '1' && window.parent !== window;
}

/** Tells the contract page what happened: `editor:close` or `editor:saved`. */
function notifyContractPage(type) {
  if (editorIsEmbedded()) window.parent.postMessage({ type }, window.location.origin);
}

/* The contract page asks for a page (an issue in the review was clicked). */
window.addEventListener('message', (event) => {
  if (event.origin !== window.location.origin || !event.data || event.data.type !== 'editor:goto') return;
  const bar = document.querySelectorAll('.page-bar')[Number(event.data.page) - 1];
  if (bar) bar.scrollIntoView({ block: 'start' });
});
