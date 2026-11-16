/**
 * One download of a contract version's PDF for everything on the contract
 * page: the workspace and the PDF editor inside it (an iframe of this origin)
 * both ask here. Without it the same file travelled twice on every open and
 * three times after a save.
 *
 * Only the newest request is kept, in memory, for as long as the page lives:
 * nothing is written to disk (these files carry personal data).
 *
 * The editor reaches it as `window.parent.__swmDocumentCache`
 * (`public/editor/js/contract-source.js`).
 */
interface Entry {
  key: string;
  bytes: Promise<ArrayBuffer>;
}

let entry: Entry | null = null;

export const documentCache = {
  /** The version's bytes: the kept download, or `load()` (kept for the next caller). */
  get(contractId: string, versionNo: number, load: () => Promise<ArrayBuffer>): Promise<ArrayBuffer> {
    const key = `${contractId}:${versionNo}`;
    if (entry && entry.key === key) return entry.bytes;
    // The editor's frame may be the one that downloads: a buffer made in
    // another frame is not an `ArrayBuffer` of this one, so it is copied in.
    const bytes = load().then((data) =>
      data instanceof ArrayBuffer ? data : (new Uint8Array(data).slice().buffer as ArrayBuffer),
    );
    const mine: Entry = { key, bytes };
    entry = mine;
    // A failed download is not kept: the next caller tries again.
    bytes.catch(() => {
      if (entry === mine) entry = null;
    });
    return bytes;
  },
  clear(): void {
    entry = null;
  },
};

declare global {
  interface Window {
    __swmDocumentCache?: typeof documentCache;
  }
}

if (typeof window !== 'undefined') window.__swmDocumentCache = documentCache;
