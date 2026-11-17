/*
 * Signatures and dates over the editor's pages, and the signing half of Undo / Redo.
 *
 * The contract page (EmbeddedEditor) sends `editor:overlays`:
 *   { overlays: [...], lastAt, canRedo, busy }
 * Each overlay is in PDF points, top-left origin, on the unrotated page:
 *   { kind: 'signature', page, x, y, width, height, src }
 *   { kind: 'date', page, x, y (baseline), text, fontSize, fontFamily, bold, italic }
 * They are drawn in their own layer on each page (never part of the text the
 * editor saves) and drawn again whenever a page is re-rendered (zoom, rotate).
 *
 * `lastAt` is when the newest signing step was made; the editor's Undo takes
 * back whichever is newer, that step or its own last edit (`undoIfNewer`).
 */
(function () {
  const origin = window.location.origin;
  const state = { overlays: [], lastAt: -1, canRedo: false, busy: false };

  function post(type) {
    if (window.parent !== window) window.parent.postMessage({ type }, origin);
  }

  function engine() {
    return window.sejdaEngine || null;
  }

  /** A rectangle in points (top-left origin) → pixels on the page as it is shown. */
  function toPixels(page, x, y, width, height) {
    const view = page.pdfPage.view; // [x0, y0, x1, y1] in PDF space
    const viewport = page.viewport;
    const a = viewport.convertToViewportPoint(view[0] + x, view[3] - y);
    const b = viewport.convertToViewportPoint(view[0] + x + width, view[3] - (y + height));
    return {
      left: Math.min(a[0], b[0]),
      top: Math.min(a[1], b[1]),
      width: Math.abs(b[0] - a[0]),
      height: Math.abs(b[1] - a[1]),
    };
  }

  /*
   * A placed signature or date can be dragged. While dragging it only moves on
   * screen; on release the move goes to the contract page in points (right /
   * down positive), which stores it and sends the new overlays back.
   */
  function makeDraggable(el, page, overlay, isSvg) {
    el.dataset.key = overlay.key;
    el.style.pointerEvents = 'auto';
    el.style.cursor = 'move';
    el.setAttribute('title', 'Drag to move');
    const stop = (event) => event.stopPropagation();
    el.addEventListener('mousedown', stop);
    el.addEventListener('click', stop);
    el.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || state.busy) return;
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startY = event.clientY;
      let moved = false;
      try {
        el.setPointerCapture(event.pointerId);
      } catch (error) {
        // dragging still works without capture
      }
      const place = (dx, dy) => {
        if (isSvg) el.setAttribute('transform', `translate(${dx} ${dy})`);
        else el.style.transform = `translate(${dx}px, ${dy}px)`;
      };
      const onMove = (move) => {
        const dx = move.clientX - startX;
        const dy = move.clientY - startY;
        if (!moved && Math.hypot(dx, dy) < 3) return;
        moved = true;
        el.classList.add('sign-overlay-dragging');
        place(dx, dy);
      };
      const onUp = (up) => {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        el.classList.remove('sign-overlay-dragging');
        if (!moved || up.type === 'pointercancel') {
          place(0, 0);
          return;
        }
        // Screen pixels -> PDF points through the page's own viewport (zoom and turn included).
        const viewport = page.viewport;
        const a = viewport.convertToPdfPoint(0, 0);
        const b = viewport.convertToPdfPoint(up.clientX - startX, up.clientY - startY);
        window.parent.postMessage(
          { type: 'editor:overlay-move', key: overlay.key, dx: b[0] - a[0], dy: -(b[1] - a[1]) },
          origin,
        );
      };
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerup', onUp);
      el.addEventListener('pointercancel', onUp);
    });
  }

  function draw(page) {
    if (!page || !page.stage || !page.viewport || !page.pdfPage) return;
    let layer = null;
    for (const child of page.stage.children) {
      if (child.classList.contains('sign-overlay-layer')) layer = child;
    }
    if (!layer) {
      layer = document.createElement('div');
      layer.className = 'sign-overlay-layer';
      page.stage.appendChild(layer);
    }
    layer.innerHTML = '';
    const pageNo = page.pageIndex + 1;
    const scale = page.viewport.scale;
    const svgNs = 'http://www.w3.org/2000/svg';
    let svg = null;

    state.overlays.forEach((overlay) => {
      if (!overlay || overlay.page !== pageNo) return;
      if (overlay.kind === 'signature') {
        const box = toPixels(page, overlay.x, overlay.y, overlay.width, overlay.height);
        const el = document.createElement(overlay.src ? 'img' : 'div');
        el.className = overlay.src ? 'sign-overlay-signature' : 'sign-overlay-signature sign-overlay-pending';
        if (overlay.src) {
          el.src = overlay.src;
          el.alt = '';
          el.draggable = false;
          el.onerror = () => el.classList.add('sign-overlay-pending');
        }
        el.style.left = `${box.left}px`;
        el.style.top = `${box.top}px`;
        el.style.width = `${box.width}px`;
        el.style.height = `${box.height}px`;
        if (overlay.src) makeDraggable(el, page, overlay, false);
        layer.appendChild(el);
      } else if (overlay.kind === 'date') {
        if (!svg) {
          svg = document.createElementNS(svgNs, 'svg');
          svg.setAttribute('class', 'sign-overlay-dates');
          layer.appendChild(svg);
        }
        const at = toPixels(page, overlay.x, overlay.y, 0, 0);
        const text = document.createElementNS(svgNs, 'text');
        text.setAttribute('x', String(at.left));
        text.setAttribute('y', String(at.top));
        text.setAttribute('font-size', String(overlay.fontSize * scale));
        text.style.fontFamily = overlay.fontFamily || 'Helvetica, Arial, sans-serif';
        text.style.fontWeight = overlay.bold ? '700' : '400';
        text.style.fontStyle = overlay.italic ? 'italic' : 'normal';
        text.textContent = overlay.text;
        makeDraggable(text, page, overlay, true);
        svg.appendChild(text);
      }
    });
  }

  function drawAll() {
    const current = engine();
    if (!current || !current.pages) return;
    current.pages.forEach(draw);
  }

  function updateButtons() {
    const current = engine();
    const undo = document.getElementById('btn-undo');
    const redo = document.getElementById('btn-redo');
    const localUndo = Boolean(current && current.undoStack && current.undoStack.length);
    const localRedo = Boolean(current && current.redoStack && current.redoStack.length);
    if (undo) undo.disabled = state.busy || !(localUndo || state.lastAt >= 0);
    if (redo) redo.disabled = state.busy || !localRedo;
  }

  window.addEventListener('message', (event) => {
    if (event.origin !== origin || event.source !== window.parent) return;
    const data = event.data;
    if (!data || data.type !== 'editor:overlays') return;
    state.overlays = Array.isArray(data.overlays) ? data.overlays : [];
    state.lastAt = typeof data.lastAt === 'number' ? data.lastAt : -1;
    state.canRedo = Boolean(data.canRedo);
    state.busy = Boolean(data.busy);
    // What was undone on the contract page cannot be redone any more (a newer step was made there).
    const current = engine();
    if (!state.canRedo && current && current.redoStack) {
      current.redoStack = current.redoStack.filter((entry) => entry.type !== 'sign');
    }
    drawAll();
    updateButtons();
  });

  window.SignOverlays = {
    draw,
    drawAll,
    updateButtons,
    /**
     * Undo the last signing step if it is newer than the editor's own last
     * edit (`localAt`, -1 when there is none). 'done', 'busy', or false.
     */
    undoIfNewer(localAt) {
      if (state.lastAt < 0 || state.lastAt <= localAt) return false;
      if (state.busy) return 'busy';
      state.busy = true;
      updateButtons();
      post('editor:sign-undo');
      return 'done';
    },
    redo() {
      if (state.busy || !state.canRedo) return false;
      state.busy = true;
      updateButtons();
      post('editor:sign-redo');
      return true;
    },
    /** A new edit in the editor: signing steps undone before it stay undone. */
    localChanged() {
      if (state.canRedo) post('editor:sign-redo-clear');
      updateButtons();
    },
    ready() {
      post('editor:ready');
      drawAll();
      updateButtons();
    },
  };
})();
