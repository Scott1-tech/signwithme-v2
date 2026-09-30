# Yagona ish maydoni (Workspace) — amalga oshirish rejasi

Sana: 2026-09-25. Manba: Plan agenti (faqat o'qish) + direktor qarorlari (§7).
Har bosqich agenti ishni **shu hujjatdan** boshlaydi. Qator raqamlari 2026-09-25
holatiga ko'ra — ishdan oldin joriy kodga qarab tekshiring.

## 0. Foydalanuvchi talabi va yopiq qarorlar

- «Boshqa-boshqa sahifa bo'lmasin — bitta joyda ham imzo, ham editor.» Review +
  imzo + muharrir **bitta sahifada**, **suzuvchi, sichqoncha bilan suriladigan
  bitta asboblar paneli** + o'ng panel. Eski `/review`, `/sign`, `/edit`
  URL'lari shu sahifani ochadi.
- Asboblar (foydalanuvchi hammasini tanladi): select, text, date, ✔/✖, imzo,
  **initsial**, highlight/underline/strikeout, ink, rect/ellipse/line/arrow,
  sticky note, rasm/muhr, **whiteout**, **mavjud matnni tahrirlash**, rang
  palitrasi (`black|blue|red|green|yellow|orange|purple`, **oq yo'q**),
  qalinlik, undo/redo.
- **Mavjud imkoniyatlarning birortasi yo'qolmaydi**: review/override qoidalari,
  imzolash darvozasi (tasdiqlanmagan kontraktda imzo qo'yish 409), slotlar,
  2 rejim, avto-to'ldirish, surish/resize, LayoutPanel (shu jumladan 2026-09-25
  qo'shilgan «Move together»), dublikat/o'chirish, qidiruv/eskiz/burish, finalize.
- Test bazasi `tests/ui/` (Python + Playwright, MockApi) — **134/134** bilan
  boshlanadi. DOM shartnomasi `tests/ui/README.md` da. Testlar ko'chirilishi
  mumkin, **zaiflashtirilmaydi**.
- Backend kontrakti **v5** parallel qurilmoqda (§8).

## 1. Hozirgi holat xaritasi

### 1.1 `/review`: `pages/ReviewPage.tsx` (379 qator)

| Qism | Qator | Izoh |
|---|---|---|
| PDF yuklash (`fetchPdf`) | 71-76 | Uch sahifada bir xil kod (Sign 243-253, Editor 159-164) |
| `useComparison` → `issues`, `issuesForPage`, `refresh` | 40-41 | |
| `useContractLayout` + `useSlotDrag` | 54-69 | Review'da ham slot sudraladi (layout kaliti bir xil) |
| `activeFieldId` | 45, 78-84 | Uchinchi xil «tanlash» holati |
| `correctField` (tuzatish → rerun → refresh) | 90-106 | |
| Override qoidalari (`criticalMissing/Fmcsa/Inconsistencies`, `overrideEligible`, `overrideUnavailable`, `describeOverrideBlockers`) | 23-32, 116-139 | Sof mantiq, sahifa ichida qotgan |
| `decide` / `approveWithOverride` → `navigate('/sign')` | 141-186 | |
| Overlay `FieldHighlighter` (`.highlight`) | 201-214 | Sign o'sha slotlarni `.slot` qilib chizadi — ikki xil render |
| «Open editor» | 234-240 | |
| Decision paneli, «Continue to signing» | 242-375 | |

### 1.2 `/sign`: `pages/SignaturePage.tsx` (~1702 qator)

| Blok | Qator | Qayerga |
|---|---|---|
| Turlar va sof yordamchilar (`PendingPlacement`, `FinalizeMissing`, `SIGN_MODES`, `BulkOutcome`, `SLOT_FAILURE_TEXT`, `readLayoutRejection`, `signingErrorText`, `groupPositionedByPage`, `today`) | 59-180 | `lib/signing.ts` |
| `source`, `signatures`, `signaturesLoaded` + fetch | 187-189, 243-253 | `useContractDocument`, `useContractSignatures` |
| Kutubxona (`mySignatures`, `libraryLoaded`, `reloadLibrary`) | 203-204, 235-257 | `useSignatureLibrary` (Editor 145-197 da dublikat) |
| Tanlovchi oqimi (`pending`, `pickingBulk`, `saveToLibrary`, `placeDataUrl`, `confirmSignature`, `chooseStored`, `pickerTitle`, `closePicker`) | 190, 205, 223, 259-267, 508-597, 1088-1097 | `useSignatureChooser` (Editor 142-147, 199-204, 363-414 da dublikat) |
| Slotlar (`slots`, `sigSlots`, `dateSlots`, `slotById`, `pairedDateFor`, `unpairedDateSlots`) | 269-280, 332-346 | `useSignatureSlots(result)` |
| Layout + drag + resize + clamp eslatmasi | 284-330 | `useSlotLayout` |
| `layoutRows` | 348-379 | `lib/signing.ts: buildLayoutRows` |
| Sahifa xaritalari, `signedFieldIds`, `freeSignatures`, `slotsComplete`, `emptySig/DateSlots` | 381-430 | `useSigningState` |
| `dateValues`, `selectedDate`, `applyDateToAll` | 193-194, 432-436 | `useDateValues` (+ persist, §7) |
| Hover taymeri, `collapsedPages`, `focusSlot`, `jumpToSlot` | 207-215, 438-506 | `useSlotFocus` → yagona tanlash |
| Darvoza (`override`, `blocked`, `notApproved`, `criticalIssues`) | 612-636 | `lib/gate.ts: computeSigningGate` |
| Bulk (`applyToAll`, `runBulkApply`) | 638-885 | `useBulkSigning` |
| Avto-to'ldirish + Undo | 887-991 | `useAutoFill` |
| `startSlot`, `replaceSlot`, `removeSignature` | 599-610, 993-1022 | `useContractSignatures` / `useSlotActions` |
| Finalize (422 layout, 409 missing) | 1024-1086 | `useFinalize` (Editor 541-583 bilan birlashadi) |
| `toolsTarget`, `toolsHandlers`, `markBoxFor` | 1099-1140 | `overlay/SlotLayer.tsx` |
| `renderPageOverlay` (slot, sana, imzo, SVG sanalar, `SlotToolbar`, ghost, `SlotResizeHandle`) | 1151-1397 | `overlay/SlotLayer.tsx` |
| O'ng panel JSX | 1452-1685 | `panels/SignSection.tsx`, `panels/FinalizeBar.tsx` |
| Xato ro'yxati `.review__document .alert--error` | 1402-1440 | `panels/FinalizeErrors.tsx` — **hujjat ustunida qoladi** (`test_override.py:358`) |
| `PdfViewer interactive={!blocked}` + bo'sh joyga bosish = erkin imzo | 1441-1449 | `signature` asbobiga aylanadi |

### 1.3 `/edit`: `pages/DocumentEditor.tsx` (737 qator)

- `useAnnotations` (snapshot undo, `UNDO_LIMIT=100`, `swm.annotations.<id>`) + `useAnnotationEdit` (move/resize, bitta commit): 120-128.
- `tool`, `selectedId`, `nextFont/Size/MarkSize`, `dateValue`: 130-137.
- Inline matn (`inline`, `inlineRef`, `commitInline`, `editAnnotation`): 149-157, 222-266.
- `handleCanvasClick` — asboblar bo'yicha `if` zanjiri: 277-359. **Registr yo'q.**
- Erkin imzolar (`field_id===null`), joylash/o'chirish: 166-181, 361-434.
- `window` keydown (Ctrl+Z/Y, Delete, strelkalar, Enter/F2, Esc): 472-537.
- Finalize: `dates` yubormaydi, layout'ni `loadLayout()` dan, `annotations` v4: 541-583.
- `AnnotationLayer`: faqat annotation bor sahifalarda, 607-636.

### 1.4 Umumiy qatlam

- `PdfViewer.tsx`: `canvas` → `PdfSearchMarks` (`.pdf-search-layer`) → `.pdf-overlay` (`renderPageOverlay` bolalari + burilganda `.pdf-overlay__shield`) (246-288). `onClick` `.pdf-page` da, pt ga o'giriladi (124-142). `interactive` prop `pdf-page--interactive` va burilish eslatmasini boshqaradi (201-205).
- **Overlay virtualizatsiya qilinmagan** — `renderPageOverlay` hamma sahifa uchun har renderda chaqiriladi; faqat canvas virtualizatsiyalangan.
- `usePdfViewer.ts`: `windowRef` tashqariga chiqmagan (236). `getPageText` faqat hosila `PageText` (`pieces` x/y/w/h unrotated pt) beradi, xom `TextContent` saqlanmaydi (477-550); LRU kesh (`MAX_TEXT_CHARS`, `MAX_TEXT_PIECES`).
- `lib/annotations.ts`: v4 turlari, `MAX_ANNOTATIONS=200`; `sanitizeAnnotation` noma'lum turni tashlaydi (275-276).
- CSS: `.pdf-overlay{pointer-events:none}`, `.pdf-overlay > *{pointer-events:auto}` (`viewer.css:121-122`, `index.css:467-468`); istisnolar `.swm-ann-layer` (`editor.css:233-240`), `.placed-signature`, `.placed-dates` (`index.css:502-518`). z-index: `--z-raised:10`, `--z-sticky:20`, `--z-overlay:40`, `--z-modal:50`.

### 1.5 Birlashtirishda hal qilinadigan to'qnashuvlar

1. Slot ikki xil: Review `.highlight[data-field-id]` (`FieldHighlighter.tsx:38-68`), Sign `.slot[data-field-id]` → **faqat `.slot`** qoladi (testlar shuni o'qiydi).
2. Erkin imzo ikki xil: Sign `.placed-signature` (1236-1296, sudralmaydi), Editor `.swm-sig` + «Remove and place again» (`AnnotationLayer.tsx:361-404`) → **faqat `.swm-sig`**.
3. Ikki xil finalize: Sign `dates + layout`, tugma lokal tekshiruvgacha o'chiq; Editor `layout + annotations`, tugma doim yoqiq → **bitta**, §7-9.
4. «interactive»: `/sign` da tik sahifa `pdf-page--interactive` (`test_rotation.py:355`); `/review` da tasdiqlangan kontraktda ham burilishda READONLY (`test_rotation.py:419-427`).

## 2. Maqsadli arxitektura

### 2.1 Komponent daraxti

```
App.tsx
└─ Route "contracts/:contractId/:panel"  (panel ∈ review|sign|edit, aks holda Navigate)
   └─ pages/ContractWorkspace.tsx            (yupqa: contractId, panel → <Workspace/>)
      └─ workspace/Workspace.tsx             <div class="review workspace editor--<tool>" data-testid="workspace">
         ├─ WorkspaceProvider (kontekstlar, 2.2)
         ├─ <div class="review__document workspace__stage">
         │   ├─ DocumentBanners           (loadError, template-match-warning, FinalizeErrors ← .alert--error)
         │   ├─ FloatingToolbar           data-testid="editor-toolbar" (docked-top = oqimda; floating = absolute)
         │   │   └─ PropertiesBar         (ann-font, ann-size, ann-date, rang, qalinlik, fill, ann-delete, undo/redo, ann-count, sig-count)
         │   └─ PdfViewer interactive={tool.placesOnClick && gate.ok(tool)}
         │        onCanvasClick={dispatchToTool} renderPageOverlay={p => <PageOverlay page={p}/>}
         │        renderTextLayer={textLayerActive ? p => <PdfTextLayer page={p}/> : undefined}
         └─ <aside class="review__panel workspace__panel">
             ├─ PanelSections (akkordeon, 2.7)
             │   ├─ ReviewSection   ← ReviewPage 217-376
             │   ├─ SignSection     ← SignaturePage 1453-1652
             │   ├─ LayoutSection   ← clampNote + LayoutPanel
             │   └─ ObjectsSection  (annotationlar/erkin imzolar ro'yxati, editor-hint, editor-note)
             └─ FinalizeBar (sticky pastda): data-testid="editor-finalize", "Generate final contract"
         SignatureChooserHost (SignatureModal, bitta nusxa)
```

### 2.2 Holat egaligi

Barcha holat `Workspace` darajasida — panel almashganda yo'qolmaydi.

| Holat | Ega | Shakl |
|---|---|---|
| Hujjat | `useContractDocument(contractId)` | `{ source: ArrayBuffer\|null; loadError: string\|null }` (PDF bir marta) |
| Kontrakt/natija | `useComparison` (o'zgarmaydi) | |
| Viewer | `usePdfViewer(source)` + `windowPages` | §3 |
| Server imzolari | `useContractSignatures(contractId)` | `{ rows; loaded; place(req); placeBulk(req); remove(id); setRows }` — **undo'da yo'q** |
| Kutubxona | `useSignatureLibrary()` | `{ items; loaded; reload; defaultSignature }` |
| Tanlovchi | `useSignatureChooser()` | `{ request: ChooserRequest\|null; open; close; saveToLibrary; setSaveToLibrary }`, `ChooserRequest = { purpose:'slot'\|'free'\|'bulk'\|'initials'; page?; bbox?; fieldId? }` |
| Slotlar | `useSignatureSlots(result)` | `{ slots, sigSlots, dateSlots, slotById, pairedDateFor, unpairedDateSlots, sigByPage, dateByPage }` |
| Layout | `useSlotLayout({contractId, slots, viewer})` | `{ layout, commit(next,label), replace(next), reset, drag, resize, clampNote, rows }` |
| Sanalar | `useDateValues(contractId)` | `{ values; set; fillAll; remove }` + `selectedDate`, persist `swm.dates.<id>` |
| Darvoza | `computeSigningGate(contract,result)` (sof) | `{ blocked; notApproved; overrideCovers; criticalIssues; reason }` |
| Review qoidalari | `lib/reviewRules.ts: computeOverrideState(result)` | `{ criticalMissing, criticalFmcsa, criticalInconsistencies, eligible, unavailable, blockers }` |
| Bulk/avto | `useBulkSigning`, `useAutoFill` | hozirgi mantiq so'zma-so'z |
| Annotationlar | `useAnnotations(contractId, history)` | `{ items; get; add; patch; removeMany; ... }` |
| Tarix | `useDocumentHistory()` | 2.3 |
| Asbob | `useToolState()` | `{ toolId; props; setTool; setProp }`, `swm.toolProps` |
| Tanlash | `useSelection()` | 2.3 |
| Panel | `useWorkspaceRoute()` | `{ panel; setPanel(p) }` (navigate replace) |
| Toolbar joyi | `useFloatingPanel('swm.toolbar')` | 2.6 |

Kontekstlar: **barqaror** (`DocumentCtx`: viewer funksiyalari, contract, slotlar) va **o'zgaruvchan** (`EditCtx`: tanlash, preview, tool) — pointer harakati faqat ikkinchisini qayta render qiladi.

### 2.3 Yagona tanlash va undo

```ts
// workspace/state/useSelection.ts
export type Selection =
  | { type: 'annotations'; ids: string[] }   // group_id bo'yicha kengaytirilgan
  | { type: 'slot'; fieldId: string }         // focusedSlotId + review activeFieldId
  | { type: 'signature'; id: string }         // erkin imzo/initsial (server qatori)
  | { type: 'issue'; key: string; fieldId: string | null }
  | null;
```

- Annotation bosilsa `select(id)`; `group_id` bo'lsa butun guruh tanlanadi.
- `SlotCardList` `focusedSlotId` va `ComparisonSidebar` `activeFieldId` yagona manbadan.
- Esc → `null`. Delete faqat `annotations` turida; server qatorlari faqat «Remove» tugmasi bilan.

```ts
// workspace/state/useDocumentHistory.ts
type Channel = 'annotations' | 'layout';
interface Step { channel: Channel; label: string; before: unknown; after: unknown }
export interface DocumentHistory {
  record(step: Step): void; undo(): void; redo(): void;
  canUndo: boolean; canRedo: boolean; undoDepth: number; redoDepth: number;
  register(channel: Channel, apply: (value: unknown) => void): () => void;
}
```

- Annotationlar undo'da (snapshot, reference bo'lishish, 100 qadam, bitta gesture = bitta qadam; edit-text whiteout+text = bitta qadam).
- Layout gesture'lari `commit` → undo'da; `fitLayoutToPagesReport` avto-clamp va finalize oldi clamp `replace` → **qadam emas**.
- **Server yozuvlari** (imzo, initsial, asset yuklash, bulk, autofill) undo'da **yo'q**: darhol saqlanadi/hash/audit; undo = yashirin DELETE, redo = yangi id/hash; 409 redo'ni rad etishi mumkin. `test_editor.py:708-711` va `SIGNATURE_LOCK_NOTE` shuni qulflaydi.
- `image` annotation lokal, `asset_id` ga ishora qiladi → undo'da.

### 2.4 Overlay qatlamlari va z-tartib (`.pdf-page` ichida)

1. `canvas.pdf-canvas`
2. `.pdf-search-layer` (`pointer-events:none`, o'zgarmaydi)
3. **`.pdf-text-layer`** (W3) — faqat oynadagi sahifada va matn asbobi yoqilganda; default `pointer-events:none`; `.workspace--text-tools .pdf-page:not(.pdf-page--rotated) .pdf-text-layer { pointer-events:auto }`
4. `.pdf-overlay` bolalari, pastdan yuqoriga:

| # | Bola | Klass | pointer-events |
|---|---|---|---|
| a | `ToolCaptureLayer` (ink, rect, ellipse, line, arrow, whiteout) | `.ws-capture` | faqat drag asbobida `auto`, aks holda render yo'q |
| b | Annotation chizmasi (whiteout eng pastda → highlight → shakl/ink → matn/belgi) | `.swm-ann-layer` (SVG) | `none` |
| c | `IssueLayer` (slot bo'lmagan muammolar) | `.highlight` | `auto` |
| d | Slot qutilari + slot imzolari + `.placed-dates` | `.slot`, `.placed-signature`, `.placed-dates` | select/signature asbobida `auto`; **boshqa joylash asbobida `none`** |
| e | Erkin imzo va initsial | `.swm-sig` (+`data-signature-kind`) | `auto` |
| f | Annotation hit-qutilari | `.swm-ann` | `auto` |
| g | Tanlash ramkasi/tutqichlar, `SlotToolbar`, `SlotResizeHandle`, ghost | `.swm-ann-handle`, `.slot-resize` | `auto`, `z-index: var(--z-raised)` |
| h | Inline input / note tahrirchisi | `.swm-ann-input` | `auto` |
| i | `.pdf-overlay__shield` | | **eng oxirgi**, o'zgarmaydi |

- (b), (f), (g) faqat `windowPages ± 1` da; (d) hamma sahifada (arzon, testlar `.pdf-page .slot` ni sanaydi).
- `PageOverlay` `React.memo`, faqat o'z sahifa bo'laklari (`itemsByPage.get(p)`, `preview?.page === p ? preview : null`).

### 2.5 Asbob registri

```ts
// workspace/tools/types.ts
export type ToolId = 'select'|'text'|'date'|'check'|'cross'|'signature'|'initials'
  |'highlight'|'underline'|'strikeout'|'ink'|'rect'|'ellipse'|'line'|'arrow'
  |'note'|'image'|'whiteout'|'edit-text';
export type ToolInput = 'none' | 'click' | 'drag' | 'text-selection' | 'text-run';
export interface ToolContext {
  page: number; scale: number; pageSize: PageSizePt | null; props: ToolProps;
  annotations: AnnotationStore; selection: SelectionApi;
  chooser: SignatureChooserApi; gate: SigningGate;
  notify(note: string): void; openInline(editor: InlineEditor): void;
}
export interface Gesture {
  move(pt: Point, ev: PointerEvent): void; end(pt: Point): void; cancel(): void;
  Preview: React.FC<{ scale: number }>;
}
export interface ToolDefinition {
  id: ToolId; label: string; glyph: string; hint: string; shortcut?: string;
  group: 'select'|'fill'|'markup'|'draw'|'insert'|'redact'; order: number;
  cursor: string; input: ToolInput; placesOnClick: boolean;
  needsTextLayer?: boolean; serverWrite?: boolean;
  properties: Array<'color'|'stroke'|'fill'|'font'|'size'|'date'>;
  onPageClick?(ctx: ToolContext, pt: Point, suggested: BBox): void;
  beginGesture?(ctx: ToolContext, pt: Point): Gesture;
  onTextSelection?(ctx: ToolContext, rects: RectPt[]): void;
  onTextRun?(ctx: ToolContext, run: TextRun): void;
}
```

- `workspace/tools/registry.ts`: `import.meta.glob('./*/index.ts', { eager: true })`, `group` + `order` bo'yicha. Yangi asbob = bitta papka, umumiy fayl tahrirlanmaydi.
- Annotation turlari: `lib/annotationKinds/<kind>.ts` → `{ kind, sanitize, forRequest, bounds, translate, Render, hitBox }`; `lib/annotations.ts` barrel bo'lib qoladi; union declaration merging:
  ```ts
  // lib/annotationKinds/types.ts
  export interface AnnotationKindMap {}
  export type Annotation = AnnotationKindMap[keyof AnnotationKindMap];
  // lib/annotationKinds/ink.ts
  declare module './types' { interface AnnotationKindMap { ink: InkAnnotation } }
  ```
- `DocumentEditor.handleCanvasClick` (277-359) → `tools/text|date|check|cross|signature`; `tools/signature` sign-sahifa click-to-place'ini ham oladi (`chooser.open({purpose:'free'})`).
- **Boshlang'ich asbob URL'dan**: `/sign` → `signature`; `/review`, `/edit` → `select`. Keyin panel almashsa asbob o'zgarmaydi.

### 2.6 Suzuvchi asboblar paneli

```ts
interface ToolbarPlacement { mode: 'docked-top' | 'floating'; x: number; y: number;
  orientation: 'horizontal' | 'vertical'; collapsed: boolean }   // swm.toolbar, sanitize
```

- Default **`docked-top`** (oqimda — testlar aniq pikselga bosadi). Sudralganda `floating`.
- Surish: `toolbar-grip`, pointer capture, `.workspace__stage` ga nisbatan, clamp (≥48 px ko'rinadi). Yuqori chetga 24 px → `docked-top`; chap/o'ng chetga → `floating` + `vertical`. Stage `ResizeObserver` → qayta clamp.
- Yig'ish: `toolbar-collapse` → faqat joriy asbob glifi + kengaytirish.
- Klaviatura: `role="toolbar"` + roving tabindex, strelkalar/Home/End; grip ustida strelka 10 px (Shift 50), Enter docked/floating; Alt+T fokus, Esc hujjatga. Bir harfli yorliqlar (`aria-keyshortcuts`): V, T, D, K, X, S, I, H, U, P, R, O, L, A, N, M, W, E — input/textarea/select ichida va Ctrl/Alt bilan **o'chiq**.
- ≤640 px: pastga majburan yopishadi (`position:fixed; bottom:0`), gorizontal scroll, 44 px tegish, surish o'chiq, `PropertiesBar` pop-over. 641-1024 px: default docked-top.
- Joy yetmasa oxirgi guruhlar «More ▾» ga (`ResizeObserver`).
- Saqlanadigan testid'lar: `editor-toolbar`, `tool-<id>`, `ann-font`, `ann-size`, `ann-date`, `ann-delete`, `editor-undo`, `editor-redo`, `undo-depth`, `redo-depth`, `ann-count`, `sig-count`.

### 2.7 O'ng panel

- **Akkordeon**: Tekshiruv / Imzo va sana / Joylashuv / Obyektlar; ochiqlar `swm.panel.sections`.
- URL segmenti majburan ochadi va ko'rinishga scroll qiladi: `/review` → Tekshiruv; `/sign` → Imzo **+** Joylashuv (`open_sign` `layout-panel` ni kutadi); `/edit` → Obyektlar.
- `FinalizeBar` doim ko'rinadi (sticky), tayyorlik xulosasi bilan («2 imzo, 1 sana yetishmaydi» → bosilsa `focusSlot`).

## 3. pdf.js matn qatlami (W3)

`usePdfViewer.ts` ga:
```ts
windowPages: ReadonlySet<number>;   // runSchedule (552-581) ichida, to'plam o'zgargandagina setState
renderTextLayer(page: number, container: HTMLDivElement): { cancel(): void; done: Promise<TextLayerHandle | null> };
interface TextLayerHandle { runs: TextRun[]; spanIndexOf(el: Element): number | -1 }
```
- `pdfjs.TextLayer` (4.8.69), `viewport = page.getViewport({ scale, rotation: page.rotate })` — o'quvchi burishi qo'shilmaydi; overlay bilan bir xil `layerStyle`/`rotationTransform`. `--scale-factor` CSS o'zgaruvchisi; `.textLayer` qoidalari `pdfjs-dist/web/pdf_viewer.css` (4.8.69) dan `styles/viewer.css` ga (versiyaga qarab tekshir).
- Xom `TextContent` uchun alohida LRU (≤10 sahifa); mavjud `textCacheRef` o'zgarmaydi.
- Geometriya bitta manbadan: `getPageText` 504-522 → `lib/textRuns.ts: runGeometry(item, viewportTransform)` (qidiruv, highlight, edit-text).
  ```ts
  interface TextRun { index: number; text: string; x: number; y: number; width: number;
    height: number; baseline: number; fontSize: number; fontFamily: 'sans-serif'|'serif'|'monospace';
    bold: boolean; italic: boolean }
  ```
  `bold/italic`: `page.commonObjs.get(item.fontName)?.name` ↔ `/Bold|Black|Heavy/`, `/Italic|Oblique/`; yo'q bo'lsa `false`.
- Mount sharti: `activeTool.needsTextLayer && windowPages.has(page) && rotationOf(page) === 0`; oynadan chiqsa `cancel()` + DOM tozalash. ≤6 sahifa × ~360 span.
- Burilgan sahifada matn asboblari o'chiq (`.pdf-page--rotated .pdf-text-layer{pointer-events:none}`).
- Tanlash → rect (`workspace/text/useTextSelection.ts`): `getSelection()` → `range.getClientRects()` ∩ qatlam → `/scale` → bir qatordagilar birlashadi (|Δy| < 2 pt) → clamp, ≥1 pt, ≤50 rect → `onTextSelection` → selection tozalanadi.
- Qidiruv panelida «Highlight all matches» (mavjud `SearchHit` rect'laridan, matn qatlamisiz).
- Yangi konversiya yo'q — unrotated pt + `ptToPx`/`pxToPt`.

## 4. Marshrutlar

```tsx
<Route path="contracts/:contractId/final" element={<FinalContract />} />     // statik, g'olib
<Route path="contracts/:contractId/:panel" element={<ContractWorkspace />} />
<Route path="contracts/:contractId" element={<WorkspaceDefaultRedirect />} />
```
- `/review|sign|edit` — **bitta Route elementi**; panel almashganda (`navigate(..., {replace:true})`) qayta mount yo'q: PDF qayta olinmaydi, scroll/zoom saqlanadi. Dashboard (`Dashboard.tsx:856, 864, 875`), `ReviewQueue.tsx:31`, `ContractUpload.tsx:144, 538` o'zgarishsiz.
- Noto'g'ri `:panel` → `<Navigate to="../review" replace/>`. `/contracts/:id` → pending + reviewer → `/review`; approved → `/sign`; aks holda `/review`.
- Approve (`ReviewPage.tsx:148,180`) → `setPanel('sign')` (URL `/sign`, `test_override.py:231`). «Continue to signing», «Go to review» (Sign 1545) → `setPanel`.
- Review/sign ichidagi «Open editor» olib tashlanadi; Dashboard'dagi `dash-open-editor` qoladi.

## 5. Bosqichlar

Har bosqich oxirida: `npm run typecheck` + `npm run build` EXIT 0, `python tests\ui\run_all.py` hammasi yashil. Vite dev server tip tekshirmaydi — build'ni o'tkazib yuborma.

### W0 — `SignaturePage` ni ichidan bo'lish (UI o'zgarmaydi)

Ketma-ket, har qadamdan keyin testlar:
1. `lib/signing.ts` (59-180), `lib/gate.ts` (612-636), `lib/reviewRules.ts` (Review 23-32, 116-139).
2. `useContractDocument`, `useSignatureLibrary`, `useContractSignatures`.
3. `useSignatureSlots`, `useDateValues`, `useSigningState`.
4. `useSignatureChooser` + `SignatureChooserHost` (Editor dublikati ham).
5. `useBulkSigning`, `useAutoFill` (so'zma-so'z, faqat `setSignatures` → `signatures.setRows`).
6. `overlay/SlotLayer.tsx` (1099-1397).
7. `panels/SignSection.tsx`, `panels/FinalizeBar.tsx`, `panels/FinalizeErrors.tsx`, `panels/ReviewSection.tsx`, `overlay/IssueLayer.tsx`.
8. `useFinalize({ dates, layout, annotations })` — bitta body quruvchi; `annotations` bo'sh bo'lsa kalit yuborilmaydi; `isAnnotationsUnsupported` shu yerga.
9. `useSlotLayout` (284-330) — eng oxirida.

Testlar **o'zgarmaydi**. Xavf: `useCallback` bog'liqliklari (`applyToAll`, autofill effekti) aynan saqlansin — aks holda autofill ikki marta ishlaydi.

### W1 — Ish maydoni qobig'i (yangi asbob yo'q)

- Yangi: `pages/ContractWorkspace.tsx`, `workspace/Workspace.tsx`, `workspace/useWorkspaceRoute.ts`, `workspace/state/useSelection.ts`, `overlay/PageOverlay.tsx`, `overlay/FreeSignatureLayer.tsx`, `panels/ObjectsSection.tsx`, `styles/workspace.css`.
- O'zgaradi: `App.tsx`, `AnnotationLayer.tsx` (erkin imzo qismi chiqadi), `FieldHighlighter.tsx` (slot tarmog'i 38-69 chiqadi), `EditorToolbar.tsx` (joyi ko'chadi, «Signing page» havolasi chiqadi). `PdfViewer.tsx` o'zgarmaydi.
- `ReviewPage.tsx`, `SignaturePage.tsx`, `DocumentEditor.tsx` o'chiriladi.
- Qoidalar: erkin imzo faqat `.swm-sig`; slot faqat `.slot`; bitta finalize; boshlang'ich asbob URL'dan; slot qatlami joylash asbobida `pointer-events:none`; `blocked` da `serverWrite` asboblari o'chiq + sabab.
- Testlar ko'chadi: `test_editor.py:373-393` (`document-editor` → `workspace`), `:1409-1452` (uch URL + dashboard bitta workspace, panel almashganda `/file` bir marta, URL segmenti), `:1110, 1338, 1370, 1391` (finalize oldidan `apply_dates_to_all(page)`).
- Yangi `test_workspace.py` (≥10): uch URL bitta workspace; approve'dan keyin qayta yuklanmasdan `/sign`; text asbobida slot ustiga bosish matn qo'yadi; slot imzosi `.placed-signature`, erkin `.swm-sig`, dublikat yo'q; finalize body'da dates+layout+annotations; tasdiqlanmagan — signature asbobi disabled + sabab, text ishlaydi, finalize o'chiq; `/sign`→`/edit`→`/sign` da `dateValues` saqlanadi; sidebar muammosi → `slot--active`; burilgan sahifa `/sign` PLACEMENT, `/review` READONLY; Obyektlar ro'yxati sahifa bilan mos.
- Xavf: `.review__document` / `.review__panel` saqlansin (`test_override.py:358`). Global keydown sign bo'limida ham — Delete/strelkalar faqat annotation tanlanganda.

### W2 — Asbob registri + suzuvchi panel + yagona tarix

- `workspace/tools/{types,registry}.ts`, `tools/{select,text,date,check,cross,signature}/index.ts`, `workspace/toolbar/{FloatingToolbar,PropertiesBar}.tsx`, `useFloatingPanel.ts`, `workspace/state/{useDocumentHistory,useToolState}.ts`, `hooks/useAnnotations.ts` (tarix tashqariga).
- W2b: `useSlotLayout` `commit`/`replace`; slot surish undo'ga.
- v5 `color?` `text/date/check/cross` ga.
- `test_toolbar.py`: surish, persist, clamp, yopishish, yig'ish, yorliqlar, roving focus, 640 px, slot surishni Ctrl+Z qaytarishi.

### W3 — Matn qatlami + highlight/underline/strikeout

- `usePdfViewer.ts`, `lib/textRuns.ts`, `PdfViewer.tsx` (`renderTextLayer`), `workspace/text/{PdfTextLayer.tsx,useTextSelection.ts}`, `lib/annotationKinds/{highlight,underline,strikeout}.ts`, `tools/{highlight,underline,strikeout}/`, `PdfSearchBox.tsx`, `styles/viewer.css`.
- `MAX_ANNOTATIONS` 200 → 500 (`"0 / 200"` testlari shu commit'da).
- README DOM shartnomasi (`.pdf-text-layer`, `data-ann-rects`).
- `test_textlayer.py`.
- **Holat (2026-09-27, `_staging\w3`):** qurildi — `tools/{highlight,underline,strikeout}` (H / U / **C** — S imzoda, X/K belgilarda band; C = "cross out"), umumiy `tools/markup.ts`; `text/useTextSelection.ts` (belgi→span→`TextRun.item`→run; x/w Liberation metrikasi bilan kerningsiz, y/h run ink qutisi; qator birlashtirish, ustunlar alohida; sahifa bo'yicha, bitta undo); `PdfTextLayer mode="select"`; «Highlight all matches» (`pdf-search-highlight-all`, bitta qadam, ≤500 hammasi-yoki-hech-narsa); `MAX_ANNOTATIONS` 500; slot sanasi 6 oila + B/I (`date_bold`/`date_italic` faqat `true`). Test fayli `test_textlayer.py` o'rniga **`test_markup.py`** (19). `lib/annotationKinds/*` bo'linmasi QILINMADI — `lib/annotations.ts` hali monolit (W4 eslatmasi, JURNAL).

### W4 — ink, shakllar, note, rasm/muhr

- `overlay/ToolCaptureLayer.tsx`, `tools/{ink,rect,ellipse,line,arrow,note,image}/`, `lib/annotationKinds/*`, `api/assets.ts`, `lib/pathSimplify.ts` (RDP 0.5 pt).
- `useAnnotationEdit` → umumiy `bounds/translate/resizeBox`; rect/ellipse/image 8 tutqich, line/arrow 2 uch.
- `test_draw.py`; MockApi'ga assets endpointi (harness'ni bitta agent tahrirlaydi).
- **Holat (2026-09-27, `_staging\w4`):** qurildi — `overlay/ToolCaptureLayer.tsx` (`.ws-capture`, overlay'ning BIRINCHI bolasi, faqat drag asbobida; preview qatlamning o'z state'ida → hech bir `PageOverlay` qayta render bo'lmaydi; Esc bekor; `workspace--drawing` boshqa hit-qutilarni chetga suradi); `tools/{ink (P), rect (R), ellipse (O), line (L), arrow (A), note (N), image (M)}` + umumiy `tools/drawing.tsx` (3 fabrika). `lib/annotationKinds/{shared,ink,shape,line,note,image,resize}.ts` — FAQAT yangi turlar bo'lindi (eski 8 tur `annotations.ts` da qoldi, barrel dispatch qiladi; sabab `shared.ts` izohida). `lib/pathSimplify.ts` (RDP 0.5 pt, iterativ), `lib/imageFile.ts` (2 MB / magic / 4000 px oldindan), `api/assets.ts`, `hooks/useAnnotationAssets.ts` (StrictMode'da ham bitta GET). `useAnnotationEdit` → preview = butun annotation; `bindShapeHandle` (8 tutqich / 2 uch, `reshapeByHandle`). PropertiesBar: `stroke` / `fill` guruhlari (`propertyGroupsFor(kind)`), chizmalarda appearance = faqat rang. Toolbar: `ToolDefinition.menu` → «Markup ▾» / «Shapes ▾» (ink + 4 shakl) / «Insert ▾» (note, image) — qatorda 10 tugma (W4 dan oldingidek), 1600 px'da bo'sh joy ~110 px (bo'sh imzo hisobi bilan ham sig'adi). `slotsStepAside` (image: serverWrite, lekin slotlar chetga). `tests/ui/test_draw.py` 23; jami 238.

### W5 — whiteout + mavjud matnni tahrirlash + initsial

- `tools/{whiteout,edit-text,initials}/`, `lib/annotationKinds/whiteout.ts`, `group_id` (sanitize, selection, bir qadamli qo'shish/o'chirish).
- edit-text: span → `TextRun` → inline editor → commit: `whiteout{x,y,w,h ± 0.5}` + `text{x, baseline, font=map(fontFamily), size, color:'black'}` bitta `group_id`.
- Initsial: `chooser.open({purpose:'initials'})` → «har sahifaga» N ta POST (`BULK_CONCURRENCY=3`), undo'siz, «Remove all initials».
- `test_redact.py`.
- **Holat (2026-09-27, `_staging\w5`):** qurildi — `tools/whiteout` (W, `redact`, Edit text'dan keyin; `drawing.tsx whiteoutTool` = rect sudrashi, Shift kvadrat), `lib/annotationKinds/whiteout.ts` (make/sanitize/forRequest/same/clamp; barrel dispatch), `overlay/WhiteoutLayer.tsx` — whiteout'lar endi slot/imzo qatlamidan PASTDA (chop tartibi: whiteout → imzolar → qolganlar; oldin annotation SVG'sining tagida, ya'ni imzolar USTIDA edi), erkin whiteout 8 tutqich (`hasBoxHandles`), ekrandagi punktir chegara faqat CSS; birinchi whiteout'da bir martalik «faqat yashiradi» eslatmasi (`swm.whiteout.noticed`). `tools/initials` (I, `fill`, Signature'dan keyin; `serverWrite` + `slotsStepAside`), `lib/initials.ts` + `hooks/useInitials.ts` (60×24 pt ichida nisbat bilan, bosilgan joy markazida; «Every page» pastki-o'ng burchakdan, `runPool` 3; «Remove all initials»; ajratish: quti ≤ 60×24 YOKI `swm.initials.<id>`); kutubxonaga `label: "Initials"`. `clampAnnotationsToPages` + `store.replace` — saqlangan annotation sahifa o'lchami kelgach sahifaga tortiladi (qadam emas, bir marta aytiladi). Toolbar: 12 tugma; 1600 px'da (12 imzo hisobi bilan ham) overflow 0 — compact rejimda asbob chetlari 3 px / 25 px, Delete faqat ⌫. `test_redact.py` 13; jami 251.

### Parallel bajarish

| Nima | Kim bilan xavfsiz | To'qnashuv |
|---|---|---|
| W0 | Bitta agent | `SignaturePage.tsx` |
| W1 | Bitta agent | `App.tsx`, `Workspace.tsx` |
| W2 | Avval `tools/types.ts`; keyin toolbar ∥ registry+tarix | `useAnnotations.ts` |
| W3 ∥ W4 | Parallel (glob registry, declaration merging) | `lib/annotations.ts` barrel, `tests/ui/_harness.py` — ketma-ket |
| W5 | W3 dan keyin | |

## 6. Test va ishlash xatarlari

- `test_editor.py`: 373-393, 1409-1452, 1110/1338/1370/1391, `"0 / 200"` (W3). Joylash asbobida slot qatlami `pointer-events:none` bosish nuqtalarini kafolatlaydi.
- `test_rotation.py:355` / `:419-427` — boshlang'ich asbob qoidasiga bog'liq.
- `test_override.py:231, 250, 361` (URL segmentlari), `:358` (`.review__document .alert--error`).
- `open_sign` `layout-panel` va `.pdf-page .slot` sonini kutadi.
- `test_warnings`: `template-match-warning` hujjat ustunida, `template-match-badge` imzo bo'limida.
- Ishlash (49-66 bet, 500 element): `PageOverlay` memo + per-sahifa bo'laklar + og'ir qatlamlar `windowPages ± 1`; drag preview faqat o'z sahifasiga; ink RDP; matn qatlami ≤6 sahifa; tarix reference bo'lishadi; `getBBox` faqat oynada.

## 7. Direktor qarorlari (Plan'dagi ochiq savollar, 2026-09-25)

1. **Tasdiqlanmagan kontrakt**: lokal annotationlar faol; `serverWrite` asboblari (imzo, initsial, asset yuklash) va finalize **o'chiq**, sababi yoziladi («Contract not approved yet»). Asset yuklash darvozasi backend v5 hisobotiga qarab.
2. **Annotationlar hozircha `localStorage` da** (boshqa mashinada ko'rinmaydi). Server qoralamasi — alohida qaror, bu ish doirasida EMAS; UI'da «Saved in this browser only» ko'rinib tursin.
3. **v5 maydon nomlari — backend kontrakti bo'yicha** (§8): `x_pt/y_pt/w_pt/h_pt`, `x1_pt..y2_pt`, `stroke_pt`, `rects[]`, `paths[][]`. v4 maydonlari o'zgarmaydi.
4. **Chop etish tartibi**: asl sahifa → whiteout → imzolar/slot sanalari → qolgan annotationlar (ro'yxat tartibida). Highlight — shaffof (alfa ~0.35). Note — haqiqiy PDF `/Text` annotatsiya (ikonka + popup). Brauzer z-tartibi shunga mos (§2.4).
5. **edit-text shriftlari**: hozircha 3 ta base-14 (Helvetica/Times-Roman/Courier). Bold/italic W5 da backend bilan kelishiladi; qo'llanmasa UI buni ochiq aytadi.
6. **Guruh**: edit-text juftligida whiteout qulf (asl joyda), matn erkin suriladi; o'chirish butun guruhni o'chiradi; bitta undo qadami.
7. **Initsial**: backend o'zgarmaydi — erkin imzo endpointi (`field_id: null`), kichikroq default o'lcham, «har sahifaga» = N ta POST (`BULK_CONCURRENCY=3`), undo'siz, «Remove all initials».
8. **`dateValues` persist**: ha, `swm.dates.<id>`.
9. **Finalize darvozasi**: sign qoidasi — `busy || blocked || signatures.length===0 || !slotsComplete` bo'lsa o'chiq; Editor'ning 409 «Show» havolalari saqlanadi.

### 7.1 Ustuvorlik o'zgardi (2026-09-26, foydalanuvchi talabi)

«PDF'ning matnlarini edit qilish — PDF yuklangandan keyin matnini tahrirlash, shrift tanlash — shu funksiyalar ham bo'lishi kerak.» → **mavjud matnni tahrirlash oldinga olinadi**. Yangi tartib:

1. Sana tuzatmasi («Har safar so'rasin») — sinxronlash.
2. **F1 backend shriftlar**: Liberation Sans/Serif/Mono × Regular/Bold/Italic/BoldItalic (SIL OFL, `backend/app/assets/fonts/liberation/`), PDF'ga subset embedded; `font` enum + `LiberationSans|LiberationSerif|LiberationMono`; `bold?`/`italic?` (base-14 uchun ham); embedded shriftda belgi tekshiruvi TTF cmap bo'yicha (kirill, `oʻ` o'tadi). Tanlov sababi: Arial / Times New Roman / Courier New bilan **metrik-mos** — tahrirlangan matn asl kengligiga sig'adi.
3. **WE (edit-text)** = W3 ning matn qatlami qismi (§3) + W5 ning edit-text qismi: run'lar bir qatorga birlashtiriladi, bosilsa inline tahrirchi asl matn bilan ochiladi, shrift avtomatik tanlanadi (serif→LiberationSerif, sans→LiberationSans, mono→LiberationMono, bold/italic aniqlanadi, o'lcham run balandligidan), foydalanuvchi shrift/o'lcham/rang/qalin/kursivni o'zgartira oladi → `whiteout` + `text` bitta `group_id` (§7-6). Oddiy Text asbobiga ham yangi shriftlar va qalin/kursiv. Preview shu TTF'larni `@font-face` bilan ishlatadi.
4. Keyin W3 belgilash (highlight/underline/strikeout) → W4 → W5 qolgani (whiteout asbobi, initsial).

## 8. Backend kontrakti v5 (kelishilgan)

Umumiy: `id` (`[A-Za-z0-9_-]{1,64}`, noyob), `kind`, `page`, ixtiyoriy `group_id`; PDF pt, **yuqori-chap origin**; har tur `extra="forbid"` (discriminated union); `color` enum `black|blue|red|green|yellow|orange|purple` (oq yo'q); jami ≤500 element, ink nuqtalari jami ≤20 000; sahifa tashqarisi → 422 `annotation_outside_page`.

| kind | Maydonlar |
|---|---|
| `text`, `date` | v4 + `color?` (default black; yo'q bo'lsa PDF v4 bilan bayt-bir xil) |
| `check`, `cross` | v4 + `color?` |
| `highlight` / `underline` / `strikeout` | `rects: [{x_pt,y_pt,w_pt,h_pt}]` (1…50), `color` (highlight default yellow, qolgan red) |
| `ink` | `paths: [[{x_pt,y_pt}...]]` (1…20 path, 2…2000 nuqta, element ≤4000), `stroke_pt` 0.5…12, `color` |
| `rect` / `ellipse` | `x_pt,y_pt,w_pt,h_pt`, `stroke_pt`, `color`, `fill: bool` (faqat shaffof ~0.2) |
| `line` / `arrow` | `x1_pt,y1_pt,x2_pt,y2_pt`, `stroke_pt`, `color` |
| `note` | `x_pt,y_pt`, `text` 1…1000 (WinAnsi), `color` (default yellow) — PDF `/Text` annotatsiya |
| `image` | `asset_id`, `x_pt,y_pt,w_pt,h_pt`; `POST /api/contracts/{id}/annotation-assets` → `{asset_id,width_px,height_px}`, `GET .../annotation-assets/{asset_id}` |
| `whiteout` | `x_pt,y_pt,w_pt,h_pt` — noshaffof oq; auditda joyi (matnsiz); **faqat vizual** (asl matn faylda qoladi) |

Matn (text/date/note) — faqat WinAnsi; boshqa belgi → 422 (`undrawable_character`).

### 8.1 Backend v5 qurildi (2026-09-25, pytest 772) — frontend preview SHUNGA mos bo'lsin

- **Palitra hex'lari** (`pdf_builder.py:177`): black `#000000`, blue `#1D4ED8`, red `#DC2626`, green `#16A34A`, yellow `#FACC15`, orange `#EA580C`, purple `#7E22CE`. Default: highlight/note `yellow`, underline/strikeout `red`, qolganlari `black`.
- **Highlight**: alfa **0.35**, oddiy aralashtirish (multiply EMAS).
- **Underline/strikeout**: qalinlik `max(0.75, h·0.08)`; underline rect pastki cheti ichida, strikeout o'rtada.
- **ink / rect / ellipse**: chiziq geometriya ustida **markazlangan** (SVG kabi); ink yumaloq uch/burilish.
- **rect `fill: true`**: shaffof, alfa **0.2**.
- **line / arrow**: uchi to'g'ri kesilgan (butt); arrow boshi to'lgan uchburchak — uzunlik `6·stroke`, yarim kenglik `3·stroke`; uzunlik ≥ 1 pt.
- **stroke_pt** default **2.0**.
- **image**: qutini aynan to'ldiradi (aspect saqlanmaydi — frontend resize'da aspect'ni o'zi saqlasin), alfa saqlanadi.
- **note**: haqiqiy `/Text` annotatsiya, `/F`=28 (Print|NoZoom|NoRotate), `/Rect` yuqori-chap burchagi (x_pt, y_pt) ga bog'langan; matnda `\n` ruxsat (WinAnsi tekshiruvi qoladi).
- **Asset**: `POST /api/contracts/{id}/annotation-assets` — JSON `{image_data}` yoki multipart `file`; javob `{asset_id (32 hex), width_px, height_px}`; ≤2 MB (413), Pillow chegaralari (422), RGBA PNG'ga qayta kodlanadi, fon **olib tashlanmaydi**. Darvoza: `assert_can_view_contract` + `_assert_signable` → **tasdiqlanmagan kontraktda 409** (demak W4 da rasm asbobi `serverWrite: true`). Kontrakt boshiga ≤100 asset (409). `GET .../annotation-assets/{asset_id}` → `image/png`. Boshqa kontrakt `asset_id` → finalize 422 `unknown_annotation_asset`.
- **Z-tartib** tasdiqlangan: whiteout → imzolar → qolgan annotationlar (ro'yxat tartibida); whiteout joylashtirilgan imzoni yopmaydi.
- **Audit**: `by_kind` + `whiteouts: [{page,x_pt,y_pt,w_pt,h_pt}]`; matn/id/`group_id` yozilmaydi.
- **Unsupported kind** → 422 (`loc` da `kind`); frontend `isAnnotationsUnsupported` bunga mos.
- ⚠ Ochiq (v5 dan oldin mavjud): `/Rotate` 90/270 sahifalarda overlay xaritalash imzo/sana/matnni noto'g'ri joyga qo'yadi (strict xfail). 2026-09-25 skani: saqlangan 17 PDF / 800 sahifada **burilgan sahifa yo'q** — hozircha ta'siri yo'q.
