    class SejdaPDFEngine {
      constructor(options = {}) {
        pdfjsLib.GlobalWorkerOptions.workerSrc = new URL('vendor/pdf.worker.min.js', document.baseURI).href;

        this.root = typeof options.container === 'string'
          ? document.querySelector(options.container)
          : options.container;
        this.root.classList.add('sejda-workspace');

        this.defaultScale = options.scale || 1.35;
        this.pdfBytes = null;
        this.pdfDoc = null;
        this.pages = [];
        this.activeTool = 'text';
        this.focusedNode = null;
        this.selectedBox = null;
        this.selectedLineObj = null;
        this.pendingSignature = null;
        this.pendingImage = null;
        this.undoStack = [];
        // What Undo took back, newest last; `{ type: 'sign' }` is a signing step
        // undone on the contract page (sign-overlays.js). A new edit clears it.
        this.redoStack = [];

        this.measureCtx = document.createElement('canvas').getContext('2d');

        this._buildTextFloatingBar();
        this._buildBoxFloatingBar();
      }

      _buildTextFloatingBar() {
        const bar = document.createElement('div');
        bar.className = 'sejda-floating-bar';
        bar.onmousedown = (e) => {
          if (e.target.tagName !== 'SELECT' && e.target.tagName !== 'INPUT') {
            e.preventDefault();
          }
          e.stopPropagation();
        };
        bar.innerHTML = `
          <button class="f-btn" data-cmd="bold" title="Qalin (Bold)"><i class="fa-solid fa-bold"></i></button>
          <button class="f-btn" data-cmd="italic" title="Yotiq (Italic)"><i class="fa-solid fa-italic"></i></button>
          <button class="f-btn" data-cmd="move" title="Joyini surish (Ushlab torting)"><i class="fa-solid fa-arrows-up-down-left-right"></i></button>
          <div class="f-sep"></div>
          <button class="f-btn" data-cmd="dec"><i class="fa-solid fa-minus"></i></button>
          <input type="number" data-cmd="size" value="11" min="5" max="72" style="width:42px;text-align:center;border:1px solid #cbd5e1;border-radius:3px;padding:2px;" />
          <button class="f-btn" data-cmd="inc"><i class="fa-solid fa-plus"></i></button>
          <div class="f-sep"></div>
          <select data-cmd="font" style="border:1px solid #cbd5e1;border-radius:3px;padding:3px;font-size:12px;">
            <option value="Times New Roman, Times, serif">Times New Roman</option>
            <option value="Helvetica, Arial, sans-serif">Helvetica / Arial</option>
            <option value="Courier New, monospace">Courier New</option>
          </select>
          <input type="color" data-cmd="color" value="#000000" title="Matn / Chiziq rangi" style="width:22px;height:22px;border:none;background:none;cursor:pointer;" />
          <div class="f-sep" id="line-tools-sep" style="display:none;"></div>
          <button class="f-btn" data-cmd="line-shorter" id="btn-line-shorter" style="display:none;" title="Chiziqni qisqartirish">
            <i class="fa-solid fa-compress"></i> -Uzunlik
          </button>
          <button class="f-btn" data-cmd="line-longer" id="btn-line-longer" style="display:none;" title="Chiziqni uzaytirish">
            <i class="fa-solid fa-expand"></i> +Uzunlik
          </button>
          <button class="f-btn" data-cmd="toggle-line" id="btn-toggle-line" style="display:none;color:#0956a6;" title="Tagidagi chiziqni yoqish/o'chirish">
            <i class="fa-solid fa-underline"></i> Chiziq
          </button>
          <div class="f-sep"></div>
          <button class="f-btn" data-cmd="delete" style="color:#dc2626;" title="Butunlay o'chirish"><i class="fa-regular fa-trash-can"></i></button>
          <div class="f-sep"></div>
          <button class="f-btn" data-cmd="close" title="Panelni yopish (Esc)"><i class="fa-solid fa-xmark"></i></button>
        `;
        document.body.appendChild(bar);
        this.textBar = bar;

        // The bar goes away when the work is done: its ✕, Esc, or a click / Tab
        // that leaves the text (but not a click on the bar's own controls).
        const closeBar = () => {
          if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
          this.deselectAll();
        };
        bar.querySelector('[data-cmd="close"]').onclick = closeBar;
        document.addEventListener('keydown', (e) => {
          if (e.key === 'Escape' && bar.classList.contains('visible')) closeBar();
        });
        document.addEventListener('focusout', (e) => {
          if (!e.target.closest || !e.target.closest('.dom-layer')) return;
          setTimeout(() => {
            const active = document.activeElement;
            if (this.barDragging) return;
            if (active && (bar.contains(active) || (active.closest && active.closest('.dom-layer')))) return;
            if (document.querySelector('.sejda-editable-line.selected, .added-obj.selected')) return;
            bar.classList.remove('visible');
          }, 0);
        });

        bar.querySelector('[data-cmd="bold"]').onclick = () => this._toggleFormat('bold');
        bar.querySelector('[data-cmd="italic"]').onclick = () => this._toggleFormat('italic');
        bar.querySelector('[data-cmd="inc"]').onclick = () => this._stepFontSize(1);
        bar.querySelector('[data-cmd="dec"]').onclick = () => this._stepFontSize(-1);
        bar.querySelector('[data-cmd="size"]').oninput = (e) => this._applyFontSize(parseFloat(e.target.value) || 11);
        bar.querySelector('[data-cmd="font"]').onchange = (e) => this._applyFontFamily(e.target.value);
        bar.querySelector('[data-cmd="color"]').oninput = (e) => this._applyColor(e.target.value);
        bar.querySelector('[data-cmd="delete"]').onclick = () => this._deleteActiveNode();

        bar.querySelector('[data-cmd="line-shorter"]').onclick = () => {
          if (!this.selectedLineObj) return;
          this.selectedLineObj.style.width = `${Math.max(16, this.selectedLineObj.offsetWidth - 20)}px`;
        };
        bar.querySelector('[data-cmd="line-longer"]').onclick = () => {
          if (!this.selectedLineObj) return;
          this.selectedLineObj.style.width = `${this.selectedLineObj.offsetWidth + 20}px`;
        };
        bar.querySelector('[data-cmd="toggle-line"]').onclick = () => {
          if (!this.selectedLineObj) return;
          this.selectedLineObj.classList.toggle('line-hidden');
          bar.querySelector('[data-cmd="toggle-line"]').classList.toggle(
            'active',
            !this.selectedLineObj.classList.contains('line-hidden')
          );
        };

        bar.querySelector('[data-cmd="move"]').onmousedown = (e) => {
          if (!this.focusedNode) return;
          this._startMove(e, this._moveTarget());
        };

        // A grip right next to the text being edited: drag it to move the text
        // (the bar's own ✥ does the same). It shows and hides with the bar.
        const grip = document.createElement('div');
        grip.className = 'line-grip';
        grip.title = 'Surish uchun ushlab torting';
        grip.innerHTML = '<i class="fa-solid fa-grip-vertical"></i>';
        grip.addEventListener('mousedown', (e) => {
          if (!this.focusedNode) return;
          this._startMove(e, this._moveTarget());
        });
        document.body.appendChild(grip);
        this.grip = grip;
        new MutationObserver(() => {
          if (!bar.classList.contains('visible')) grip.classList.remove('visible');
        }).observe(bar, { attributes: true, attributeFilter: ['class'] });

        // The bar and the grip are fixed on screen: they follow the text when the pages scroll.
        window.addEventListener('scroll', () => {
          if (this.barDragging || !bar.classList.contains('visible')) return;
          if (this.barTarget && this.barTarget.isConnected) this._positionBar(bar, this.barTarget);
        }, { capture: true, passive: true });

        // Enter ends the edit of a line (a line of the PDF is one line): the bar goes away.
        document.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter' || e.shiftKey) return;
          const target = e.target;
          if (!target.classList || !['sejda-line', 'line-text-input', 'added-text-editor'].some((name) => target.classList.contains(name))) return;
          e.preventDefault();
          target.blur();
          this.deselectAll();
        });
      }

      // The element a move takes: the line widget, the added object, or the text line itself.
      _moveTarget() {
        return this.focusedNode.closest('.sejda-editable-line')
          || this.focusedNode.closest('.added-obj')
          || this.focusedNode;
      }

      _startMove(e, target) {
        e.preventDefault();
        e.stopPropagation();
        const bar = this.textBar;
        const grip = this.grip;
        const pageStage = target.closest('.pdf-stage');
        const pageState = this.pages.find(p => p.stage === pageStage) || this.pages[0];
        const sc = pageState ? pageState.scale : 1.35;
        const sx = e.clientX, sy = e.clientY;
        const sl = parseFloat(target.style.left) || 0;
        const st = parseFloat(target.style.top) || 0;
        // While dragging, the bar and the grip travel with the pointer instead
        // of jumping onto the text that is being moved.
        const barLeft = parseFloat(bar.style.left) || 0;
        const barTop = parseFloat(bar.style.top) || 0;
        const gripLeft = parseFloat(grip.style.left) || 0;
        const gripTop = parseFloat(grip.style.top) || 0;
        this.barDragging = true;
        document.body.classList.add('moving-text');

        const onMove = (ev) => {
          const nl = sl + (ev.clientX - sx);
          const nt = st + (ev.clientY - sy);
          target.style.left = `${nl}px`;
          target.style.top = `${nt}px`;
          if (target.classList.contains('sejda-line')) {
            target.dataset.dx = ((nl - parseFloat(target.dataset.initLeft)) / sc).toFixed(2);
            target.dataset.dy = ((nt - parseFloat(target.dataset.initTop)) / sc).toFixed(2);
            this._recordModification(target);
          }
          bar.style.left = `${barLeft + (ev.clientX - sx)}px`;
          bar.style.top = `${barTop + (ev.clientY - sy)}px`;
          grip.style.left = `${gripLeft + (ev.clientX - sx)}px`;
          grip.style.top = `${gripTop + (ev.clientY - sy)}px`;
        };
        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
          this.barDragging = false;
          document.body.classList.remove('moving-text');
          this._positionBar(this.textBar, target);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }

      _buildBoxFloatingBar() {
        const bar = document.createElement('div');
        bar.className = 'sejda-floating-bar';
        bar.onmousedown = (e) => e.stopPropagation();
        bar.innerHTML = `
          <label class="f-btn" title="Fon rangi">
            <i class="fa-solid fa-fill-drip"></i> Fon:
            <input type="color" data-box="fill" value="#ffffff" style="width:22px;height:20px;border:1px solid #cbd5e1;cursor:pointer;" />
          </label>
          <label class="f-btn" title="Chegara chizig'i">
            <input type="checkbox" data-box="has-border" /> Ramka
          </label>
          <input type="color" data-box="stroke" value="#000000" style="width:22px;height:20px;border:none;background:none;cursor:pointer;" />
          <div class="f-sep"></div>
          <button class="f-btn" data-box="add-text" style="color:#0956a6;font-weight:600;">
            <i class="fa-solid fa-i-cursor"></i> + Ustiga yozish
          </button>
          <div class="f-sep"></div>
          <button class="f-btn" data-box="dup" title="Nusxalash"><i class="fa-regular fa-copy"></i></button>
          <button class="f-btn" data-box="del" style="color:#dc2626;" title="O'chirish"><i class="fa-regular fa-trash-can"></i></button>
        `;
        document.body.appendChild(bar);
        this.boxBar = bar;

        bar.querySelector('[data-box="fill"]').oninput = (e) => {
          if (!this.selectedBox) return;
          this.selectedBox.dataset.fillColor = e.target.value;
          this.selectedBox.style.backgroundColor = e.target.value;
        };

        bar.querySelector('[data-box="has-border"]').onchange = (e) => {
          if (!this.selectedBox) return;
          const stroke = bar.querySelector('[data-box="stroke"]').value;
          this.selectedBox.dataset.hasBorder = e.target.checked ? 'true' : 'false';
          this.selectedBox.style.border = e.target.checked ? `1.5px solid ${stroke}` : '1px dashed transparent';
        };

        bar.querySelector('[data-box="stroke"]').oninput = (e) => {
          if (!this.selectedBox) return;
          this.selectedBox.dataset.strokeColor = e.target.value;
          bar.querySelector('[data-box="has-border"]').checked = true;
          this.selectedBox.dataset.hasBorder = 'true';
          this.selectedBox.style.border = `1.5px solid ${e.target.value}`;
        };

        bar.querySelector('[data-box="add-text"]').onclick = () => {
          if (!this.selectedBox) return;
          const stage = this.selectedBox.closest('.pdf-stage');
          const state = this.pages.find(p => p.stage === stage);
          if (!state) return;
          const x = parseFloat(this.selectedBox.style.left) + 6;
          const y = parseFloat(this.selectedBox.style.top) + Math.max(2, (this.selectedBox.offsetHeight - 16) / 2);
          this.deselectAll();
          this._spawnNewText(state, x, y);
        };

        bar.querySelector('[data-box="dup"]').onclick = () => {
          if (!this.selectedBox) return;
          const stage = this.selectedBox.closest('.pdf-stage');
          const state = this.pages.find(p => p.stage === stage);
          if (!state) return;
          const x = parseFloat(this.selectedBox.style.left) + 15;
          const y = parseFloat(this.selectedBox.style.top) + 15;
          const w = this.selectedBox.offsetWidth;
          const h = this.selectedBox.offsetHeight;
          const nb = this._createResizableBox(state, x, y, w, h, this.selectedBox.dataset.type);
          nb.style.backgroundColor = this.selectedBox.style.backgroundColor;
          nb.dataset.fillColor = this.selectedBox.dataset.fillColor;
          nb.dataset.strokeColor = this.selectedBox.dataset.strokeColor;
          nb.dataset.hasBorder = this.selectedBox.dataset.hasBorder;
          nb.style.border = this.selectedBox.style.border;
          this._selectBox(nb);
        };

        bar.querySelector('[data-box="del"]').onclick = () => {
          if (!this.selectedBox) return;
          this.selectedBox.remove();
          this.selectedBox = null;
          this.boxBar.classList.remove('visible');
        };
      }

      deselectAll() {
        document.querySelectorAll('.added-obj[data-type="added-text"]').forEach(wrap => {
          const ed = wrap.querySelector('.added-text-editor');
          if (ed && !ed.textContent.trim() && document.activeElement !== ed) {
            wrap.remove();
          }
        });
        document.querySelectorAll('.added-obj.selected, .sejda-box-item.selected, .sejda-editable-line.selected').forEach(el => {
          el.classList.remove('selected');
        });
        this.textBar.classList.remove('visible');
        this.boxBar.classList.remove('visible');
        this.selectedBox = null;
        this.selectedLineObj = null;
      }

      async loadPDF(bytes) {
        this.pdfBytes = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
        this.pdfDoc = await pdfjsLib.getDocument({
          data: this.pdfBytes.slice(0),
          disableFontFace: false
        }).promise;

        this.root.innerHTML = '';
        this.pages = [];
        this.deselectAll();
        this.registry = new Map();
        this.overlaySeq = 0;
        this.structureChanged = false;
        this.pendingMarkup = new Map();

        // A page is drawn when it comes near the screen, not all at once: a
        // 40-page contract opens as fast as a 2-page one and holds bitmaps
        // only for the pages that were looked at.
        if (this.lazyObserver) this.lazyObserver.disconnect();
        this.lazyObserver = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const state = this.pages.find((page) => page.stage === entry.target);
            if (state) this._ensureRendered(state);
          });
        }, { rootMargin: '900px 0px' });

        for (let i = 1; i <= this.pdfDoc.numPages; i++) {
          await this._buildPage(i);
        }
        // The first pages are on screen at once.
        await Promise.all(this.pages.slice(0, 2).map((state) => this._ensureRendered(state)));
      }

      /** Draws a page the first time it is needed, then shows its saved marks. */
      async _ensureRendered(state) {
        if (state.rendered) return;
        if (state.rendering) return state.rendering;
        state.rendering = (async () => {
          await this._renderStage(state, true);
          const pending = this.pendingMarkup && this.pendingMarkup.get(state.pageIndex);
          if (pending) {
            this.pendingMarkup.delete(state.pageIndex);
            this.restorePageMarkup(state, pending);
          }
        })();
        try {
          await state.rendering;
        } finally {
          state.rendering = null;
        }
      }

      /** Saved marks for a page: shown now if it is drawn, else when it is. */
      showPageMarkup(state, elements) {
        if (state.rendered) this.restorePageMarkup(state, elements);
        else if (elements && elements.length) this.pendingMarkup.set(state.pageIndex, elements);
      }

      /**
       * SEJDA v5.0 — TRUE BINARY CANVAS DECONSTRUCTOR:
       * PDF.js sahifani chizayotganda:
       * 1. Barcha matnlarni (ctx.fillText / strokeText) 100% to'xtatadi -> Canvas'ga birorta ham harf tushmaydi!
       * 2. Barcha gorizontal chiziqlarni (ctx.moveTo/lineTo/rect + stroke/fill/fillRect) 100% ushlab qoladi ->
       *    Canvas'ga chizmasdan, ularni jonli .sejda-editable-line obyektlariga aylantiradi!
       * 3. Barcha kichik kvadrat Checkboxlarni (☐) ushlab qolib, jonli Checkboxga aylantiradi!
       */
      async _renderDeconstructedBinaryCanvas(pdfPage, canvas, viewport, scale, passThrough = false) {
        // passThrough: the page is drawn exactly as the PDF has it (text and
        // rules included); the hooks below only record where things are.
        const ctx = canvas.getContext('2d');
        const interceptedLines = [];
        const interceptedBoxes = [];
        const capturedGlyphColors = [];

        const origFillText = ctx.fillText;
        const origStrokeText = ctx.strokeText;
        const origBeginPath = ctx.beginPath;
        const origMoveTo = ctx.moveTo;
        const origLineTo = ctx.lineTo;
        const origRect = ctx.rect;
        const origFillRect = ctx.fillRect;
        const origStrokeRect = ctx.strokeRect;
        const origStroke = ctx.stroke;
        const origFill = ctx.fill;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        let ptsCount = 0;

        const resetPathBounds = () => {
          minX = Infinity; minY = Infinity; maxX = -Infinity; maxY = -Infinity;
          ptsCount = 0;
        };

        const recordPoint = (x, y) => {
          const m = ctx.getTransform();
          const sx = m.a * x + m.c * y + m.e;
          const sy = m.b * x + m.d * y + m.f;
          if (sx < minX) minX = sx;
          if (sy < minY) minY = sy;
          if (sx > maxX) maxX = sx;
          if (sy > maxY) maxY = sy;
          ptsCount++;
        };

        // 1. Matnlarni Canvas'ga chizishni 100% to'xtatamiz!
        ctx.fillText = function(str) {
          const m = ctx.getTransform();
          capturedGlyphColors.push({
            x: m.e,
            y: m.f,
            color: ctx.fillStyle
          });
          if (passThrough) return origFillText.apply(this, arguments);
        };

        ctx.strokeText = function() {
          if (passThrough) return origStrokeText.apply(this, arguments);
        };

        // 2. Vektor chiziqlar va Checkbox kvadratlarni ushlab qolamiz!
        ctx.beginPath = function() {
          resetPathBounds();
          return origBeginPath.apply(this, arguments);
        };

        ctx.moveTo = function(x, y) {
          recordPoint(x, y);
          return origMoveTo.apply(this, arguments);
        };

        ctx.lineTo = function(x, y) {
          recordPoint(x, y);
          return origLineTo.apply(this, arguments);
        };

        ctx.rect = function(x, y, w, h) {
          recordPoint(x, y);
          recordPoint(x + w, y + h);
          return origRect.apply(this, arguments);
        };

        ctx.fillRect = function(x, y, w, h) {
          const m = ctx.getTransform();
          const x1 = m.a * x + m.c * y + m.e;
          const y1 = m.b * x + m.d * y + m.f;
          const x2 = m.a * (x + w) + m.c * (y + h) + m.e;
          const y2 = m.b * (x + w) + m.d * (y + h) + m.f;
          const sw = Math.abs(x2 - x1);
          const sh = Math.abs(y2 - y1);

          if (sw >= 18 * scale && sh <= 4.5 * scale) {
            interceptedLines.push({
              x: Math.min(x1, x2),
              y: (y1 + y2) / 2,
              w: sw,
              color: typeof ctx.fillStyle === 'string' ? ctx.fillStyle : '#1e293b'
            });
            if (!passThrough) return; // Canvas'ga chizmaymiz!
          }
          return origFillRect.apply(this, arguments);
        };

        ctx.strokeRect = function(x, y, w, h) {
          const m = ctx.getTransform();
          const x1 = m.a * x + m.c * y + m.e;
          const y1 = m.b * x + m.d * y + m.f;
          const x2 = m.a * (x + w) + m.c * (y + h) + m.e;
          const y2 = m.b * (x + w) + m.d * (y + h) + m.f;
          const sw = Math.abs(x2 - x1);
          const sh = Math.abs(y2 - y1);

          if (sw >= 7 * scale && sw <= 18 * scale && Math.abs(sw - sh) <= 3.5 * scale) {
            interceptedBoxes.push({
              x: Math.min(x1, x2),
              y: Math.min(y1, y2),
              w: sw,
              h: sh
            });
          }
          return origStrokeRect.apply(this, arguments);
        };

        ctx.stroke = function() {
          if (ptsCount >= 2 && maxX > minX) {
            const w = maxX - minX;
            const h = maxY - minY;
            // Gorizontal vektor chiziq
            if (w >= 18 * scale && h <= 4.5 * scale && ptsCount <= 10) {
              interceptedLines.push({
                x: minX,
                y: (minY + maxY) / 2,
                w,
                color: typeof ctx.strokeStyle === 'string' ? ctx.strokeStyle : '#1e293b'
              });
              if (passThrough) return origStroke.apply(this, arguments);
              origBeginPath.call(this);
              resetPathBounds();
              return; // Canvas'ga chizmaymiz!
            }
            // Checkbox kvadrati (☐)
            if (w >= 7 * scale && w <= 18 * scale && Math.abs(w - h) <= 3.5 * scale && ptsCount <= 10) {
              interceptedBoxes.push({ x: minX, y: minY, w, h });
            }
          }
          return origStroke.apply(this, arguments);
        };

        ctx.fill = function() {
          if (ptsCount >= 2 && maxX > minX) {
            const w = maxX - minX;
            const h = maxY - minY;
            // Ingichka to'ldirilgan poligon sifatida chizilgan gorizontal chiziq
            if (w >= 18 * scale && h <= 4.5 * scale && ptsCount <= 12) {
              interceptedLines.push({
                x: minX,
                y: (minY + maxY) / 2,
                w,
                color: typeof ctx.fillStyle === 'string' ? ctx.fillStyle : '#1e293b'
              });
              if (passThrough) return origFill.apply(this, arguments);
              origBeginPath.call(this);
              resetPathBounds();
              return; // Canvas'ga chizmaymiz!
            }
          }
          return origFill.apply(this, arguments);
        };

        try {
          await pdfPage.render({ canvasContext: ctx, viewport }).promise;
        } finally {
          ctx.fillText = origFillText;
          ctx.strokeText = origStrokeText;
          ctx.beginPath = origBeginPath;
          ctx.moveTo = origMoveTo;
          ctx.lineTo = origLineTo;
          ctx.rect = origRect;
          ctx.fillRect = origFillRect;
          ctx.strokeRect = origStrokeRect;
          ctx.stroke = origStroke;
          ctx.fill = origFill;
        }

        return { interceptedLines, interceptedBoxes, capturedGlyphColors };
      }

      async _buildPage(pageNum) {
        const pdfPage = await this.pdfDoc.getPage(pageNum);
        const state = {
          pageNum,
          pageIndex: pageNum - 1,
          scale: this.defaultScale,
          // The page's own /Rotate: it is shown as the file shows it.
          rotation: ((pdfPage.rotate || 0) % 360 + 360) % 360,
          pdfPage
        };
        this.pages.push(state);

        const bar = document.createElement('div');
        bar.className = 'page-bar';
        bar.innerHTML = `
          <span class="page-num-badge">${pageNum}</span>
          <div class="page-ctrl-group">
            <button data-act="del" title="Sahifani o'chirish"><i class="fa-regular fa-trash-can"></i></button>
            <button data-act="zin" title="Yaqinlashtirish"><i class="fa-solid fa-magnifying-glass-plus"></i></button>
            <button data-act="zout" title="Uzoqlashtirish"><i class="fa-solid fa-magnifying-glass-minus"></i></button>
            <button data-act="rccw" title="Chapga burish"><i class="fa-solid fa-rotate-left"></i></button>
            <button data-act="rcw" title="O'ngga burish"><i class="fa-solid fa-rotate-right"></i></button>
          </div>
          <button class="action-chip" data-act="ins" style="color:#2563eb;">
            <i class="fa-solid fa-circle-plus"></i> Insert page here
          </button>
        `;

        const stage = document.createElement('div');
        stage.className = 'pdf-stage';

        const vectorCanvas = document.createElement('canvas');
        vectorCanvas.className = 'vector-canvas';

        const annotCanvas = document.createElement('canvas');
        annotCanvas.className = 'annot-canvas';

        const domLayer = document.createElement('div');
        domLayer.className = 'dom-layer';

        // Pieces of the page WITHOUT its text, shown under a line that is being changed.
        const patchLayer = document.createElement('div');
        patchLayer.className = 'patch-layer';

        stage.append(vectorCanvas, patchLayer, annotCanvas, domLayer);
        this.root.append(bar, stage);

        state.stage = stage;
        state.vectorCanvas = vectorCanvas;
        state.annotCanvas = annotCanvas;
        state.domLayer = domLayer;
        state.patchLayer = patchLayer;
        this._watchReveal(state);

        // Sized now, drawn when it comes near the screen (`_ensureRendered`).
        const viewport = pdfPage.getViewport({ scale: state.scale, rotation: state.rotation });
        state.viewport = viewport;
        stage.style.width = `${viewport.width}px`;
        stage.style.height = `${viewport.height}px`;
        stage.classList.add('page-not-drawn');
        this._attachStageEvents(state, bar);
        this.lazyObserver.observe(stage);
      }

      async _renderStage(state, rebuildDom = false) {
        state.rendered = true;
        state.stage.classList.remove('page-not-drawn');
        const viewport = state.pdfPage.getViewport({ scale: state.scale, rotation: state.rotation });
        state.viewport = viewport;

        state.stage.style.width = `${viewport.width}px`;
        state.stage.style.height = `${viewport.height}px`;
        state.vectorCanvas.width = viewport.width;
        state.vectorCanvas.height = viewport.height;

        const { interceptedLines, interceptedBoxes, capturedGlyphColors } =
          await this._renderDeconstructedBinaryCanvas(
            state.pdfPage,
            state.vectorCanvas,
            viewport,
            state.scale,
            true
          );
        // The text-free copy belongs to the old size; it is drawn again when needed.
        state.cleanCanvas = null;
        state.cleanPending = null;
        state.patchSig = null;
        state.renderSeq = (state.renderSeq || 0) + 1;

        if (rebuildDom) {
          state.annotCanvas.width = viewport.width;
          state.annotCanvas.height = viewport.height;
          // The text edits made on this page are carried over the rebuild (zoom, rotate).
          const carried = this._snapshotPage(state);
          this._dropPageEntries(state);
          state.domLayer.innerHTML = '';

          // 1. Matn qatorlari va matn ichidagi "_____" chiziqlarni yaratish
          const createdLineRects = await this._extractAndBuildLiveDomNodes(state, capturedGlyphColors);

          // 2. Canvas'dan ushlab qolingan haqiqiy Binar Vektor Chiziqlarni jonli vidjetlarga aylantirish
          const lineH = 14 * state.scale;
          for (const ln of interceptedLines) {
            const duplicate = createdLineRects.some(
              ex => Math.abs(ex.y - ln.y) < 5 && Math.abs(ex.x - ln.x) < 10
            );
            if (duplicate) continue;
            createdLineRects.push(ln);

            const vector = this._createEditableLineWidget(
              state,
              ln.x,
              ln.y - lineH + 2,
              ln.w,
              lineH,
              'Times New Roman, Times, serif',
              10.5,
              ln.color
            );
            this._register(vector, state, 'vector', '', ln.x, ln.y - lineH + 2, ln.w, lineH, vector.querySelector('.line-text-input'));
          }

          // 3. Canvas'dan ushlab qolingan Binar Vektor Checkboxlarni jonli qilish
          for (const bx of interceptedBoxes) {
            this._createAutoCheckbox(state, bx.x, bx.y, bx.w, bx.h);
          }
          this._restorePage(state, carried);
        }
        // The signatures and dates placed in the app, at this page's new size.
        if (window.SignOverlays) window.SignOverlays.draw(state);
        this._syncReveal(state);
      }

      /*
       * THE PAGE STAYS AS THE PDF HAS IT. The canvas shows the original page;
       * every line of text has an invisible, clickable twin over it. Only a
       * line that is being edited (or was changed, moved, restyled or erased)
       * is "revealed": a piece of the page drawn without its text covers the
       * original there, and the editable twin becomes visible on top of it.
       * Everything else is the untouched original picture.
       */
      _watchReveal(state) {
        let queued = false;
        const schedule = () => {
          if (queued) return;
          queued = true;
          requestAnimationFrame(() => {
            queued = false;
            this._syncReveal(state);
          });
        };
        new MutationObserver(schedule).observe(state.domLayer, {
          subtree: true,
          childList: true,
          characterData: true,
          attributes: true,
          attributeFilter: ['style', 'class'],
        });
        state.domLayer.addEventListener('focusin', schedule);
        state.domLayer.addEventListener('focusout', schedule);
        // The text-free copy is prepared as soon as the pointer is over the page.
        state.stage.addEventListener('mouseenter', () => this._ensureCleanCanvas(state), { passive: true });
      }

      _styleSignature(el) {
        return [el.style.fontSize, el.style.fontFamily, el.style.fontWeight, el.style.fontStyle, el.style.color].join('|');
      }

      // Is this registered piece different from the original page (or being edited)?
      _isTouched(entry) {
        const el = entry.el;
        if (!el.isConnected) return true;
        if (entry.kind === 'checkbox' || entry.kind === 'added') return false;
        if (entry.kind === 'line') {
          if (document.activeElement === el) return true;
          if (el.textContent !== entry.origText) return true;
          if (Math.abs(parseFloat(el.dataset.dx) || 0) > 0.001 || Math.abs(parseFloat(el.dataset.dy) || 0) > 0.001) return true;
          if (Math.abs((parseFloat(el.style.left) || 0) - entry.px.x) > 0.5) return true;
          if (Math.abs((parseFloat(el.style.top) || 0) - entry.px.top) > 0.5) return true;
          if (entry.styleSig === undefined) entry.styleSig = this._styleSignature(el);
          return this._styleSignature(el) !== entry.styleSig;
        }
        return el.classList.contains('line-hidden') || this._geometryChanged(entry);
      }

      // The part of the original picture this piece occupies, in canvas pixels.
      _patchRect(entry, scale) {
        const { x, top, w, h } = entry.px;
        if (entry.kind === 'line') return { x: x - 2, y: top - h * 0.12, w: w + 4, h: h * 1.3 };
        if (entry.kind === 'vector') return { x: x - 1, y: top + h - 2 - 3 * scale, w: w + 2, h: 6 * scale };
        return { x: x - 1, y: top, w: w + 2, h: h * 1.35 };
      }

      // The page drawn without its text and rules, made once per size, only when a line is touched.
      _ensureCleanCanvas(state) {
        if (state.cleanCanvas || state.cleanPending || !state.viewport) return state.cleanPending || null;
        const seq = state.renderSeq;
        const canvas = document.createElement('canvas');
        canvas.width = state.vectorCanvas.width;
        canvas.height = state.vectorCanvas.height;
        state.cleanPending = this._renderDeconstructedBinaryCanvas(state.pdfPage, canvas, state.viewport, state.scale)
          .then(() => {
            if (state.renderSeq !== seq) return;
            state.cleanCanvas = canvas;
            state.cleanPending = null;
            this._syncReveal(state);
          })
          .catch(() => {
            if (state.renderSeq === seq) state.cleanPending = null;
          });
        return state.cleanPending;
      }

      _syncReveal(state) {
        if (!state.patchLayer || !this.registry) return;
        const touched = [];
        const pristine = [];
        for (const entry of this.registry.values()) {
          if (entry.state !== state) continue;
          if (entry.kind === 'checkbox') continue;
          if (entry.kind === 'added') {
            entry.el.classList.add('revealed');
          } else if (this._isTouched(entry)) {
            touched.push(entry);
          } else {
            pristine.push(entry);
          }
        }
        pristine.forEach((entry) => entry.el.classList.remove('revealed'));
        if (touched.length === 0) {
          if (state.patchSig !== '') {
            state.patchLayer.innerHTML = '';
            state.patchSig = '';
          }
          return;
        }
        if (!state.cleanCanvas) {
          // Not shown yet: the original stays in place until its text-free copy is ready.
          this._ensureCleanCanvas(state);
          return;
        }
        const rects = touched.map((entry) => {
          const rect = this._patchRect(entry, state.scale);
          const x = Math.max(0, Math.floor(rect.x));
          const y = Math.max(0, Math.floor(rect.y));
          const right = Math.min(state.cleanCanvas.width, Math.ceil(rect.x + rect.w));
          const bottom = Math.min(state.cleanCanvas.height, Math.ceil(rect.y + rect.h));
          return { x, y, w: right - x, h: bottom - y };
        }).filter((rect) => rect.w > 0 && rect.h > 0);
        const signature = rects.map((rect) => `${rect.x},${rect.y},${rect.w},${rect.h}`).join(';');
        if (signature !== state.patchSig) {
          state.patchSig = signature;
          state.patchLayer.innerHTML = '';
          for (const rect of rects) {
            const patch = document.createElement('canvas');
            patch.width = rect.w;
            patch.height = rect.h;
            patch.style.left = `${rect.x}px`;
            patch.style.top = `${rect.y}px`;
            patch.getContext('2d').drawImage(state.cleanCanvas, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
            state.patchLayer.appendChild(patch);
          }
        }
        touched.forEach((entry) => {
          if (entry.el.isConnected) entry.el.classList.add('revealed');
        });
      }

      async _extractAndBuildLiveDomNodes(state, capturedGlyphColors) {
        const textContent = await state.pdfPage.getTextContent();
        const styles = textContent.styles || {};
        const viewport = state.viewport;
        const scale = state.scale;

        const rawSegments = [];
        const underscoreSegments = [];
        const createdLineRects = [];

        for (const item of textContent.items) {
          if (!item.str || !item.str.trim()) continue;

          const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
          const fontH = Math.hypot(tx[2], tx[3]);
          const startX = tx[4];
          const baselineY = tx[5];
          const topY = baselineY - fontH * 0.82;
          const totalWidth = item.width * scale;

          const st = styles[item.fontName] || {};
          // pdf.js gives only a generic family here ("sans-serif"). The font's own name
          // (e.g. "Arial-BoldItalicMT") tells the real family, bold and italic.
          const loadedFont = state.pdfPage.commonObjs && state.pdfPage.commonObjs.has(item.fontName)
            ? state.pdfPage.commonObjs.get(item.fontName)
            : null;
          const realFontName = loadedFont && loadedFont.name ? String(loadedFont.name).toLowerCase() : '';
          const fam = `${st.fontFamily || ''} ${realFontName}`.toLowerCase();
          const isBold = /bold|black|heavy|semibold|demi/.test(fam);
          const isItalic = /italic|oblique/.test(fam);
          const isSans = /sans|helvetica|arial|calibri|verdana|tahoma|gothic|segoe|carlito|arimo/.test(fam);
          const fallbackFont = isSans
            ? 'Helvetica, Arial, sans-serif'
            : 'Times New Roman, Times, serif';

          let hexColor = '#000000';
          const matchedColor = capturedGlyphColors.find(
            c => Math.abs(c.x - startX) < 18 && Math.abs(c.y - baselineY) < 12
          );
          if (matchedColor && typeof matchedColor.color === 'string' && matchedColor.color.startsWith('#')) {
            hexColor = matchedColor.color;
          }

          // MUHIM: Hatto bitta '_' yoki ketma-ket '___' bo'lsa ham alohida ajratamiz!
          const parts = item.str.split(/(_{1,}|\[\s*\]|\s{2,}|[\u2610\u2611\u2612\u25A1\u25A0☐☑☒])/g).filter(Boolean);

          this.measureCtx.font = `${isBold ? 'bold ' : ''}${isItalic ? 'italic ' : ''}${fontH}px ${fallbackFont}`;
          const partMeasures = parts.map(p => Math.max(1, this.measureCtx.measureText(p).width));
          const sumMeasured = partMeasures.reduce((a, b) => a + b, 0) || 1;
          const ratio = totalWidth > 0 ? (totalWidth / sumMeasured) : 1;

          let cursorX = startX;
          for (let i = 0; i < parts.length; i++) {
            const tokenStr = parts[i];
            const tokenW = partMeasures[i] * ratio;
            const pdfOriginX = item.transform[4] + ((cursorX - startX) / scale);
            const pdfOriginY = item.transform[5];

            if (/^_+$/.test(tokenStr)) {
              // Ketma-ket alohida '_' belgilarini birlashtirish uchun yig'amiz
              underscoreSegments.push({
                x: cursorX,
                topY,
                baselineY,
                w: tokenW,
                h: fontH,
                fallbackFont,
                pdfFontSize: fontH / scale
              });
            } else if (/^(\[\s*\]|[\u2610\u2611\u2612\u25A1\u25A0☐☑☒])$/.test(tokenStr.trim())) {
              const boxSize = Math.min(fontH * 0.82, 14 * scale);
              const boxY = baselineY - boxSize;

              this._createAutoCheckbox(state, cursorX, boxY - 1, boxSize + 2, boxSize + 2);
            } else if (!/^\s+$/.test(tokenStr)) {
              const trimLeading = tokenStr.match(/^\s+/);
              const leadW = trimLeading ? this.measureCtx.measureText(trimLeading[0]).width * ratio : 0;
              const cleanStr = tokenStr.trim();
              const cleanW = Math.max(8, this.measureCtx.measureText(cleanStr).width * ratio);

              rawSegments.push({
                str: cleanStr,
                x: cursorX + leadW,
                topY,
                baselineY,
                width: cleanW,
                height: fontH,
                pdfOriginX: pdfOriginX + (leadW / scale),
                pdfOriginY,
                pdfFontSize: fontH / scale,
                fallbackFont,
                isBold,
                isItalic,
                color: hexColor
              });
            }

            cursorX += tokenW;
          }
        }

        // Ketma-ket kelgan '_' belgilarini bitta yaxlit chiziqqa birlashtirish
        underscoreSegments.sort((a, b) => (Math.abs(a.baselineY - b.baselineY) < 3 ? a.x - b.x : a.baselineY - b.baselineY));
        const mergedUnderscores = [];
        for (const u of underscoreSegments) {
          const prev = mergedUnderscores[mergedUnderscores.length - 1];
          if (prev && Math.abs(prev.baselineY - u.baselineY) < 3 && (u.x - (prev.x + prev.w)) < u.h * 0.8) {
            prev.w = (u.x + u.w) - prev.x;
          } else {
            mergedUnderscores.push({ ...u });
          }
        }

        for (const uLine of mergedUnderscores) {
          if (uLine.w >= 14 * scale) {
            const blank = this._createEditableLineWidget(
              state,
              uLine.x,
              uLine.topY,
              uLine.w,
              uLine.h * 0.96,
              uLine.fallbackFont,
              uLine.pdfFontSize,
              '#1e293b'
            );
            this._register(blank, state, 'underscore', '', uLine.x, uLine.topY, uLine.w, uLine.h * 0.96, blank.querySelector('.line-text-input'));
            createdLineRects.push({ x: uLine.x, y: uLine.baselineY, w: uLine.w });
          }
        }

        // Matn bo'laklarini tartiblash va faqat bitta probel masofasidagilarni birlashtirish
        rawSegments.sort((a, b) => (Math.abs(a.baselineY - b.baselineY) < 3 ? a.x - b.x : a.baselineY - b.baselineY));
        const coalesced = [];
        for (const seg of rawSegments) {
          const prev = coalesced[coalesced.length - 1];
          const gap = prev ? (seg.x - (prev.x + prev.width)) : 999;

          if (
            prev &&
            Math.abs(prev.baselineY - seg.baselineY) < 3.0 &&
            gap >= -2 &&
            gap < seg.height * 0.42
          ) {
            if (gap > seg.height * 0.12 && !prev.str.endsWith(' ') && !seg.str.startsWith(' ')) {
              prev.str += ' ';
            }
            prev.str += seg.str;
            prev.width = (seg.x + seg.width) - prev.x;
          } else {
            coalesced.push({ ...seg });
          }
        }

        for (const ln of coalesced) {
          const div = document.createElement('div');
          div.className = 'sejda-line';
          div.contentEditable = 'true';
          div.spellcheck = false;
          div.textContent = ln.str;

          div.dataset.origText = ln.str;
          div.dataset.originX = ln.pdfOriginX;
          div.dataset.originY = ln.pdfOriginY;
          div.dataset.initLeft = ln.x;
          div.dataset.initTop = ln.topY;
          div.dataset.dx = '0';
          div.dataset.dy = '0';
          div.dataset.fontSize = ln.pdfFontSize.toFixed(2);
          div.dataset.fontFamily = ln.fallbackFont;
          div.dataset.bold = ln.isBold ? 'true' : 'false';
          div.dataset.italic = ln.isItalic ? 'true' : 'false';
          div.dataset.color = ln.color;

          div.style.left = `${ln.x}px`;
          div.style.top = `${ln.topY}px`;
          div.style.fontSize = `${ln.height}px`;
          div.style.fontFamily = ln.fallbackFont;
          div.style.fontWeight = ln.isBold ? 'bold' : 'normal';
          div.style.fontStyle = ln.isItalic ? 'italic' : 'normal';
          div.style.color = ln.color;
          this._register(div, state, 'line', ln.str, ln.x, ln.topY, ln.width, ln.height);

          div.addEventListener('mousedown', (e) => {
            if (this.activeTool !== 'text') return;
            e.stopPropagation();
            this.deselectAll();
          });

          div.addEventListener('focus', () => {
            this.focusedNode = div;
            this.selectedLineObj = null;
            this._showLineControlsInBar(false);
            this._positionBar(this.textBar, div);
          });

          div.addEventListener('input', () => {
            this._recordModification(div);
          });

          state.domLayer.appendChild(div);
        }

        return createdLineRects;
      }

      /**
       * JONLI BOSHQARILADIGAN CHIZIQ VIDJETI (.sejda-editable-line)
       * - Canvas'dan butunlay ajratilgan: qisqartirsangiz, uzaytirsangiz yoki o'chirsangiz,
       *   tagida eski chiziq qolmaydi!
       */
      _createEditableLineWidget(state, x, topY, width, height, fontFamily, pdfFontSize, strokeColor = '#1e293b') {
        const wrap = document.createElement('div');
        wrap.className = 'sejda-editable-line';
        wrap.style.left = `${x}px`;
        wrap.style.top = `${topY}px`;
        wrap.style.width = `${width}px`;
        wrap.style.height = `${height}px`;
        wrap.style.setProperty('--line-thickness', `${Math.max(1, state.scale * 0.75)}px`);
        wrap.style.setProperty('--stroke-color', strokeColor);

        const input = document.createElement('div');
        input.className = 'line-text-input';
        input.contentEditable = 'true';
        input.spellcheck = false;
        input.style.fontSize = `${pdfFontSize * state.scale}px`;
        input.style.fontFamily = fontFamily;
        input.dataset.fontSize = pdfFontSize.toFixed(1);
        input.dataset.fontFamily = fontFamily;
        input.dataset.bold = 'false';
        input.dataset.italic = 'false';
        input.dataset.color = '#000000';

        const lhLeft = document.createElement('div');
        lhLeft.className = 'line-handle lh-left';
        lhLeft.title = 'Chap uchini tortib chiziqni cho\'zish / qisqartirish';

        const lhRight = document.createElement('div');
        lhRight.className = 'line-handle lh-right';
        lhRight.title = 'O\'ng uchini tortib chiziqni cho\'zish / qisqartirish';

        wrap.append(lhLeft, input, lhRight);
        state.domLayer.appendChild(wrap);

        wrap.addEventListener('mousedown', (e) => {
          if (e.target.classList.contains('line-handle')) return;
          e.stopPropagation();
          this.deselectAll();
          wrap.classList.add('selected');
          this.selectedLineObj = wrap;
          this.focusedNode = input;
          this._showLineControlsInBar(true, !wrap.classList.contains('line-hidden'));
          this._positionBar(this.textBar, wrap);
        });

        input.addEventListener('focus', () => {
          wrap.classList.add('selected');
          this.selectedLineObj = wrap;
          this.focusedNode = input;
          this._showLineControlsInBar(true, !wrap.classList.contains('line-hidden'));
          this._positionBar(this.textBar, wrap);
        });

        const bindResizeHandle = (handle, isLeft) => {
          handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.deselectAll();
            wrap.classList.add('selected');
            this.selectedLineObj = wrap;
            this.focusedNode = input;
            this._showLineControlsInBar(true, !wrap.classList.contains('line-hidden'));

            const sx = e.clientX;
            const origL = parseFloat(wrap.style.left) || 0;
            const origW = wrap.offsetWidth;

            const onMove = (mv) => {
              const dx = mv.clientX - sx;
              if (isLeft) {
                const nw = origW - dx;
                if (nw >= 14) {
                  wrap.style.left = `${origL + dx}px`;
                  wrap.style.width = `${nw}px`;
                }
              } else {
                const nw = Math.max(14, origW + dx);
                wrap.style.width = `${nw}px`;
              }
              this._positionBar(this.textBar, wrap);
            };
            const onUp = () => {
              window.removeEventListener('mousemove', onMove);
              window.removeEventListener('mouseup', onUp);
            };
            window.addEventListener('mousemove', onMove);
            window.addEventListener('mouseup', onUp);
          });
        };

        bindResizeHandle(lhLeft, true);
        bindResizeHandle(lhRight, false);
        return wrap;
      }

      _showLineControlsInBar(show, isLineVisible = true) {
        const disp = show ? 'inline-flex' : 'none';
        document.getElementById('line-tools-sep').style.display = show ? 'block' : 'none';
        document.getElementById('btn-line-shorter').style.display = disp;
        document.getElementById('btn-line-longer').style.display = disp;
        const btnToggle = document.getElementById('btn-toggle-line');
        btnToggle.style.display = disp;
        btnToggle.classList.toggle('active', isLineVisible);
      }

      _createAutoCheckbox(state, x, topY, w, h) {
        // The same box can be found twice (as a glyph and as a drawn square): one is enough.
        const twin = [...state.domLayer.querySelectorAll('.sejda-auto-checkbox')].some((node) =>
          Math.abs((parseFloat(node.style.left) || 0) - x) < 4 && Math.abs((parseFloat(node.style.top) || 0) - topY) < 4);
        if (twin) return;
        const box = document.createElement('div');
        box.className = 'sejda-auto-checkbox';
        box.style.left = `${x}px`;
        box.style.top = `${topY}px`;
        box.style.width = `${w}px`;
        box.style.height = `${h}px`;
        box.style.fontSize = `${h * 0.9}px`;
        box.title = 'Belgilash uchun bosing (✓)';

        box.addEventListener('mousedown', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const before = box.textContent;
          if (this.activeTool === 'cross') {
            box.textContent = box.textContent === '✕' ? '' : '✕';
          } else {
            box.textContent = box.textContent ? '' : '✓';
          }
          this._pushUndo({ type: 'checkbox-toggle', node: box, before });
        });

        state.domLayer.appendChild(box);
        this._register(box, state, 'checkbox', '', x, topY, w, h);
      }

      /*
       * EDIT REGISTRY. Every text line, blank and checkbox the page shows, with
       * where it sits in the PDF (points, unrotated, relative to the crop box).
       * The save turns what changed here into the server's text operations.
       */
      _register(el, state, kind, origText, x, topY, width, height, textEl = null) {
        if (!this.registry) this.registry = new Map();
        this.overlaySeq = (this.overlaySeq || 0) + 1;
        const id = `ov${this.overlaySeq}`;
        el.dataset.lid = id;
        this.registry.set(id, {
          el,
          textEl: textEl || el,
          kind,
          origText,
          state,
          pt: this._pageRect(state, x, topY, width, height),
          px: { x, top: topY, w: width, h: height },
          styleSig: kind === 'line' ? this._styleSignature(el) : undefined,
        });
      }

      _pageRect(state, x, topY, width, height) {
        const view = state.pdfPage.view;
        const a = state.viewport.convertToPdfPoint(x, topY);
        const b = state.viewport.convertToPdfPoint(x + width, topY + height);
        const left = Math.min(a[0], b[0]) - view[0];
        const right = Math.max(a[0], b[0]) - view[0];
        const top = view[3] - Math.max(a[1], b[1]);
        const bottom = view[3] - Math.min(a[1], b[1]);
        return { x: left, y: top, w: right - left, h: bottom - top };
      }

      _dropPageEntries(state) {
        if (!this.registry) return;
        for (const [id, entry] of this.registry) {
          if (entry.state === state) this.registry.delete(id);
        }
      }

      // Did the person move, hide or resize one of this page's line widgets?
      _geometryChanged(entry) {
        const el = entry.el;
        const left = parseFloat(el.style.left) || 0;
        const top = parseFloat(el.style.top) || 0;
        const width = parseFloat(el.style.width) || 0;
        return Math.abs(left - entry.px.x) > 0.5
          || Math.abs(top - entry.px.top) > 0.5
          || Math.abs(width - entry.px.w) > 0.5;
      }

      // Changes a rebuilt page could not carry over (zoom and rotate rebuild the page).
      _hasDomOnlyChanges(state) {
        // Added text, ✓ / ✕ and whiteout are carried over (`collectPageMarkup`); anything else is not.
        if (state.domLayer.querySelector('.added-obj:not([data-type="added-text"]):not([data-type="check"]):not([data-type="cross"]), .sejda-box-item:not([data-type="whiteout"])')) return true;
        for (const entry of (this.registry || new Map()).values()) {
          if (entry.state !== state) continue;
          if (entry.kind === 'line') {
            const moved = Math.abs(parseFloat(entry.el.dataset.dx) || 0) > 0.001
              || Math.abs(parseFloat(entry.el.dataset.dy) || 0) > 0.001;
            if (moved) return true;
          } else if (entry.kind !== 'checkbox') {
            if (entry.el.classList.contains('line-hidden') || this._geometryChanged(entry)) return true;
          }
        }
        return false;
      }

      // The changes a page carries over a rebuild: changed lines, erased lines, checkbox states.
      _snapshotPage(state) {
        const items = [];
        for (const entry of (this.registry || new Map()).values()) {
          if (entry.state !== state) continue;
          const erased = !entry.el.isConnected;
          const value = erased ? '' : entry.textEl.textContent;
          if (!erased && value === entry.origText) continue;
          items.push({
            kind: entry.kind,
            origText: entry.origText,
            cx: entry.pt.x + entry.pt.w / 2,
            cy: entry.pt.y + entry.pt.h / 2,
            erased,
            value,
          });
        }
        return items;
      }

      // Puts a snapshot back on the rebuilt page: the same kind and text, at the same place in points.
      _restorePage(state, items) {
        let lost = 0;
        for (const item of items) {
          let match = null;
          let best = 3;
          for (const entry of this.registry.values()) {
            if (entry.state !== state || entry.kind !== item.kind || entry.origText !== item.origText) continue;
            const distance = Math.hypot(entry.pt.x + entry.pt.w / 2 - item.cx, entry.pt.y + entry.pt.h / 2 - item.cy);
            if (distance < best) {
              best = distance;
              match = entry;
            }
          }
          if (!match) {
            lost += 1;
          } else if (item.erased) {
            match.el.remove();
          } else if (match.kind === 'checkbox') {
            match.el.textContent = item.value;
          } else {
            match.textEl.textContent = item.value;
          }
        }
        if (lost > 0) {
          alert(`${lost} ta o‘zgarish sahifa qayta chizilganda tiklanmadi. Iltimos, tekshirib qayta kiriting.`);
        }
      }

      /**
       * Page changes on a contract, saved with the text changes in one draft:
       * a turn (absolute /Rotate), a deleted page, a blank page inserted after one.
       */
      pagePlan() {
        const plan = [];
        this.pages.forEach((state) => {
          if (state.deleted) {
            plan.push({ type: 'delete', pageIndex: state.pageIndex });
            return;
          }
          const own = (((state.pdfPage.rotate || 0) % 360) + 360) % 360;
          if (state.rotation !== own) plan.push({ type: 'rotate', pageIndex: state.pageIndex, rotation: state.rotation });
          for (let n = 0; n < (state.insertsAfter || 0); n += 1) plan.push({ type: 'insert', afterIndex: state.pageIndex });
        });
        return plan;
      }

      _hasPendingChanges() {
        const plan = this.collectEdits();
        return plan.edits.length > 0 || plan.unsupported.length > 0;
      }

      /*
       * What the person changed, for the save. `edits` is the changed text;
       * `unsupported` lists the changes the save cannot write yet. Any of them
       * blocks the save, so nothing changes without the person knowing.
       */
      collectEdits() {
        const edits = [];
        const unsupported = [];
        const place = (state) => `Sahifa ${state.pageIndex + 1}`;
        const change = (entry, newText, blank) => ({
          pageIndex: entry.state.pageIndex,
          ptX: entry.pt.x,
          ptY: entry.pt.y,
          ptW: entry.pt.w,
          ptH: entry.pt.h,
          origText: blank ? '' : entry.origText,
          newText,
          blank,
        });
        for (const entry of (this.registry || new Map()).values()) {
          const { el, kind, state } = entry;
          const erased = !el.isConnected;
          const text = erased ? '' : entry.textEl.textContent.replace(/ /g, ' ');
          if (kind === 'checkbox') {
            // ✓ / ✕ in a box is saved as markup (`collectPageMarkup`).
          } else if (kind === 'line') {
            const moved = Math.abs(parseFloat(el.dataset.dx) || 0) > 0.001
              || Math.abs(parseFloat(el.dataset.dy) || 0) > 0.001;
            if (erased) edits.push(change(entry, '', false));
            else if (moved) {
              // A moved line is written at its new place (points; down and right are positive).
              edits.push({
                ...change(entry, text, false),
                dxPt: parseFloat(el.dataset.dx) || 0,
                dyPt: parseFloat(el.dataset.dy) || 0,
              });
            } else if (text !== entry.origText) edits.push(change(entry, text, false));
          } else if (kind === 'added') {
            // Text typed on it is saved as markup (`collectPageMarkup`), not here.
          } else {
            // 'underscore' is a blank in the text; 'vector' is a line drawn in the page.
            if (!erased && (el.classList.contains('line-hidden') || this._geometryChanged(entry))) {
              unsupported.push(`${place(state)}: chiziqni yashirish yoki o‘zgartirish hozircha saqlanmaydi.`);
            }
            if (kind === 'vector' && erased) {
              unsupported.push(`${place(state)}: chiziqni o‘chirish hozircha saqlanmaydi.`);
            } else if (kind === 'underscore' && erased) {
              edits.push(change(entry, '', true));
            }
            // Text typed on a blank is saved as markup (`collectPageMarkup`), not as a text change.
          }
        }
        for (const state of this.pages) {
          const turned = state.rotation !== (((state.pdfPage.rotate || 0) % 360) + 360) % 360;
          if (turned && !this.contractMode) {
            unsupported.push(`${place(state)}: sahifa burilgan — burish hozircha saqlanmaydi (0° ga qaytaring).`);
          }
          if (turned && this.collectPageMarkup(state).length) {
            unsupported.push(`${place(state)}: burilgan sahifaga qo‘shilgan matn va belgilar hozircha saqlanmaydi — avval sahifani saqlang, so‘ng qo‘shing.`);
          }
          // Added text, ✓ / ✕ and whiteout are saved as markup; other added objects are not yet.
          if (state.domLayer.querySelector('.added-obj:not([data-type="added-text"]):not([data-type="check"]):not([data-type="cross"]), .sejda-box-item:not([data-type="whiteout"])')) {
            unsupported.push(`${place(state)}: qo‘shilgan matn yoki shakl hozircha saqlanmaydi.`);
          }
        }
        if (this.structureChanged && !this.contractMode) {
          unsupported.push('Sahifalar qo‘shilgan yoki o‘chirilgan — bu hozircha saqlanmaydi.');
        }
        return { edits, unsupported: [...new Set(unsupported)] };
      }

      _recordModification(node) {
        if (!node.classList.contains('modified')) {
          node.classList.add('modified');
          this._pushUndo({
            type: 'edit',
            node,
            origText: node.dataset.origText,
            initLeft: node.dataset.initLeft,
            initTop: node.dataset.initTop
          });
        }
      }

      _positionBar(barEl, targetNode) {
        const r = targetNode.getBoundingClientRect();
        barEl.classList.add('visible');
        // Above the text with a clear gap; under it when there is no room
        // above (below the header). The bar never lies on the text itself.
        const header = document.querySelector('.top-header');
        const floor = (header ? header.getBoundingClientRect().bottom : 0) + 6;
        const barH = barEl.offsetHeight || 36;
        const barW = barEl.offsetWidth || 520;
        const above = r.top - barH - 14;
        barEl.style.top = `${above >= floor ? above : Math.min(window.innerHeight - barH - 6, r.bottom + 14)}px`;
        barEl.style.left = `${Math.max(12, Math.min(window.innerWidth - barW - 12, r.left))}px`;
        if (barEl === this.textBar) this.barTarget = targetNode;
        if (barEl === this.textBar && this.grip) {
          this.grip.classList.add('visible');
          this.grip.style.left = `${Math.max(2, r.left - 22)}px`;
          this.grip.style.top = `${r.top + r.height / 2 - 11}px`;
        }

        if (barEl === this.textBar && this.focusedNode) {
          barEl.querySelector('[data-cmd="bold"]').classList.toggle('active', this.focusedNode.dataset.bold === 'true');
          barEl.querySelector('[data-cmd="italic"]').classList.toggle('active', this.focusedNode.dataset.italic === 'true');
          barEl.querySelector('[data-cmd="size"]').value = Math.round(parseFloat(this.focusedNode.dataset.fontSize) || 11);
          barEl.querySelector('[data-cmd="color"]').value = this.focusedNode.dataset.color || '#000000';
        } else if (barEl === this.boxBar && this.selectedBox) {
          barEl.querySelector('[data-box="fill"]').value = this.selectedBox.dataset.fillColor || '#ffffff';
          barEl.querySelector('[data-box="has-border"]').checked = this.selectedBox.dataset.hasBorder === 'true';
          barEl.querySelector('[data-box="stroke"]').value = this.selectedBox.dataset.strokeColor || '#000000';
        }
      }

      _toggleFormat(prop) {
        if (!this.focusedNode) return;
        const nextState = this.focusedNode.dataset[prop] !== 'true';
        this.focusedNode.dataset[prop] = nextState ? 'true' : 'false';
        if (prop === 'bold') this.focusedNode.style.fontWeight = nextState ? 'bold' : 'normal';
        if (prop === 'italic') this.focusedNode.style.fontStyle = nextState ? 'italic' : 'normal';
        this._recordModification(this.focusedNode);
        this._positionBar(this.textBar, this.selectedLineObj || this.focusedNode);
      }

      _stepFontSize(delta) {
        if (!this.focusedNode) return;
        this._applyFontSize((parseFloat(this.focusedNode.dataset.fontSize) || 11) + delta);
      }

      _applyFontSize(size) {
        if (!this.focusedNode) return;
        const pageStage = this.focusedNode.closest('.pdf-stage');
        const pageState = this.pages.find(p => p.stage === pageStage) || this.pages[0];
        const clamped = Math.max(5, Math.min(72, size));
        this.focusedNode.dataset.fontSize = clamped;
        const sc = pageState ? pageState.scale : 1.35;
        this.focusedNode.style.fontSize = `${clamped * sc}px`;
        this.textBar.querySelector('[data-cmd="size"]').value = clamped;
        this._recordModification(this.focusedNode);
      }

      _applyFontFamily(val) {
        if (!this.focusedNode) return;
        this.focusedNode.style.fontFamily = val;
        this.focusedNode.dataset.fontFamily = val;
        this._recordModification(this.focusedNode);
      }

      _applyColor(hex) {
        if (!this.focusedNode) return;
        this.focusedNode.dataset.color = hex;
        this.focusedNode.style.color = hex;
        if (this.selectedLineObj) {
          this.selectedLineObj.style.setProperty('--stroke-color', hex);
        }
        this._recordModification(this.focusedNode);
      }

      _deleteActiveNode() {
        if (this.selectedLineObj) {
          this.selectedLineObj.remove();
          this.selectedLineObj = null;
          this.textBar.classList.remove('visible');
          return;
        }
        if (!this.focusedNode) return;
        if (this.focusedNode.classList.contains('sejda-line')) {
          this._recordModification(this.focusedNode);
          this.focusedNode.remove();
        } else {
          this.focusedNode.closest('.added-obj')?.remove();
        }
        this.textBar.classList.remove('visible');
      }

      setTool(tool) {
        this.activeTool = tool;
        // Only the tool class changes: `embedded` and the others stay.
        [...document.body.classList].filter((name) => name.startsWith('tool-')).forEach((name) => document.body.classList.remove(name));
        document.body.classList.add(`tool-${tool}`);
        document.querySelectorAll('.annot-canvas').forEach(c => {
          c.classList.toggle('active', tool === 'annotate');
        });
      }

      /** Record one step for Undo. A new step means nothing undone can be redone. */
      _pushUndo(entry) {
        entry.at = Date.now();
        this.undoStack.push(entry);
        this.redoStack = [];
        if (window.SignOverlays) window.SignOverlays.localChanged();
      }

      /**
       * Take back the last change: this editor's own last edit, or the last
       * signature / date placed on the contract page, whichever is newer.
       */
      undo() {
        const last = this.undoStack[this.undoStack.length - 1];
        const localAt = last ? (last.at || 0) : -1;
        const signing = window.SignOverlays ? window.SignOverlays.undoIfNewer(localAt) : false;
        if (signing === 'busy') return;
        if (signing === 'done') {
          this.redoStack.push({ type: 'sign' });
          return;
        }
        if (!last) return;
        this.undoStack.pop();
        if (last.type === 'spawn') {
          last.parent = last.node.parentNode;
          last.next = last.node.nextSibling;
          last.node.remove();
        }
        if (last.type === 'checkbox-toggle') {
          last.after = last.node.textContent;
          last.node.textContent = last.before || '';
        }
        if (last.type === 'edit') {
          if (!last.node.isConnected && this.pages[0]) {
            this.pages[0].domLayer.appendChild(last.node);
          }
          last.redo = {
            text: last.node.textContent,
            left: last.node.style.left,
            top: last.node.style.top,
            dx: last.node.dataset.dx,
            dy: last.node.dataset.dy,
          };
          last.node.textContent = last.origText;
          last.node.style.left = `${last.initLeft}px`;
          last.node.style.top = `${last.initTop}px`;
          last.node.dataset.dx = '0';
          last.node.dataset.dy = '0';
          // The next change to this line is a new step of its own.
          last.node.classList.remove('modified');
        }
        this.redoStack.push(last);
        this.deselectAll();
        if (window.SignOverlays) window.SignOverlays.updateButtons();
      }

      /** Put back what the last Undo took back. */
      redo() {
        const next = this.redoStack.pop();
        if (!next) return;
        if (next.type === 'sign') {
          if (!window.SignOverlays || !window.SignOverlays.redo()) this.redoStack.push(next);
          return;
        }
        if (next.type === 'spawn' && next.parent) {
          next.parent.insertBefore(next.node, next.next && next.next.parentNode === next.parent ? next.next : null);
        }
        if (next.type === 'checkbox-toggle') next.node.textContent = next.after || '';
        if (next.type === 'edit' && next.redo) {
          next.node.textContent = next.redo.text;
          next.node.style.left = next.redo.left;
          next.node.style.top = next.redo.top;
          next.node.dataset.dx = next.redo.dx || '0';
          next.node.dataset.dy = next.redo.dy || '0';
          next.node.classList.add('modified');
        }
        next.at = Date.now();
        this.undoStack.push(next);
        this.deselectAll();
        if (window.SignOverlays) window.SignOverlays.updateButtons();
      }

      _attachStageEvents(state, bar) {
        const { PDFDocument } = PDFLib;

        bar.addEventListener('click', async (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;
          const act = btn.dataset.act;
          if (['zin', 'zout', 'rcw', 'rccw'].includes(act) && this._hasDomOnlyChanges(state)) {
            alert('Bu sahifada hozircha saqlanmaydigan o‘zgarishlar bor (siljitish, yashirish yoki shakl). Ularni bekor qiling, so‘ng masshtabni yoki burilishni o‘zgartiring.');
            return;
          }
          if (act === 'zin') {
            state.scale = Math.min(2.4, state.scale + 0.15);
            const carried = this.collectPageMarkup(state);
            await this._renderStage(state, true);
            this.restorePageMarkup(state, carried);
          } else if (act === 'zout') {
            state.scale = Math.max(0.6, state.scale - 0.15);
            const carried = this.collectPageMarkup(state);
            await this._renderStage(state, true);
            this.restorePageMarkup(state, carried);
          } else if (act === 'rcw') {
            const carried = this.collectPageMarkup(state);
            state.rotation = (state.rotation + 90) % 360;
            await this._renderStage(state, true);
            this.restorePageMarkup(state, carried);
          } else if (act === 'rccw') {
            const carried = this.collectPageMarkup(state);
            state.rotation = (state.rotation + 270) % 360;
            await this._renderStage(state, true);
            this.restorePageMarkup(state, carried);
          } else if (act === 'del' && this.contractMode) {
            if (this.pages.filter((page) => !page.deleted).length <= 1) return alert('Yagona sahifani o‘chirib bo‘lmaydi.');
            if (!confirm(`Sahifa ${state.pageIndex + 1} o‘chirilsinmi? «Shartnomaga saqlash» bosilganda o‘chadi.`)) return;
            state.deleted = true;
            bar.classList.add('page-pending-delete');
            state.stage.classList.add('page-pending-delete');
            bar.querySelector('.page-num-badge').title = 'Saqlanganda o‘chiriladi';
            this.structureChanged = true;
            if (window.SignOverlays) window.SignOverlays.updateButtons();
          } else if (act === 'ins' && this.contractMode) {
            state.insertsAfter = (state.insertsAfter || 0) + 1;
            const note = document.createElement('div');
            note.className = 'page-pending-insert';
            note.textContent = 'Yangi bo‘sh sahifa — «Shartnomaga saqlash» bosilganda qo‘shiladi';
            state.stage.after(note);
            this.structureChanged = true;
          } else if (act === 'del') {
            if (this._hasPendingChanges()) return alert('Avval matndagi o‘zgarishlarni saqlang yoki sahifani yangilang, so‘ng sahifalarni o‘zgartiring.');
            const doc = await PDFDocument.load(this.pdfBytes);
            if (doc.getPageCount() <= 1) return alert('Yagona sahifani o\'chirib bo\'lmaydi.');
            doc.removePage(state.pageIndex);
            await this.loadPDF(await doc.save());
            this.structureChanged = true;
          } else if (act === 'ins') {
            if (this._hasPendingChanges()) return alert('Avval matndagi o‘zgarishlarni saqlang yoki sahifani yangilang, so‘ng sahifalarni o‘zgartiring.');
            const doc = await PDFDocument.load(this.pdfBytes);
            doc.insertPage(state.pageIndex, [612, 792]);
            await this.loadPDF(await doc.save());
            this.structureChanged = true;
          }
        });

        let drawing = false;
        const aCtx = state.annotCanvas.getContext('2d');
        state.annotCanvas.onmousedown = (e) => {
          if (this.activeTool !== 'annotate') return;
          drawing = true;
          const r = state.annotCanvas.getBoundingClientRect();
          aCtx.beginPath();
          aCtx.moveTo(e.clientX - r.left, e.clientY - r.top);
          aCtx.strokeStyle = '#facc15';
          aCtx.globalAlpha = 0.45;
          aCtx.lineWidth = 14;
          aCtx.lineCap = 'round';
        };
        state.annotCanvas.onmousemove = (e) => {
          if (!drawing) return;
          const r = state.annotCanvas.getBoundingClientRect();
          aCtx.lineTo(e.clientX - r.left, e.clientY - r.top);
          aCtx.stroke();
        };
        window.addEventListener('mouseup', () => { drawing = false; });

        state.stage.addEventListener('mousedown', (e) => {
          if (e.button !== 0) return;
          if (
            e.target.closest('.added-obj') ||
            e.target.closest('.sejda-auto-checkbox') ||
            e.target.closest('.sejda-editable-line') ||
            e.target.classList.contains('rh')
          ) {
            return;
          }

          const clickedBox = e.target.closest('.sejda-box-item');
          if (clickedBox) {
            if (this.activeTool === 'text' && !clickedBox.classList.contains('selected')) {
              e.preventDefault();
              e.stopPropagation();
              this.deselectAll();
              const r = state.stage.getBoundingClientRect();
              this._spawnNewText(state, e.clientX - r.left, e.clientY - r.top - 8);
              return;
            }
            return;
          }

          if (this.activeTool === 'text' && e.target.closest('.sejda-line') && !e.altKey) {
            return;
          }

          e.preventDefault();
          this.deselectAll();

          const r = state.stage.getBoundingClientRect();
          const startX = e.clientX - r.left;
          const startY = e.clientY - r.top;

          if (this.activeTool === 'line') {
            const onUp = (upEv) => {
              window.removeEventListener('mouseup', onUp);
              const endX = upEv.clientX - r.left;
              const x = Math.min(startX, endX);
              const w = Math.max(70, Math.abs(endX - startX));
              const lineH = 14 * state.scale;
              const widget = this._createEditableLineWidget(
                state,
                x,
                startY - lineH + 2,
                w,
                lineH,
                'Times New Roman, Times, serif',
                10.5
              );
              this._register(widget, state, 'added', '', x, startY - lineH + 2, w, lineH, widget.querySelector('.line-text-input'));
              this._pushUndo({ type: 'spawn', node: widget });
              const inp = widget.querySelector('.line-text-input');
              if (inp) inp.focus();
            };
            window.addEventListener('mouseup', onUp);
            return;
          }

          if (this.activeTool === 'whiteout' || this.activeTool === 'shape' || this.activeTool === 'link') {
            const preview = document.createElement('div');
            preview.className = 'rubberband-preview';
            if (this.activeTool === 'shape') {
              preview.style.background = 'transparent';
              preview.style.border = '2px solid #000000';
            } else if (this.activeTool === 'link') {
              preview.style.background = 'rgba(43, 136, 217, 0.14)';
            }
            preview.style.left = `${startX}px`;
            preview.style.top = `${startY}px`;
            preview.style.width = '0px';
            preview.style.height = '0px';
            state.domLayer.appendChild(preview);

            const onDrag = (mv) => {
              const curX = mv.clientX - r.left;
              const curY = mv.clientY - r.top;
              preview.style.left = `${Math.min(startX, curX)}px`;
              preview.style.top = `${Math.min(startY, curY)}px`;
              preview.style.width = `${Math.abs(curX - startX)}px`;
              preview.style.height = `${Math.abs(curY - startY)}px`;
            };

            const onEnd = (upEv) => {
              window.removeEventListener('mousemove', onDrag);
              window.removeEventListener('mouseup', onEnd);
              preview.remove();

              const curX = upEv.clientX - r.left;
              const curY = upEv.clientY - r.top;
              let x = Math.min(startX, curX);
              let y = Math.min(startY, curY);
              let w = Math.abs(curX - startX);
              let h = Math.abs(curY - startY);

              if (w < 12 && h < 12) {
                w = this.activeTool === 'shape' ? 120 : 150;
                h = this.activeTool === 'shape' ? 60 : 26;
                y = Math.max(0, startY - h / 2);
              }

              if (this.activeTool === 'link') {
                const url = prompt('Havola (URL) manzilini kiriting:', 'https://');
                if (!url) return;
                const box = this._createResizableBox(state, x, y, w, h, 'link');
                box.dataset.url = url;
                this._selectBox(box);
              } else {
                const box = this._createResizableBox(state, x, y, w, h, this.activeTool);
                this._selectBox(box);
              }
            };

            window.addEventListener('mousemove', onDrag);
            window.addEventListener('mouseup', onEnd);
            return;
          }

          if (this.activeTool === 'text') {
            this._spawnNewText(state, startX, startY - 8);
          } else if (this.activeTool === 'check' || this.activeTool === 'cross') {
            this._spawnMark(state, this.activeTool, startX, startY, 14 * state.scale);
          } else if (this.activeTool === 'field') {
            const el = this._createObjWrap(state, startX, startY - 12, 165, 22, 'field');
            const inp = document.createElement('input');
            inp.type = 'text';
            inp.placeholder = 'Matn kiriting...';
            inp.style.cssText = 'width:100%;height:100%;border:1px solid #64748b;background:rgba(241,245,249,0.85);padding:2px 6px;font-size:13px;outline:none;';
            el.appendChild(inp);
            inp.focus();
          } else if (this.activeTool === 'sign' && editorIsEmbedded()) {
            // On the contract page a signature is the contract's own (saved,
            // printed at finalize): the page opens its signature dialog for
            // this spot -- saved, drawn, uploaded or typed.
            const at = this._pageRect(state, startX, startY, 0, 0);
            window.parent.postMessage(
              { type: 'editor:sign-at', page: state.pageIndex + 1, x: at.x, y: at.y },
              window.location.origin,
            );
          } else if (this.activeTool === 'sign' && this.pendingSignature) {
            this._spawnImage(state, startX - 65, startY - 28, this.pendingSignature, 145, 55);
          } else if (this.activeTool === 'image' && this.pendingImage) {
            this._spawnImage(state, startX - 65, startY - 45, this.pendingImage, 150, 100);
          }
        });
      }

      _createResizableBox(state, x, y, w, h, type, quiet = false) {
        const box = document.createElement('div');
        box.className = 'sejda-box-item';
        box.dataset.type = type;
        box.style.left = `${x}px`;
        box.style.top = `${y}px`;
        box.style.width = `${w}px`;
        box.style.height = `${h}px`;

        if (type === 'whiteout') {
          box.style.backgroundColor = '#ffffff';
          box.dataset.fillColor = '#ffffff';
          box.dataset.hasBorder = 'false';
          box.dataset.strokeColor = '#000000';
        } else if (type === 'shape') {
          box.style.backgroundColor = 'transparent';
          box.style.border = '1.5px solid #000000';
          box.dataset.fillColor = '#ffffff';
          box.dataset.hasBorder = 'true';
          box.dataset.strokeColor = '#000000';
        } else if (type === 'link') {
          box.style.backgroundColor = 'rgba(43, 136, 217, 0.12)';
          box.style.border = '1px dashed #2b88d9';
          box.dataset.fillColor = '#ffffff';
          box.dataset.hasBorder = 'false';
          box.dataset.strokeColor = '#2b88d9';
        }

        ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].forEach(dir => {
          const handle = document.createElement('div');
          handle.className = `rh rh-${dir}`;
          handle.dataset.dir = dir;
          box.appendChild(handle);
        });

        state.domLayer.appendChild(box);
        this._bindBoxDragAnd8WayResize(box);
        if (!quiet) this._pushUndo({ type: 'spawn', node: box });
        return box;
      }

      _selectBox(box) {
        this.deselectAll();
        box.classList.add('selected');
        this.selectedBox = box;
        this._positionBar(this.boxBar, box);
      }

      _bindBoxDragAnd8WayResize(box) {
        box.addEventListener('mousedown', (e) => {
          if (this.activeTool === 'text' && !box.classList.contains('selected') && !e.target.classList.contains('rh')) {
            return;
          }

          e.preventDefault();
          e.stopPropagation();
          this._selectBox(box);

          const startX = e.clientX;
          const startY = e.clientY;
          const origL = parseFloat(box.style.left) || 0;
          const origT = parseFloat(box.style.top) || 0;
          const origW = box.offsetWidth;
          const origH = box.offsetHeight;
          const dir = e.target.dataset.dir;

          const onMove = (mv) => {
            const dx = mv.clientX - startX;
            const dy = mv.clientY - startY;

            if (!dir) {
              box.style.left = `${origL + dx}px`;
              box.style.top = `${origT + dy}px`;
            } else {
              let newL = origL, newT = origT, newW = origW, newH = origH;
              if (dir.includes('e')) newW = Math.max(14, origW + dx);
              if (dir.includes('s')) newH = Math.max(10, origH + dy);
              if (dir.includes('w')) {
                const pw = origW - dx;
                if (pw >= 14) { newW = pw; newL = origL + dx; }
              }
              if (dir.includes('n')) {
                const ph = origH - dy;
                if (ph >= 10) { newH = ph; newT = origT + dy; }
              }
              box.style.left = `${newL}px`;
              box.style.top = `${newT}px`;
              box.style.width = `${newW}px`;
              box.style.height = `${newH}px`;
            }
            this._positionBar(this.boxBar, box);
          };

          const onUp = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
          };

          window.addEventListener('mousemove', onMove);
          window.addEventListener('mouseup', onUp);
        });
      }

      _createObjWrap(state, x, y, w, h, type, quiet = false) {
        const el = document.createElement('div');
        el.className = quiet ? 'added-obj' : 'added-obj selected';
        el.dataset.type = type;
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        if (w) el.style.width = `${w}px`;
        if (h) el.style.height = `${h}px`;

        state.domLayer.appendChild(el);
        this._makeAddedObjDraggable(el);
        if (!quiet) this._pushUndo({ type: 'spawn', node: el });
        return el;
      }

      /* ---------------------------------------------------------------------
       * ADDED MARKS <-> CONTRACT MARKUP (js/markup-bridge.js). What the person
       * adds to a page is described in points and saved as the contract's
       * markup; the same description carries the marks over a zoom.
       * ------------------------------------------------------------------- */

      _toPx(state, xPt, yPt) {
        const view = state.pdfPage.view;
        const p = state.viewport.convertToViewportPoint(view[0] + xPt, view[3] - yPt);
        return { x: p[0], y: p[1] };
      }

      // Where the first line of a text element sits: its left end on the baseline, stage px.
      _baselineOf(el, state) {
        const probe = document.createElement('span');
        probe.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline;padding:0;margin:0;';
        el.insertBefore(probe, el.firstChild);
        const r = probe.getBoundingClientRect();
        probe.remove();
        const s = state.stage.getBoundingClientRect();
        return { x: r.left - s.left, y: r.top - s.top };
      }

      _markupIdOf(node) {
        if (!node.dataset.markupId) node.dataset.markupId = window.MarkupBridge.newId();
        return node.dataset.markupId;
      }

      _textElement(state, node, textEl, text) {
        const B = window.MarkupBridge;
        const at = this._baselineOf(textEl, state);
        const pt = this._pageRect(state, at.x, at.y, 0, 0);
        const element = {
          id: this._markupIdOf(node),
          kind: 'text',
          x_pt: B.round(pt.x),
          y_pt: B.round(pt.y),
          text,
          font: B.fontToWire(textEl.dataset.fontFamily || textEl.style.fontFamily),
          size_pt: B.round(B.clamp(parseFloat(textEl.dataset.fontSize) || 11, 6, 48)),
        };
        if (textEl.dataset.bold === 'true') element.bold = true;
        if (textEl.dataset.italic === 'true') element.italic = true;
        const color = B.colorToWire(textEl.dataset.color || '#000000');
        if (color !== 'black') element.color = color;
        return element;
      }

      /** Every mark this page adds, as markup elements (no page: the caller knows it). */
      collectPageMarkup(state) {
        // Not drawn yet: its saved marks are still exactly as loaded.
        if (!state.rendered) return [...((this.pendingMarkup && this.pendingMarkup.get(state.pageIndex)) || [])];
        const B = window.MarkupBridge;
        const out = [];
        const clean = (value) => (value || '').replace(/\u00A0/g, ' ').replace(/\s*\n\s*/g, ' ').trim();
        state.domLayer.querySelectorAll('.added-obj[data-type="added-text"]').forEach((wrap) => {
          const ed = wrap.querySelector('.added-text-editor');
          const text = ed ? clean(ed.textContent) : '';
          if (text) out.push(this._textElement(state, wrap, ed, text.slice(0, 200)));
        });
        // Text typed on a blank (a rule of the page, or "____" in its text).
        for (const entry of (this.registry || new Map()).values()) {
          if (entry.state !== state || !entry.el.isConnected) continue;
          if (entry.kind !== 'underscore' && entry.kind !== 'vector' && entry.kind !== 'added') continue;
          const text = clean(entry.textEl.textContent);
          if (text) out.push(this._textElement(state, entry.el, entry.textEl, text.slice(0, 200)));
        }
        const markAt = (node, cx, cy, px, kind) => {
          const pt = this._pageRect(state, cx, cy, 0, 0);
          out.push({
            id: this._markupIdOf(node),
            kind,
            x_pt: B.round(pt.x),
            y_pt: B.round(pt.y),
            size_pt: B.round(B.clamp(px / state.scale * 0.75, 6, 72)),
          });
        };
        state.domLayer.querySelectorAll('.added-obj[data-type="check"], .added-obj[data-type="cross"]').forEach((el) => {
          const left = parseFloat(el.style.left) || 0;
          const top = parseFloat(el.style.top) || 0;
          markAt(el, left + el.offsetWidth / 2, top + el.offsetHeight / 2, Math.min(el.offsetWidth, el.offsetHeight), el.dataset.type);
        });
        state.domLayer.querySelectorAll('.sejda-auto-checkbox').forEach((box) => {
          const mark = box.textContent.trim();
          if (mark !== '✓' && mark !== '✕') return;
          const left = parseFloat(box.style.left) || 0;
          const top = parseFloat(box.style.top) || 0;
          const w = parseFloat(box.style.width) || box.offsetWidth;
          const h = parseFloat(box.style.height) || box.offsetHeight;
          markAt(box, left + w / 2, top + h / 2, Math.min(w, h) * 1.05, mark === '✓' ? 'check' : 'cross');
        });
        state.domLayer.querySelectorAll('.sejda-box-item[data-type="whiteout"]').forEach((box) => {
          const rect = this._pageRect(state, parseFloat(box.style.left) || 0, parseFloat(box.style.top) || 0, box.offsetWidth, box.offsetHeight);
          if (rect.w < 1 || rect.h < 1) return;
          out.push({ id: this._markupIdOf(box), kind: 'whiteout', x_pt: B.round(rect.x), y_pt: B.round(rect.y), w_pt: B.round(rect.w), h_pt: B.round(rect.h) });
        });
        // A mark the person did not move or change keeps the exact values it
        // was loaded with: a zoom's rounding is not a change to save.
        return out.map((fresh) => {
          const node = state.domLayer.querySelector(`[data-markup-id="${fresh.id}"]`);
          if (!node || !node.dataset.markupOrig) return fresh;
          let original;
          try {
            original = JSON.parse(node.dataset.markupOrig);
          } catch (error) {
            return fresh;
          }
          const keys = new Set([...Object.keys(original), ...Object.keys(fresh)]);
          keys.delete('page_uid');
          for (const key of keys) {
            const a = original[key];
            const b = fresh[key];
            if (typeof a === 'number' && typeof b === 'number') {
              if (Math.abs(a - b) > 1.5) return fresh;
            } else if (a !== b) {
              return fresh;
            }
          }
          const { page_uid: ignored, ...kept } = original;
          return kept;
        });
      }

      /** [{ pageIndex, element }] for every page. */
      collectMarkup() {
        const all = [];
        this.pages.forEach((state) => {
          this.collectPageMarkup(state).forEach((element) => all.push({ pageIndex: state.pageIndex, element }));
        });
        return all;
      }

      /** Shows saved / carried markup elements on a page as editable marks. */
      restorePageMarkup(state, elements) {
        const B = window.MarkupBridge;
        for (const element of elements || []) {
          if (element.kind === 'text') {
            const at = this._toPx(state, element.x_pt, element.y_pt);
            // A text on a blank goes back into that blank's input.
            const blank = [...(this.registry || new Map()).values()].find((entry) => {
              if (entry.state !== state || !['underscore', 'vector', 'added'].includes(entry.kind)) return false;
              // An empty blank, or the one already showing this text (a zoom carries both ways).
              const current = entry.textEl.textContent.trim();
              if (current && current !== element.text) return false;
              const r = entry.px;
              return at.x >= r.x - 4 && at.x <= r.x + r.w && at.y >= r.top - 2 && at.y <= r.top + r.h + 6;
            });
            const family = B.fontFromWire(element.font);
            const style = (el) => {
              el.dataset.fontSize = String(element.size_pt);
              el.dataset.fontFamily = family;
              el.dataset.bold = element.bold ? 'true' : 'false';
              el.dataset.italic = element.italic ? 'true' : 'false';
              el.dataset.color = B.colorFromWire(element.color || 'black');
              el.style.fontSize = `${element.size_pt * state.scale}px`;
              el.style.fontFamily = family;
              el.style.fontWeight = element.bold ? 'bold' : 'normal';
              el.style.fontStyle = element.italic ? 'italic' : 'normal';
              el.style.color = el.dataset.color;
            };
            if (blank) {
              style(blank.textEl);
              blank.textEl.textContent = element.text;
              blank.el.dataset.markupId = element.id;
              blank.el.dataset.markupOrig = JSON.stringify(element);
              continue;
            }
            const wrap = this._spawnNewText(state, at.x, at.y, true);
            const ed = wrap.querySelector('.added-text-editor');
            style(ed);
            ed.textContent = element.text;
            const got = this._baselineOf(ed, state);
            wrap.style.left = `${(parseFloat(wrap.style.left) || 0) + at.x - got.x}px`;
            wrap.style.top = `${(parseFloat(wrap.style.top) || 0) + at.y - got.y}px`;
            wrap.dataset.markupId = element.id;
            wrap.dataset.markupOrig = JSON.stringify(element);
          } else if (element.kind === 'check' || element.kind === 'cross') {
            const at = this._toPx(state, element.x_pt, element.y_pt);
            const glyph = element.kind === 'check' ? '✓' : '✕';
            const box = [...state.domLayer.querySelectorAll('.sejda-auto-checkbox')].find((node) => {
              const left = parseFloat(node.style.left) || 0;
              const top = parseFloat(node.style.top) || 0;
              const current = node.textContent.trim();
              // An empty box, or the one already showing this mark (a zoom carries both ways).
              if (current && current !== glyph) return false;
              if (current && node.dataset.markupId && node.dataset.markupId !== element.id) return false;
              return at.x >= left && at.x <= left + node.offsetWidth && at.y >= top && at.y <= top + node.offsetHeight;
            });
            if (box) {
              box.textContent = element.kind === 'check' ? '✓' : '✕';
              box.dataset.markupId = element.id;
              box.dataset.markupOrig = JSON.stringify(element);
              continue;
            }
            const el = this._spawnMark(state, element.kind, at.x, at.y, element.size_pt / 0.75 * state.scale, true);
            el.dataset.markupId = element.id;
            el.dataset.markupOrig = JSON.stringify(element);
          } else if (element.kind === 'whiteout') {
            const a = this._toPx(state, element.x_pt, element.y_pt);
            const b = this._toPx(state, element.x_pt + element.w_pt, element.y_pt + element.h_pt);
            const box = this._createResizableBox(state, Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y), 'whiteout', true);
            box.dataset.markupId = element.id;
            box.dataset.markupOrig = JSON.stringify(element);
          }
        }
      }

      /** A ✓ or ✕ centred on (cx, cy) px, `px` square. */
      _spawnMark(state, kind, cx, cy, px, quiet = false) {
        const el = this._createObjWrap(state, cx - px / 2, cy - px / 2, px, px, kind, quiet);
        el.textContent = kind === 'check' ? '✓' : '✕';
        el.style.cssText += `display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:${px * 0.8}px;line-height:1;color:#0956a6;`;
        return el;
      }

      _spawnNewText(state, x, y, quiet = false) {
        const wrap = this._createObjWrap(state, x, y, null, null, 'added-text', quiet);
        const ed = document.createElement('div');
        ed.className = 'added-text-editor';
        ed.contentEditable = 'true';
        ed.spellcheck = false;
        ed.dataset.placeholder = 'Matn yozing...';
        ed.style.fontSize = `${10.5 * state.scale}px`;
        ed.style.fontFamily = 'Times New Roman, Times, serif';
        ed.dataset.fontSize = '10.5';
        ed.dataset.fontFamily = 'Times New Roman, Times, serif';
        ed.dataset.bold = 'false';
        ed.dataset.italic = 'false';
        ed.dataset.color = '#000000';
        wrap.appendChild(ed);

        ed.addEventListener('focus', () => {
          this.focusedNode = ed;
          this.selectedLineObj = null;
          this._showLineControlsInBar(false);
          this.boxBar.classList.remove('visible');
          this._positionBar(this.textBar, ed);
        });

        if (!quiet) ed.focus();
        return wrap;
      }

      _spawnImage(state, x, y, src, w, h) {
        const box = this._createResizableBox(state, x, y, w, h, 'image');
        box.dataset.src = src;
        box.style.backgroundColor = 'transparent';
        const img = document.createElement('img');
        img.src = src;
        img.style.cssText = 'width:100%;height:100%;object-fit:contain;pointer-events:none;display:block;';
        box.appendChild(img);
        this._selectBox(box);
      }

      _makeAddedObjDraggable(el) {
        el.addEventListener('mousedown', (e) => {
          e.stopPropagation();
          this.boxBar.classList.remove('visible');
          document.querySelectorAll('.added-obj.selected, .sejda-box-item.selected').forEach(n => n.classList.remove('selected'));
          el.classList.add('selected');

          if (e.target.isContentEditable || e.target.tagName === 'INPUT') return;

          e.preventDefault();
          const sx = e.clientX, sy = e.clientY;
          const sl = parseFloat(el.style.left) || 0;
          const st = parseFloat(el.style.top) || 0;

          const onMove = (mv) => {
            el.style.left = `${sl + (mv.clientX - sx)}px`;
            el.style.top = `${st + (mv.clientY - sy)}px`;
          };
          const onUp = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
          };
          window.addEventListener('mousemove', onMove);
          window.addEventListener('mouseup', onUp);
        });
      }

      _sanitizeWinAnsi(str) {
        return (str || '')
          .replace(/[\u2018\u2019]/g, "'")
          .replace(/[\u201C\u201D]/g, '"')
          .replace(/[\u2013\u2014]/g, '-')
          .replace(/\u00A0/g, ' ')
          .replace(/[^\x20-\x7E\xA1-\xFF]/g, '');
      }

      /**
       * HAQIQIY BINAR PDF KOMPILYATSIYASI (TRUE BINARY CONTENT-STREAM REWRITE):
       * 1. Asl PDF faylni (PDFDocument.load(this.pdfBytes)) binar darajada ochadi!
       * 2. SejdaBinaryStreamSurgeon orqali sahifaning binar /Contents oqimini ochib,
       *    eski BT..ET matn va eski m..l S / re f chiziq operatorlarini binar oqimdan tozalaydi!
       * 3. Barcha joriy matn va chiziqlarni (yangi uzunligi va yangi koordinatasi bilan)
       *    to'g'ridan-to'g'ri asl PDF sahifasiga haqiqiy binar operatorlar sifatida yozadi!
       * 4. Yangilangan binar PDF ni ham yuklab oladi, ham muharrirga qayta yuklaydi!
       */
      async exportEditedPDF(filename = 'sejda-v5-binary-edited.pdf', { download = true } = {}) {
        this.deselectAll();
        const { PDFDocument, StandardFonts, rgb, degrees } = PDFLib;

        // Asl PDF faylning o'zini binar darajada yuklaymiz (PNG screenshot emas!)
        const pdfDoc = await PDFDocument.load(this.pdfBytes);

        const fTimes = await pdfDoc.embedFont(StandardFonts.TimesRoman);
        const fTimesB = await pdfDoc.embedFont(StandardFonts.TimesRomanBold);
        const fTimesI = await pdfDoc.embedFont(StandardFonts.TimesRomanItalic);
        const fTimesBI = await pdfDoc.embedFont(StandardFonts.TimesRomanBoldItalic);
        const fHelv = await pdfDoc.embedFont(StandardFonts.Helvetica);
        const fHelvB = await pdfDoc.embedFont(StandardFonts.HelveticaBold);
        const fHelvI = await pdfDoc.embedFont(StandardFonts.HelveticaOblique);
        const fHelvBI = await pdfDoc.embedFont(StandardFonts.HelveticaBoldOblique);

        const pickFont = (fam, b, i) => {
          if ((fam || '').toLowerCase().includes('helvetica')) {
            if (b && i) return fHelvBI;
            if (b) return fHelvB;
            if (i) return fHelvI;
            return fHelv;
          }
          if (b && i) return fTimesBI;
          if (b) return fTimesB;
          if (i) return fTimesI;
          return fTimes;
        };

        const hexRgb = (hex) => {
          const c = (hex || '#000000').replace('#', '');
          const n = parseInt(c, 16);
          if (isNaN(n)) return rgb(0, 0, 0);
          return rgb(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255);
        };

        const pdfPages = pdfDoc.getPages();

        for (let i = 0; i < this.pages.length; i++) {
          const state = this.pages[i];
          const page = pdfPages[i];
          const sc = state.scale;
          const { width: pw, height: ph } = page.getSize();

          if (state.rotation) page.setRotation(degrees(state.rotation));

          // 1-QADAM: Sahifaning haqiqiy binar /Contents oqimini ochib, eski matn va chiziqlarni tozalash!
          const rawStreamStr = SejdaBinaryStreamSurgeon.extractRawPageStreamString(pdfDoc, page);
          const cleanedStreamStr = SejdaBinaryStreamSurgeon.stripTextAndHorizontalLinesFromStream(rawStreamStr);
          SejdaBinaryStreamSurgeon.writeCleanedStreamToPage(pdfDoc, page, cleanedStreamStr);

          // 2-QADAM: Barcha jonli matn qatorlarini (.sejda-line) haqiqiy PDF binar matn sifatida yozish
          state.domLayer.querySelectorAll('.sejda-line').forEach(el => {
            const txt = this._sanitizeWinAnsi(el.textContent);
            if (!txt.trim()) return;
            const ox = parseFloat(el.dataset.originX) + (parseFloat(el.dataset.dx) || 0);
            const oy = parseFloat(el.dataset.originY) - (parseFloat(el.dataset.dy) || 0);
            const sz = parseFloat(el.dataset.fontSize) || 10.5;
            page.drawText(txt, {
              x: ox,
              y: oy,
              size: sz,
              font: pickFont(el.dataset.fontFamily, el.dataset.bold === 'true', el.dataset.italic === 'true'),
              color: hexRgb(el.dataset.color)
            });
          });

          // 3-QADAM: Barcha jonli vektor chiziqlarni (.sejda-editable-line) yangi uzunligi bilan binar yozish
          state.domLayer.querySelectorAll('.sejda-editable-line').forEach(lineEl => {
            const x = (parseFloat(lineEl.style.left) || 0) / sc;
            const y = (parseFloat(lineEl.style.top) || 0) / sc;
            const w = lineEl.offsetWidth / sc;
            const h = lineEl.offsetHeight / sc;
            const lineBottomY = ph - y - h + 1;

            if (!lineEl.classList.contains('line-hidden')) {
              page.drawLine({
                start: { x, y: lineBottomY },
                end: { x: x + w, y: lineBottomY },
                thickness: 0.85,
                color: rgb(0.12, 0.16, 0.23)
              });
            }

            const inp = lineEl.querySelector('.line-text-input');
            const txt = this._sanitizeWinAnsi(inp ? inp.innerText : '');
            if (txt.trim()) {
              const sz = parseFloat(inp.dataset.fontSize) || 10.5;
              page.drawText(txt, {
                x: x + 2,
                y: lineBottomY + 2,
                size: sz,
                font: pickFont(inp.dataset.fontFamily, inp.dataset.bold === 'true', inp.dataset.italic === 'true'),
                color: hexRgb(inp.dataset.color)
              });
            }
          });

          // 4-QADAM: Checkbox katakchalar va ularning ichidagi ✓ / ✕ belgilari
          state.domLayer.querySelectorAll('.sejda-auto-checkbox').forEach(box => {
            const x = (parseFloat(box.style.left) || 0) / sc;
            const y = (parseFloat(box.style.top) || 0) / sc;
            const w = box.offsetWidth / sc;
            const h = box.offsetHeight / sc;
            const py = ph - y - h;

            page.drawRectangle({
              x, y: py, width: w - 1, height: h - 1,
              borderColor: rgb(0.12, 0.16, 0.23),
              borderWidth: 0.9
            });

            const mark = box.textContent.trim();
            if (mark) {
              page.drawText(mark === '✕' ? 'X' : 'V', {
                x: x + 1.5,
                y: py + 1.5,
                size: 9.5,
                font: fHelvB,
                color: rgb(0.03, 0.33, 0.65)
              });
            }
          });

          // 5-QADAM: Whiteout, Shapes, Images
          for (const box of state.domLayer.querySelectorAll('.sejda-box-item')) {
            const type = box.dataset.type;
            const x = (parseFloat(box.style.left) || 0) / sc;
            const y = (parseFloat(box.style.top) || 0) / sc;
            const w = box.offsetWidth / sc;
            const h = box.offsetHeight / sc;
            const py = ph - y - h;

            if (type === 'whiteout') {
              const opts = { x, y: py, width: w, height: h, color: hexRgb(box.dataset.fillColor || '#ffffff') };
              if (box.dataset.hasBorder === 'true') {
                opts.borderColor = hexRgb(box.dataset.strokeColor || '#000000');
                opts.borderWidth = 1.2;
              }
              page.drawRectangle(opts);
            } else if (type === 'shape') {
              page.drawRectangle({
                x, y: py, width: w, height: h,
                borderColor: hexRgb(box.dataset.strokeColor || '#000000'),
                borderWidth: 1.5
              });
            } else if (type === 'image' && box.dataset.src) {
              const src = box.dataset.src;
              const img = src.startsWith('data:image/png') ? await pdfDoc.embedPng(src) : await pdfDoc.embedJpg(src);
              page.drawImage(img, { x, y: py, width: w, height: h });
            }
          }

          // 6-QADAM: Yangi qo'shilgan matn va formalar (.added-obj)
          for (const item of state.domLayer.querySelectorAll('.added-obj')) {
            const type = item.dataset.type;
            const x = (parseFloat(item.style.left) || 0) / sc;
            const y = (parseFloat(item.style.top) || 0) / sc;
            const w = item.offsetWidth / sc;
            const h = item.offsetHeight / sc;
            const py = ph - y - h;

            if (type === 'added-text') {
              const ed = item.querySelector('.added-text-editor');
              const txt = this._sanitizeWinAnsi(ed ? ed.innerText : '');
              if (txt.trim()) {
                const sz = parseFloat(ed.dataset.fontSize) || 10.5;
                page.drawText(txt, {
                  x,
                  y: ph - y - sz * 0.85,
                  size: sz,
                  font: pickFont(ed.dataset.fontFamily, ed.dataset.bold === 'true', ed.dataset.italic === 'true'),
                  color: hexRgb(ed.dataset.color)
                });
              }
            } else if (type === 'check' || type === 'cross') {
              page.drawText(type === 'cross' ? 'X' : 'V', { x: x + 2, y: py + 4, size: 12, font: fHelvB });
            } else if (type === 'field') {
              const val = this._sanitizeWinAnsi(item.querySelector('input')?.value || '');
              if (val.trim()) {
                page.drawText(val, { x: x + 4, y: py + 5, size: 10.5, font: fHelv });
              }
            }
          }
        }

        const outBytes = await pdfDoc.save();

        // Yangilangan haqiqiy binar PDF ni darhol muharrirning o'ziga ham qayta yuklaymiz!
        await this.loadPDF(outBytes);

        if (download) {
          const a = document.createElement('a');
          a.href = URL.createObjectURL(new Blob([outBytes], { type: 'application/pdf' }));
          a.download = filename;
          a.click();
        }
        return outBytes;
      }
    }

    // =========================================================================
    // NAMUNAVIY BINAR PDF GENERATSIYASI
    // =========================================================================
    async function buildBinarySamplePdf() {
      const { PDFDocument, StandardFonts, rgb } = PDFLib;
      const doc = await PDFDocument.create();
      const times = await doc.embedFont(StandardFonts.TimesRoman);
      const timesBold = await doc.embedFont(StandardFonts.TimesRomanBold);
      const timesBI = await doc.embedFont(StandardFonts.TimesRomanBoldItalic);

      const p1 = doc.addPage([612, 792]);
      const h = 792;

      const drawVecLine = (page, x1, x2, yPos) => {
        page.drawLine({
          start: { x: x1, y: yPos },
          end: { x: x2, y: yPos },
          thickness: 0.85,
          color: rgb(0.15, 0.15, 0.15)
        });
      };

      const drawVecBox = (page, x, yPos) => {
        page.drawRectangle({
          x, y: yPos, width: 9.5, height: 9.5,
          borderColor: rgb(0.15, 0.15, 0.15),
          borderWidth: 0.9
        });
      };

      p1.drawText('CARGO FREIGHT TRUCKING INC', { x: 375, y: h - 55, size: 10.5, font: timesBold });
      p1.drawText('4050 EXECUTIVE PARK DRIVE, SUITE 205', { x: 365, y: h - 70, size: 9.5, font: times });
      p1.drawText('CINCINNATI, OH 45241', { x: 430, y: h - 85, size: 9.5, font: times });

      p1.drawText('INDEPENDENT CONTRACTOR SERVICES AND EQUIPMENT RENTAL AGREEMENT', { x: 92, y: h - 125, size: 11, font: timesBold });
      p1.drawText('LEASE-TO-RENT PROGRAM', { x: 230, y: h - 142, size: 11, font: timesBold });
      p1.drawText('Between Cargo Freight Trucking Inc and Contractor', { x: 180, y: h - 160, size: 11, font: timesBI });

      p1.drawText('Company:', { x: 92, y: h - 195, size: 10.5, font: timesBold });
      p1.drawText('Cargo Freight Trucking Inc', { x: 92, y: h - 212, size: 10.5, font: times });
      p1.drawText('4050 Executive Park Drive, Suite 205', { x: 92, y: h - 228, size: 10.5, font: times });
      p1.drawText('Cincinnati, OH 45241', { x: 92, y: h - 244, size: 10.5, font: times });
      p1.drawText('DOT #: 3421532', { x: 92, y: h - 260, size: 10.5, font: times });
      p1.drawText('MC #: 1106162', { x: 92, y: h - 276, size: 10.5, font: times });

      p1.drawText('Contractor Information:', { x: 92, y: h - 312, size: 10.5, font: timesBold });

      p1.drawText('Full Name:', { x: 92, y: h - 340, size: 10.5, font: times });
      drawVecLine(p1, 148, 430, h - 341);

      p1.drawText('CDL:', { x: 92, y: h - 368, size: 10.5, font: times });
      drawVecLine(p1, 122, 265, h - 369);
      p1.drawText('State:', { x: 278, y: h - 368, size: 10.5, font: times });
      drawVecLine(p1, 308, 368, h - 369);
      p1.drawText('Exp:', { x: 380, y: h - 368, size: 10.5, font: times });
      drawVecLine(p1, 405, 485, h - 369);

      p1.drawText('Date of Birth:', { x: 92, y: h - 396, size: 10.5, font: times });
      drawVecLine(p1, 158, 285, h - 397);

      p1.drawText('Phone:', { x: 92, y: h - 424, size: 10.5, font: times });
      drawVecLine(p1, 128, 310, h - 425);

      p1.drawText('Email:', { x: 92, y: h - 452, size: 10.5, font: times });
      drawVecLine(p1, 126, 330, h - 453);

      p1.drawText('W-9 Status:', { x: 92, y: h - 480, size: 10.5, font: times });
      drawVecBox(p1, 160, h - 481);
      p1.drawText('Individual', { x: 175, y: h - 480, size: 10.5, font: times });
      drawVecBox(p1, 238, h - 481);
      p1.drawText('Sole Proprietor', { x: 253, y: h - 480, size: 10.5, font: times });
      drawVecBox(p1, 338, h - 481);
      p1.drawText('LLC', { x: 353, y: h - 480, size: 10.5, font: times });

      p1.drawText('Effective Date of Agreement:', { x: 92, y: h - 510, size: 10.5, font: times });
      drawVecLine(p1, 226, 410, h - 511);

      p1.drawText('Unit Assigned:', { x: 92, y: h - 538, size: 10.5, font: times });
      drawVecLine(p1, 162, 425, h - 539);

      p1.drawText('This Agreement sets forth the terms under which the Contractor will provide commercial transportation', { x: 92, y: h - 575, size: 10.5, font: times });
      p1.drawText('services under the operating authority of Cargo Freight Trucking Inc and will rent Company-provided', { x: 92, y: h - 590, size: 10.5, font: times });
      p1.drawText('equipment under the Lease-to-Rent Program. This document is legally binding.', { x: 92, y: h - 605, size: 10.5, font: times });

      p1.drawText('Company Representative Signature:', { x: 92, y: h - 650, size: 10.5, font: times });
      drawVecLine(p1, 255, 410, h - 651);
      p1.drawText('Date:', { x: 422, y: h - 650, size: 10.5, font: times });
      drawVecLine(p1, 450, 530, h - 651);

      p1.drawText('Contractor Signature:', { x: 92, y: h - 688, size: 10.5, font: times });
      drawVecLine(p1, 192, 410, h - 689);
      p1.drawText('Date:', { x: 422, y: h - 688, size: 10.5, font: times });
      drawVecLine(p1, 450, 530, h - 689);

      return await doc.save();
    }

    // =========================================================================
    // ISHGA TUSHIRISH
    // =========================================================================
    window.addEventListener('DOMContentLoaded', async () => {
      const engine = new SejdaPDFEngine({
        container: '#sejda-mount-root'
      });
      window.sejdaEngine = engine; // sign-overlays.js draws on its pages

      // Opened from a contract (editor/?contract=<id>): that contract's PDF.
      // Opened on its own: the built-in sample document, as before.
      const contractId = (new URLSearchParams(window.location.search).get('contract') || '').replace(/[^A-Za-z0-9_-]/g, '');
      const outputName = contractId ? `contract-${contractId}-edited.pdf` : 'sejda-v5-binary-edited.pdf';
      const statusLine = document.getElementById('editor-status');
      if (contractId) document.getElementById('back-link').href = `/contracts/${contractId}`;
      // Inside the review panel there is nothing to go back to: hide the link.
      if (editorIsEmbedded()) {
        document.getElementById('back-link').style.display = 'none';
        document.body.classList.add('embedded');
      }
      document.getElementById('back-link').addEventListener('click', (event) => {
        // Inside the contract page, «back» closes the editor there instead of leaving the page.
        if (editorIsEmbedded()) {
          event.preventDefault();
          notifyContractPage('editor:close');
        }
      });
      let baseVersionNo = null; // the version the editor works on; a save builds on it
      // The contract's saved markup the editor shows (and replaces on save).
      let markupTaken = new Set();
      let markupShown = '[]';
      const showContractMarkup = async () => {
        markupTaken = new Set();
        try {
          const [uidByIndex, saved] = await Promise.all([
            pageUidsOf(contractId, baseVersionNo),
            loadContractMarkup(contractId),
          ]);
          const { byPage, taken } = window.MarkupBridge.splitForPages(saved.elements, uidByIndex);
          markupTaken = taken;
          engine.pages.forEach((state) => engine.showPageMarkup(state, byPage.get(state.pageIndex) || []));
        } catch (error) {
          statusLine.textContent = 'Qo‘shilgan matn va belgilar yuklanmadi.';
        }
        markupShown = window.MarkupBridge.signature(engine.collectMarkup());
      };
      try {
        if (contractId) {
          statusLine.textContent = 'Shartnoma yuklanmoqda…';
          const loaded = await loadContractVersion(contractId);
          baseVersionNo = loaded.versionNo;
          await engine.loadPDF(loaded.bytes);
          await showContractMarkup();
          statusLine.textContent = `Versiya ${baseVersionNo} ochildi.`;
        } else {
          await engine.loadPDF(await buildBinarySamplePdf());
          statusLine.textContent = 'Namuna hujjat. Shartnomani tahrirlash uchun shartnoma sahifasidagi «Edit PDF» tugmasini bosing.';
        }
      } catch (error) {
        statusLine.textContent = error instanceof Error ? error.message : 'PDF yuklanmadi.';
      }
      buildPageList(engine).catch(() => {});
      // Ask the contract page for its signatures and dates, now that the pages exist.
      if (window.SignOverlays) window.SignOverlays.ready();

      // On a contract only text edits are written into the file so far. A tool
      // whose work the save would refuse is switched off here, instead of
      // letting the person do the work and lose it at «Saqlash».
      const NOT_SAVED = 'Hozircha shartnomaga saqlanmaydi';
      if (contractId) {
        document.querySelectorAll('#main-toolbar .s-tool[data-tool]').forEach((btn) => {
          if (['text', 'check', 'cross', 'whiteout'].includes(btn.dataset.tool)) return;
          if (btn.dataset.tool === 'sign' && editorIsEmbedded()) return;
          btn.classList.add('not-saved');
          btn.setAttribute('aria-disabled', 'true');
          btn.title = NOT_SAVED;
        });
        engine.contractMode = true;
      }

      document.querySelectorAll('#main-toolbar .s-tool[data-tool]').forEach(btn => {
        btn.onclick = () => {
          if (btn.classList.contains('not-saved')) {
            statusLine.textContent = `«${btn.textContent.trim()}»: ${NOT_SAVED.toLowerCase()}.`;
            return;
          }
          document.querySelectorAll('#main-toolbar .s-tool').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const tool = btn.dataset.tool;
          engine.setTool(tool);

          if (tool === 'sign' && contractId && editorIsEmbedded()) {
            statusLine.textContent = 'Imzo qo‘yiladigan joyni sahifada bosing.';
          } else if (tool === 'sign') document.getElementById('sig-modal').classList.add('open');
          if (tool === 'image') document.getElementById('img-upload').click();
        };
      });

      document.getElementById('btn-undo').onclick = () => engine.undo();
      document.getElementById('btn-redo').onclick = () => engine.redo();

      document.getElementById('binary-close').onclick = () => {
        document.getElementById('binary-modal').classList.remove('open');
      };

      document.getElementById('img-upload').onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => { engine.pendingImage = ev.target.result; };
        reader.readAsDataURL(file);
      };

      // Imzo
      const sigPad = document.getElementById('sig-pad');
      const sigCtx = sigPad.getContext('2d');
      let drawingSig = false;
      sigPad.onmousedown = (e) => {
        drawingSig = true;
        const r = sigPad.getBoundingClientRect();
        sigCtx.beginPath();
        sigCtx.moveTo(e.clientX - r.left, e.clientY - r.top);
        sigCtx.lineWidth = 2.2;
        sigCtx.strokeStyle = '#09316b';
      };
      sigPad.onmousemove = (e) => {
        if (!drawingSig) return;
        const r = sigPad.getBoundingClientRect();
        sigCtx.lineTo(e.clientX - r.left, e.clientY - r.top);
        sigCtx.stroke();
      };
      window.addEventListener('mouseup', () => { drawingSig = false; });
      document.getElementById('sig-clear').onclick = () => sigCtx.clearRect(0, 0, sigPad.width, sigPad.height);
      document.getElementById('sig-close').onclick = () => document.getElementById('sig-modal').classList.remove('open');
      document.getElementById('sig-use').onclick = () => {
        engine.pendingSignature = sigPad.toDataURL('image/png');
        document.getElementById('sig-modal').classList.remove('open');
      };

      // «Shartnomaga saqlash»: the edited file becomes the contract's next version.
      const saveButton = document.getElementById('btn-save-contract');
      saveButton.style.display = contractId ? '' : 'none';
      const setBusy = (busy, text) => {
        saveButton.disabled = busy;
        if (text) statusLine.textContent = text;
      };
      // After a save: the file and the markup the server now holds, so the next edits build on them.
      const showSaved = async (note) => {
        const loaded = await loadContractVersion(contractId);
        baseVersionNo = loaded.versionNo;
        await engine.loadPDF(loaded.bytes);
        await showContractMarkup();
        statusLine.textContent = note;
        notifyContractPage('editor:saved');
        buildPageList(engine).catch(() => {});
        if (window.SignOverlays) window.SignOverlays.drawAll();
      };

      saveButton.onclick = async () => {
        if (!contractId || baseVersionNo === null || !engine.pdfBytes) {
          statusLine.textContent = 'Shartnoma hali yuklanmadi.';
          return;
        }
        // Only the lines that changed are written; the rest of the file stays as it is.
        const plan = engine.collectEdits();
        if (plan.unsupported.length) {
          statusLine.textContent = `Saqlanmadi: ${plan.unsupported.join(' ')}`;
          return;
        }
        const marks = engine.collectMarkup();
        const marksChanged = window.MarkupBridge.signature(marks) !== markupShown;
        const pages = engine.pagePlan();
        if (!plan.edits.length && !marksChanged && !pages.length) {
          statusLine.textContent = 'O‘zgarish yo‘q.';
          return;
        }
        setBusy(true, 'Saqlanmoqda…');
        try {
          // Page uids of the version being edited: marks name their page by it (kept across versions).
          const baseUids = await pageUidsOf(contractId, baseVersionNo);
          if (!plan.edits.length && !pages.length) {
            // Only added text / marks: they are the contract's markup, the PDF itself stays as it is.
            await saveContractMarkup(contractId, baseUids, marks, markupTaken);
            await showSaved('Saqlandi: qo‘shilgan matn va belgilar.');
            return;
          }
          const result = await saveEditsToContract(contractId, baseVersionNo, plan.edits.filter((edit) => !(engine.pages[edit.pageIndex] || {}).deleted), {
            pages,
            confirmSignatures: (count) => window.confirm(
              `Bu shartnomada ${count} ta imzo bor. Hujjat o‘zgargani uchun ular o‘chiriladi va shartnoma qayta imzolanadi. Davom etamizmi?`,
            ),
          });
          if (result.status === 'blocked') {
            statusLine.textContent = `Saqlanmadi: ${result.messages.join(' ')}`;
            return;
          }
          if (result.status === 'nothing') {
            statusLine.textContent = 'O‘zgarish yo‘q.';
            return;
          }
          if (result.status === 'cancelled') {
            statusLine.textContent = 'Saqlanmadi: imzolar o‘chirilishi bekor qilindi.';
            return;
          }
          baseVersionNo = result.version.version.version_no;
          let note = describeSavedVersion(result.version);
          if (marksChanged) {
            try {
              await saveContractMarkup(contractId, baseUids, marks.filter((mark) => !(engine.pages[mark.pageIndex] || {}).deleted), markupTaken);
            } catch (error) {
              note += ` Qo‘shilgan matn va belgilar saqlanmadi: ${describeSaveFailure(error)}`;
            }
          }
          await showSaved(note);
        } catch (error) {
          statusLine.textContent = describeSaveFailure(error);
        } finally {
          setBusy(false);
        }
      };
    });
