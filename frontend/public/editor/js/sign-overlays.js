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
  const state = { overlays: [], lastAt: -1, canRedo: false, busy: false, selected: null };

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
          // A click without a drag selects it: ✕ takes it off, the corner resizes it.
          if (up.type !== 'pointercancel') select(overlay.key);
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

  /* -- selection: a frame with ✕ (take off) and a corner (resize) ------------ */

  function select(key) {
    state.selected = key;
    drawAll();
  }

  function deselect() {
    if (state.selected === null) return;
    state.selected = null;
    document.querySelectorAll('.sign-overlay-frame').forEach((frame) => frame.remove());
  }

  function takeOff(key) {
    state.selected = null;
    document.querySelectorAll('.sign-overlay-frame').forEach((frame) => frame.remove());
    window.parent.postMessage({ type: 'editor:overlay-delete', key }, origin);
  }

  function drawFrame(layer, el, overlay) {
    const layerBox = layer.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const frame = document.createElement('div');
    frame.className = 'sign-overlay-frame';
    frame.style.left = `${box.left - layerBox.left - 3}px`;
    frame.style.top = `${box.top - layerBox.top - 3}px`;
    frame.style.width = `${box.width + 6}px`;
    frame.style.height = `${box.height + 6}px`;

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'sign-overlay-remove';
    remove.title = overlay.kind === 'date' ? 'Clear this date' : 'Remove this signature';
    remove.innerHTML = '<i class="fa-solid fa-xmark"></i>';
    remove.addEventListener('pointerdown', (event) => event.stopPropagation());
    remove.addEventListener('mousedown', (event) => event.stopPropagation());
    remove.addEventListener('click', (event) => {
      event.stopPropagation();
      takeOff(overlay.key);
    });

    const corner = document.createElement('div');
    corner.className = 'sign-overlay-resize';
    corner.title = overlay.kind === 'date' ? 'Drag to resize (all dates)' : 'Drag to resize';
    corner.addEventListener('mousedown', (event) => event.stopPropagation());
    corner.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || state.busy) return;
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startY = event.clientY;
      const startW = box.width;
      const startH = box.height;
      let ratio = 1;
      try {
        corner.setPointerCapture(event.pointerId);
      } catch (error) {
        // resizing still works without capture
      }
      const isSvg = el instanceof SVGElement;
      const onMove = (move) => {
        // The larger of the two pulls decides, so the shape keeps its proportions.
        const byX = (startW + (move.clientX - startX)) / startW;
        const byY = (startH + (move.clientY - startY)) / startH;
        ratio = Math.max(0.3, Math.min(3, Math.max(byX, byY)));
        frame.style.width = `${startW * ratio + 6}px`;
        frame.style.height = `${startH * ratio + 6}px`;
        if (isSvg) {
          el.setAttribute('font-size', String(overlay.fontSize * page.viewport.scale * ratio));
        } else {
          el.style.transformOrigin = 'left top';
          el.style.transform = `scale(${ratio})`;
        }
      };
      const onUp = () => {
        corner.removeEventListener('pointermove', onMove);
        corner.removeEventListener('pointerup', onUp);
        corner.removeEventListener('pointercancel', onUp);
        if (Math.abs(ratio - 1) >= 0.01) {
          window.parent.postMessage({ type: 'editor:overlay-resize', key: overlay.key, ratio }, origin);
        } else {
          drawAll();
        }
      };
      corner.addEventListener('pointermove', onMove);
      corner.addEventListener('pointerup', onUp);
      corner.addEventListener('pointercancel', onUp);
    });
    // `page` for the svg preview above.
    const page = engine() && engine().pages.find((item) => item.stage && item.stage.contains(layer));
    frame.append(remove, corner);
    layer.appendChild(frame);
  }

  // A click elsewhere, Esc, or Delete / Backspace on the selected one.
  document.addEventListener('pointerdown', (event) => {
    if (state.selected === null) return;
    if (event.target.closest && (event.target.closest('.sign-overlay-frame') || event.target.closest('[data-key]'))) return;
    deselect();
  }, true);
  document.addEventListener('keydown', (event) => {
    if (state.selected === null) return;
    const active = document.activeElement;
    if (active && (active.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(active.tagName))) return;
    if (event.key === 'Escape') deselect();
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      takeOff(state.selected);
    }
  });

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
        if (overlay.src && state.selected === overlay.key) {
          if (el.complete) drawFrame(layer, el, overlay);
          else el.addEventListener('load', () => drawFrame(layer, el, overlay), { once: true });
        }
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
        if (state.selected === overlay.key) drawFrame(layer, text, overlay);
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
    // A selection whose mark is gone (taken off, undone) goes with it.
    if (state.selected !== null && !state.overlays.some((overlay) => overlay && overlay.key === state.selected)) state.selected = null;
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
