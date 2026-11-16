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

/** A JSON call to the server. Resolves with the parsed body, or null for 204. */
async function editorJson(path, method, body) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const response = await editorRequest(path, options);
  return response.status === 204 ? null : response.json();
}

/** The contract's CURRENT version and its file: the one reviewers and signers see. */
async function loadContractVersion(contractId) {
  const state = await (await editorRequest(`/api/contracts/${contractId}/document`)).json();
  const versionNo = state.current_version.version_no;
  const download = async () => {
    const file = await editorRequest(`/api/contracts/${contractId}/document/versions/${versionNo}/file`);
    return file.arrayBuffer();
  };
  // Inside the contract page the file is downloaded once for both (src/lib/documentCache.ts).
  let shared = null;
  try {
    shared = editorIsEmbedded() ? window.parent.__swmDocumentCache : null;
  } catch (error) {
    shared = null;
  }
  if (!shared) return { versionNo, bytes: await download() };
  // A copy: pdf.js hands the buffer it is given to its worker.
  return { versionNo, bytes: new Uint8Array(await shared.get(contractId, versionNo, download)).slice().buffer };
}

/** A request id for one save: 8 to 64 letters, digits, `-` or `_`, as the server requires. */
function newCommitRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
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
      return 'Sahifalar soni o‘zgargan. Sahifani yangilab qayta urinib ko‘ring.';
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

/*
 * The page list on the left: one small picture per page, drawn from pdf.js.
 * A click moves the editor to that page.
 */
async function buildPageList(engine) {
  const list = document.getElementById('page-list');
  if (!list || !engine.pdfDoc) return;
  list.innerHTML = '';
  for (let pageNo = 1; pageNo <= engine.pdfDoc.numPages; pageNo++) {
    const page = await engine.pdfDoc.getPage(pageNo);
    const viewport = page.getViewport({ scale: 0.2 });
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
    const button = document.createElement('button');
    button.type = 'button';
    button.title = `Sahifa ${pageNo}`;
    button.append(canvas);
    const label = document.createElement('span');
    label.textContent = String(pageNo);
    button.append(label);
    button.addEventListener('click', () => {
      const bar = document.querySelectorAll('.page-bar')[pageNo - 1];
      if (bar) bar.scrollIntoView({ block: 'start', behavior: 'smooth' });
      list.querySelectorAll('button').forEach((item) => item.classList.toggle('active', item === button));
    });
    list.append(button);
  }
}

/* The text lines of one page's text model, flattened over its blocks. */
function textLinesOf(model) {
  const lines = [];
  for (const block of model.blocks || []) {
    for (const line of block.lines || []) {
      lines.push({
        id: line.id,
        blockId: block.id,
        editable: block.editable,
        reason: block.reason,
        text: line.text,
        bbox: line.bbox,
        spans: line.spans,
      });
    }
  }
  return lines;
}

/* Removes a draft this save created, after the save failed. Best effort: a leftover draft is replaced by the next save. */
async function discardDraftQuietly(contractId, revision) {
  try {
    await editorRequest(`/api/contracts/${contractId}/document/draft?revision=${revision}`, { method: 'DELETE' });
  } catch (error) {
    // Nothing more to do here.
  }
}

/* A refused operation is named by its page and text, so the person knows which edit to change. */
function explainSaveError(error, labels) {
  const opId = error instanceof EditorApiError ? error.details.op_id : null;
  const label = opId ? labels.find((item) => item.id === opId) : null;
  if (!label) return error;
  return new EditorApiError(
    error.status,
    error.code,
    `Sahifa ${label.page}: «${label.text}» — ${error.message}`,
    error.details,
  );
}

/**
 * Saves the edits as the contract's next version. The server rewrites only the
 * edited lines (a draft of text operations, then a commit); the rest of the
 * document stays as it was. Resolves with one of:
 *   { status: 'saved', version }     the commit's answer (CommitOut)
 *   { status: 'blocked', messages }  nothing was saved; the messages say why
 *   { status: 'nothing' }            the text did not change
 *   { status: 'cancelled' }          the person did not confirm removing the signatures
 */
async function saveEditsToContract(contractId, baseVersionNo, edits, options) {
  const base = `/api/contracts/${contractId}/document`;
  const positions = await editorJson(`${base}/versions/${baseVersionNo}/pages`, 'GET');
  const pages = [];
  for (const pageIndex of [...new Set(edits.map((edit) => edit.pageIndex))]) {
    const info = positions.find((page) => page.position === pageIndex + 1);
    if (!info) {
      return { status: 'blocked', messages: [`Sahifa ${pageIndex + 1} topilmadi. Sahifani yangilang.`] };
    }
    const model = await editorJson(`${base}/versions/${baseVersionNo}/pages/${info.page_uid}/text`, 'GET');
    pages.push({ pageIndex, pageUid: info.page_uid, widthPt: model.width_pt, lines: textLinesOf(model) });
  }

  const built = window.TextOps.buildTextOps({ pages, edits });
  if (built.problems.length > 0) {
    return { status: 'blocked', messages: built.problems.map(window.TextOps.describeProblem) };
  }
  if (built.ops.length === 0) return { status: 'nothing' };

  const clientSessionId = newCommitRequestId();
  const draft = await editorJson(`${base}/draft`, 'POST', {
    base_version_no: baseVersionNo,
    client_session_id: clientSessionId,
  });
  if (!draft.is_mine) {
    throw new EditorApiError(
      409,
      'draft_locked',
      `Hujjat hozir ${draft.owner_name || 'boshqa foydalanuvchi'} tomonidan tahrirlanmoqda.`,
      {},
    );
  }

  let revision = draft.revision;
  try {
    const saved = await editorJson(`${base}/draft`, 'PUT', {
      revision,
      client_session_id: clientSessionId,
      ops: built.ops,
    });
    revision = saved.revision;
    // One request id for the whole commit, so sending it again after a confirmation is safe.
    const commit = { revision, commit_request_id: newCommitRequestId(), discard_signatures: false };
    try {
      return { status: 'saved', version: await editorJson(`${base}/draft/commit`, 'POST', commit) };
    } catch (error) {
      if (!(error instanceof EditorApiError) || error.code !== 'signatures_present') throw error;
      if (!options.confirmSignatures(error.details.count || 0)) {
        await discardDraftQuietly(contractId, revision);
        return { status: 'cancelled' };
      }
      return {
        status: 'saved',
        version: await editorJson(`${base}/draft/commit`, 'POST', { ...commit, discard_signatures: true }),
      };
    }
  } catch (error) {
    await discardDraftQuietly(contractId, revision);
    throw explainSaveError(error, built.labels);
  }
}

/** Saves an edited PDF file as the contract's next version (the upload route). Kept for the file upload. */
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
