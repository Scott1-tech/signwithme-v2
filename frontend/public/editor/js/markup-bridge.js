/*
 * What the editor ADDS to a page -- text, text typed on a blank, ✓ / ✕ and
 * whiteout -- is not written into the PDF's own content. It is saved as the
 * contract's markup (`/api/contracts/{id}/document/markup`) and printed into
 * the final PDF at finalize, like every other mark the app places.
 *
 * Element shapes (the server's legacy annotation wire; points, top-left
 * origin, on the page as displayed):
 *   text:     { kind: 'text', x_pt, y_pt (baseline), text, font, size_pt, bold?, italic?, color? }
 *   check / cross: { kind, x_pt, y_pt (centre), size_pt }
 *   whiteout: { kind: 'whiteout', x_pt, y_pt, w_pt, h_pt }
 */
(function () {
  const MANAGED = new Set(['text', 'check', 'cross', 'whiteout']);
  const PALETTE = {
    black: '#000000',
    blue: '#1D4ED8',
    red: '#DC2626',
    green: '#16A34A',
    yellow: '#FACC15',
    orange: '#EA580C',
    purple: '#7E22CE',
  };

  function rgb(hex) {
    const value = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!value) return [0, 0, 0];
    const n = parseInt(value[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  /** The nearest colour the server prints (its palette is closed). */
  function colorToWire(hex) {
    const [r, g, b] = rgb(hex);
    let best = 'black';
    let bestDistance = Infinity;
    Object.entries(PALETTE).forEach(([name, value]) => {
      const [pr, pg, pb] = rgb(value);
      const distance = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = name;
      }
    });
    return best;
  }

  /** Embedded Liberation faces: they print Latin, Cyrillic and Uzbek letters. */
  function fontToWire(family) {
    const value = String(family || '').toLowerCase();
    if (/courier|mono/.test(value)) return 'LiberationMono';
    if (/helvetica|arial|sans/.test(value)) return 'LiberationSans';
    return 'LiberationSerif';
  }

  function fontFromWire(font) {
    const value = String(font || '').toLowerCase();
    if (/courier|mono/.test(value)) return 'Courier New, monospace';
    if (/serif|times|caladea/.test(value) && !/sans/.test(value)) return 'Times New Roman, Times, serif';
    return 'Helvetica, Arial, sans-serif';
  }

  function newId() {
    const random = window.crypto && window.crypto.getRandomValues
      ? Array.from(window.crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(16).padStart(2, '0')).join('')
      : Math.random().toString(16).slice(2, 14);
    return `ed-${Date.now().toString(36)}-${random}`;
  }

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const round = (value) => Math.round(value * 100) / 100;

  /** Server elements of this version, by page index; and the ids the editor took over. */
  function splitForPages(elements, uidByIndex) {
    const indexOfUid = new Map(Object.entries(uidByIndex).map(([index, uid]) => [uid, Number(index)]));
    const byPage = new Map();
    const taken = new Set();
    (elements || []).forEach((element) => {
      if (!element || !MANAGED.has(element.kind)) return;
      const index = indexOfUid.get(element.page_uid);
      if (index === undefined) return;
      taken.add(element.id);
      if (!byPage.has(index)) byPage.set(index, []);
      byPage.get(index).push(element);
    });
    return { byPage, taken };
  }

  /** The PUT body: what the editor shows now, plus everything it did not take over. */
  function mergeForSave(serverElements, takenIds, mine, uidByIndex) {
    const kept = (serverElements || []).filter((element) => !takenIds.has(element.id));
    const placed = [];
    mine.forEach(({ pageIndex, element }) => {
      const uid = uidByIndex[pageIndex];
      if (uid) placed.push({ ...element, page_uid: uid });
    });
    return [...kept, ...placed];
  }

  /** A stable text for "did anything change": element content without ids. */
  function signature(mine) {
    return JSON.stringify(
      mine
        .map(({ pageIndex, element }) => {
          const { id, ...rest } = element;
          // A point is above what a zoom's rounding moves.
          Object.keys(rest).forEach((key) => {
            if (typeof rest[key] === 'number') rest[key] = Math.round(rest[key]);
          });
          return [pageIndex, rest];
        })
        .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    );
  }

  window.MarkupBridge = {
    MANAGED,
    colorToWire,
    colorFromWire: (name) => PALETTE[name] || '#000000',
    fontToWire,
    fontFromWire,
    newId,
    clamp,
    round,
    splitForPages,
    mergeForSave,
    signature,
  };
})();
