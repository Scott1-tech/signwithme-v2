# PDF muharriri v2 — arxitektura va bosqichli reja

Sana: 2026-09-28. Manba: Plan agenti (faqat o'qish) + foydalanuvchi qarorlari (§11). Qator raqamlari shu kun holatiga ko'ra — ishdan oldin joriy kod bilan tekshiring. Spike'ga bog'liq joylar **[S1]…[S8]** (§10.2). Oldingi reja: `docs/WORKSPACE-PLAN.md` (W0–W5, hammasi jonli).

## 0. Asosiy qarorlar

1. **Uch qatlam**, alohida saqlanadi:
   - **Content/structure ops** (`text.replace`, `text.delete`, `redact`, `image.remove`, `page.*`, `link.*`, `field.*`) — versiya PDF'iga PyMuPDF bilan «pishiriladi», faqat Save (commit) orqali.
   - **Markup** (annotations v4–v6 + yangi v7 turlari) — versiyaga pishirilmaydi; `documents.markup` da **server tomonda** (hozir `localStorage`), eksport/final paytida chiziladi.
   - **Imzo belgilari** (`signatures` qatorlari, slot sanalari) — server qatorlari, faqat finalda **bir marta**. Versiya PDF'iga imzo hech qachon tushmaydi → ikki marta chizilishi tuzilmaviy jihatdan imkonsiz.
2. **Geometriya:** PDF pt, **yuqori-chap origin, burilmagan sahifa fazosi, CropBox yuqori-chapiga nisbatan** (`signatures.bbox`, annotations v5, `lib/layout.ts`, `getPageText` bilan bir xil). Konversiya bitta joyda: `pdf_engine/adapter.py` **[S3]**.
   - **Aniqlik (ROTFIX 2026-09-30, o'lchangan):** `/Rotate`li sahifada IKKI fazo bor. *Burilmagan* (dvigatel): matn bloklari, havolalar, rasm/vidjet, redaksiya, v7 markup (`textbox`, `stamp`, `image2`, `shape2`, `ink2`, `square_mark`; `rotation_deg` burilmagan sahifada). *Ko'rsatilgan* (legacy `pdf_builder`): `signatures.bbox` (burilganda server qayta xaritalaydi), slotlar (`template_bbox`), sanalar, v1–v6 markup, `getPageText`/pdf.js matn qatlami. Frontend ekranda hammasini ko'rsatilgan sahifada saqlaydi; o'tkazish faqat `lib/viewport.ts` (`frameLayerStyle`, `mapRectPt`) va simda `lib/markupFrames.ts` da.
3. **Barqaror sahifa ID** `page_uid` (`pg_` + 12 hex), versiyalar orasida o'zgarmaydi. `document_pages.origin_page` = asl yuklamadagi N = shablon N-sahifasi (hozirgi taxmin `pdf_parser.py:180-183`). Solishtiruv, slotlar, imzolar shu xarita orqali.
4. `contracts.file_path` **o'zgarmas original**. «Imzolanadigan hujjat» hamma joyda `documents.signing_source(contract)` orqali o'qiladi (kesh/stale original himoyasi).
5. `contracts` ga ALTER yo'q; pointer `documents.current_version_id` da. Lokal SQLite'da faqat 2 nullable ustun (`signatures.page_uid`, `user_signatures.source`); yangi jadvallarni `create_all` yaratadi.
6. Mavjud matnni tahrirlash **server matn modeli** (PyMuPDF span/char) bo'yicha — tahrirni o'sha dvigatel qo'llaydi. pdf.js matn qatlami qidiruv va markup tanlash uchun qoladi.

## 1. Mavjud holat xaritasi

### 1.1 Qayta ishlatiladi
| Nima | Joy | V2 roli |
|---|---|---|
| Kirish nazorati | `api/deps.py:224` `assert_can_view_contract`, `:217` `get_contract_or_404` | Har `/document/*` da + yangi `assert_can_edit_document` |
| Imzolash darvozasi | `api/signatures.py:91-130` `_assert_signable` | O'zgarmaydi (`can_sign` invariant) |
| Override | `services/review_override.py`, `api/review.py:47-139` | O'zgarmaydi; review reset override maydonlarini tozalaydi |
| Finalize | `api/signatures.py:648-812` | Manba (`:740`) versiyaga, sahifa soni (`:667`), markup `documents.markup` dan |
| Annotation → builder | `api/signatures.py:815-882`, `:885-919` (PII: faqat sanoq + whiteout joylari) | Legacy markup uchun so'zma-so'z |
| PDF builder | `pdf_builder.py:479-569`, `:571-622` (z-tartib), `:907` `_Mapper` | Legacy markup/imzo/sana; `/Rotate` 90/270 xatosi B2 da (`test_api_annotations_v5.py:715-725` strict xfail) |
| Shriftlar | `services/fonts.py:45,67,108,130`, `assets/fonts/SOURCES.md` | Yangi oilalar shu reestrga |
| Storage | `storage.py:311-322` (atomik), `:348-368` `delete_prefix` (**rekursiv emas**), `:409`, `:58-66` | Versiya/eksport/asset fayllari **tekis** `contracts/<cid>/…`; yangi prefiks `user-assets` |
| Rasm qabul | `deps.py:125` `decode_annotation_image`, `:72` `decode_signature_image` | Galereya, typed imzo PNG |
| Sahifa soni | `rule_locator.py:284,312,322` | Versiyaga (`document_versions.page_count`) |
| Solishtiruv | `comparison_engine.py:371,416,427` | `present_pages` parametri |
| Parser | `pdf_parser.py:170,286,781` | `page_map` parametri (default identity) |
| Qayta tahlil | `workers/tasks.py:104` (`:141`), `api/contracts.py:300` | Joriy versiya + `page_map`; commit'dan keyin chaqiriladi |
| Maydon aniqlash | `field_detector.py:168-197` | «Detect form fields» |
| Rule chiziqlari | `rule_locator.py:51` | Finalda versiya baytlari |
| Audit | `services/audit.py` | Yangi action'lar (§4.6) |
| Throttle | `core/throttle.py`, `api/auth.py:24` | Share-link uchun umumlashtiriladi |
| Asbob registri | `workspace/tools/registry.ts:16,79`, `types.ts:47,56,59` | Yangi menyular/asboblar |
| Toolbar | `toolbar/FloatingToolbar.tsx` (804 q.), `PropertiesBar.tsx`, `useFloatingPanel.ts` | Spetsifikatsiya tartibi, Sejda tokenlari |
| Viewer | `hooks/usePdfViewer.ts:273`, `:903` `rotatePage` (faqat ko'rish) | Sahifa override, manba almashtirish |
| Matn qatlami | `text/PdfTextLayer.tsx`, `useTextSelection.ts`, `lib/textRuns.ts` | Qidiruv + markup |
| Annotatsiyalar | `lib/annotations.ts:1045,1170,1242`, `lib/annotationKinds/*`, `useAnnotations.ts:123-144` | Wire = legacy markup; saqlash serverga |
| Tarix | `state/useDocumentHistory.ts:42` | Yangi `'ops'` kanali |
| Finalize body | `useFinalize.ts:43`, `api/editor.ts` | Server markup bo'lsa `annotations` yuborilmaydi |
| Hujjat yuklash | `useContractDocument.ts:17` | Joriy versiya; 404 → `/file` |
| Workspace | `Workspace.tsx:170,180,185,611,1026,1064` | Sessiya va top bar |
| So'rov | `api/client.ts:47-93` (header parametri yo'q) | Revision **body** da |
| UI testlar | `tests/ui/_harness.py` (`:673` router) | Yangi route'lar; harness'ni bir vaqtda bitta agent |

### 1.2 To'qnashuvlar
1. `api/signatures.py:740` finalni asl fayldan quradi → **B1 da** tuzatiladi.
2. `rule_locator.py:293-301` sahifa soni keshi originalni sanaydi; `:339`, `signatures.py:304/312/366/667` ham.
3. Slot joyi `_resolve_signature_slot` (`signatures.py:158-178`), `_resolve_dates` (`:950`), `api/dates.py:48` — shablon sahifa raqami bilan → `page_uid` xaritasi kerak.
4. `pdf_parser.extract` (`:175-183`), `_document_fingerprint` (`:792`) sahifalarni pozitsiya bo'yicha juftlaydi.
5. `tests/ui/test_edit_text.py` (20) WE xatti-harakatini (whiteout+text) qulflaydi → foydalanuvchi «oq quti TAQIQ» → v2 bu testlarni **almashtiradi** (README «Replaced in F1»). Eski guruhlar legacy markup sifatida chizilaveradi.
6. Toolbar menyulari `markup|shapes|insert` → `text|forms|images|sign|whiteout|annotate|shapes`; `toolgroup-*` testid'lari ko'chiriladi, zaiflashtirilmaydi.
7. SQLite'da `PRAGMA foreign_keys` yoqilmagan → `ON DELETE CASCADE` ishlamaydi → har relationship `cascade="all, delete-orphan"`; `contract_admin.py:63-106` shu orqali tozalaydi.
8. `requirements.txt` da `pymupdf`, `pypdfium2` yo'q (venv'da `pymupdf 1.28.2`, `pypdfium2 5.13.0`) → qotiriladi.
9. Frontend burish (`PdfViewer.tsx:212`, `usePdfViewer.ts:903`) faqat ko'rish; haqiqiy `page.rotate` op'i alohida.

## 2. Ma'lumot modeli (`models/document.py`, `models/share.py`)

Turlar: `UUIDType`, `JSONType`, `UTCDateTime`.

**`documents`** (kontrakt bilan 1:1): `id`; `contract_id` FK CASCADE **UNIQUE** NOT NULL; `original_version_id`, `current_version_id` FK `document_versions` (`use_alter`) NULL; `markup` JSON default `[]`; `markup_revision` INT default 0; `markup_updated_by`; `created_by`; `created_at`, `updated_at`.

**`document_versions`**: `id`; `document_id` FK CASCADE; `version_no` (**UNIQUE(document_id, version_no)**); `parent_version_id`; `kind` `original|edit|submission`; `file_path` (v1 = `contract.file_path`, keyingilari `contracts/<cid>/version-<no>-<sha8>.pdf`); `file_hash` CHAR(64); `file_bytes`, `page_count`; `ops_applied` JSON (auditga **tushmaydi**); `op_summary` JSON; `content_changed`, `review_reset` BOOL; `engine`, `engine_ref`; `commit_request_id` **UNIQUE** NULL; `created_by`, `created_at`. Indeks `(document_id, version_no)`.

**`document_pages`**: `id`; `version_id` FK CASCADE; `position` (1-based); `page_uid`; `origin_page` NULL (qo'shilgan sahifada NULL); `rotation`; `width_pt`, `height_pt` (unrotated); `has_text`, `scanned`. UNIQUE(version_id, position), UNIQUE(version_id, page_uid), indeks `page_uid`.

**`document_drafts`** (hujjatga bitta): `id`; `document_id` **UNIQUE**; `base_version_id`; `revision`; `ops` JSON; `markup` JSON NULL; `payload_hash`; `owner_user_id`; `client_session_id`; `lease_expires_at`; `created_at`, `updated_at`.

**`assets`**: `id`; `scope` `user|contract`; `owner_user_id` NULL; `contract_id` NULL (CHECK: aynan bittasi); `kind` `image|stamp`; `label`; `storage_key`, `sha256`; `width_px`, `height_px`, `byte_size`; `source_asset_id`; `created_at`, `deleted_at`. Galereyadan joylashtirilganda kontrakt scope'iga **nusxalanadi** → kutubxonadan o'chirish joylashuvni buzmaydi.

**`export_jobs`**: `id`; `document_id`; `version_id`; `requested_by`; `options` JSON (`include_markup`, `flatten_forms`); `status` `queued|running|done|failed|cancelled`; `result_key`, `result_sha256`; `error`; `cancel_requested`; `created_at`, `finished_at`.

**Mavjud jadvallarga (additive, nullable):** `signatures.page_uid VARCHAR(24)`; `user_signatures.source VARCHAR(16)` (`drawn|uploaded|typed`, yozilgan matn saqlanmaydi).

**Bog'lanish:** `Contract.document` (`uselist=False, cascade="all, delete-orphan"`). `documents` **dangasa** yaratiladi (`services/documents.py: ensure_document`). `signing_source(contract) -> SigningSource{bytes_key, file_hash, page_count, pages, origin_to_position}`; `documents` yo'q → `contract.file_path` + identity (legacy bayt-bir xil).

**Migratsiya:** alembic `0005_document_editor_v2.py`; lokal `backend/scripts/migrate_local_editor_v2.py` (idempotent): online backup → `integrity_check` → `ALTER TABLE ADD COLUMN` (bo'lmasa) → `create_all(tables=[…])` → `integrity_check` + sanoqlar oldin=keyin. Server to'xtagan paytda.

**Atomik commit:** (1) validatsiya: draft revision, `base_version_id == current_version_id` (aks holda 409 `base_outdated`), kontrakt `completed`/`parsing` emas; (2) `commit_request_id` bor → o'sha natija; (3) engine → sha256 → `storage.put_bytes` (faqat markup/nostruktur commit'da fayl yo'q); (4) bitta tranzaksiya: `INSERT` versiya + sahifalar, `UPDATE documents … WHERE current_version_id=:base` (rowcount 0 → rollback → 409), `DELETE` draft, review reset, imzolar, audit, `COMMIT`; (5) xato → yangi fayl o'chiriladi; janitor `sweep_orphan_versions`; (6) `content_changed` → `process_contract_task`.

## 3. Operatsiyalar sxemasi

**Konventsiya:** pt, yuqori-chap, unrotated. `app/schemas/document.py` (pydantic, `extra="forbid"`, discriminated `kind`) ↔ `frontend/src/api/document.ts`. `Rect = {x_pt,y_pt,w_pt,h_pt}`. `id` `^[A-Za-z0-9_-]{1,64}$`. `layer` klientdan qabul qilinmaydi — server `kind` dan chiqaradi. Chegaralar: ops ≤2000, JSON ≤2 MB (413), `text.*` ≤5000 belgi, markup ≤500. Bir nishonga qayta tahrir = o'sha op almashtiriladi (bir xil `id`).

```ts
type Hex = `#${string}`;
interface TextRunStyle { text: string; font: FontFamilyId; size_pt: number; color: Hex; bold?: true; italic?: true }
interface TextTarget { block_id: string; line_ids: string[]; expect_text: string; bbox: Rect }  // mos kelmasa 409 stale_target
type ContentOp =
 | { id; kind:'text.replace'; page_uid; target: TextTarget; runs: TextRunStyle[]; box?: Rect;
     align?: 'left'|'center'|'right'|'justify'; line_height?: number; fit: 'wrap'|'shrink'|'overflow' }
 | { id; kind:'text.delete'; page_uid; target: TextTarget }
 | { id; kind:'redact'; page_uid; rects: Rect[]; fill: 'black'|'white'|'none' }
 | { id; kind:'image.remove'; page_uid; xref: number; bbox: Rect }
 | { id; kind:'page.insert'; new_page_uid; after_page_uid: string|null; size: 'same_as_previous'|{w_pt;h_pt} }
 | { id; kind:'page.delete'; page_uid }
 | { id; kind:'page.move'; page_uid; after_page_uid: string|null }
 | { id; kind:'page.rotate'; page_uid; rotation: 0|90|180|270 }
 | { id; kind:'link.add'|'link.update'; page_uid; link_id?; rect: Rect;
     target: {type:'uri'; uri} | {type:'page'; page_uid; y_pt?} }
 | { id; kind:'link.remove'; page_uid; link_id }
 | { id; kind:'field.add'|'field.update'; page_uid; field: FieldDef }
 | { id; kind:'field.remove'; name }
 | { id; kind:'field.value'; name; value: string|boolean|string[] };
interface FieldDef { name /*^[A-Za-z0-9_.\-]{1,64}$*/; type:'text'|'multiline'|'dropdown'|'radio'|'checkbox'|'signature';
  rect: Rect; required?; readonly?; tooltip?; default?; options?: string[]; export_value?; font?; size_pt?; max_len?; tab_index: number }
```

**Layer va review (§11 qarorlari bilan):**
| layer | review'ni qayta ochadi | kind'lar |
|---|---|---|
| `content` | **ha** | `text.replace`, `text.delete`, `redact`, `image.remove`, `page.insert`, `page.delete`, `page.move`, `field.value` |
| `structure` | yo'q | `page.rotate`, `link.*`, `field.add/update/remove` |
| `markup-content` | **ha** (Q1 qarori) | `textbox`, `whiteout` (legacy va v7), `stamp`, `image`/`image2`, `strikeout`, legacy `text`/`date` annotatsiyalar |
| `markup` | yo'q (faqat audit sanog'i) | `highlight`, `underline`, `ink`/`ink2`, shakllar (`rect`, `ellipse`, `line`, `arrow`, `shape2`), `note`, `check`, `cross`, `square_mark` |

`markup-content` o'zgarishi ham tasdiqlangan kontraktda review'ni qayta ochadi (commit orqali yoki markup saqlashda — qaysi nuqtada ekani B1 da aniq belgilansin: tavsiya — markup PUT'da tasdiqlangan kontraktda `markup-content` elementlari o'zgarsa `review_reset`).

**Dvigatel tartibi** (`pdf_engine/apply.py`): (1) `page.*` → yakuniy uid tartibi, o'chirilgan sahifadagi ops → `warnings`; (2) har sahifada: `redact` → `text.delete` → `text.replace` → `image.remove`; (3) fizik sahifa amallari; (4) `link.*` (maqsad o'chgan → olib tashlanadi + warning); (5) `field.*`, `field.value`, tab tartibi; (6) `/SWMPageId` **[S4]**; (7) `save(garbage=3, deflate=True)` **[S4]**.

**Markup (v4–v6 bilan):** wire = mavjud `AnnotationElement` (`schemas/contract.py:730`) o'zgarishsiz, `page` o'rniga `page_uid`. **v7 turlari:** `textbox` (runs, box, align, line_height, rotation_deg), `stamp` (preset|text, color, border, `date_text` qo'lda), `image2` (asset_id, rotation_deg, opacity), `shape2` (rect/ellipse/line/arrow, stroke_color, fill_color|null, opacity, dash solid|dash|dot, heads none|start|end|both), `ink2` (opacity, freehand_highlight), `square_mark`. Ranglar palitradan (**oq yo'q**), shaffoflik 0.05–1. Frontend `lib/markupWire.ts` `toWire`/`fromWire`. **localStorage migratsiyasi:** server `markup_revision==0` → PUT, kalit `.migrated` ga ko'chiriladi (o'chirilmaydi); ikkalasi farq qilsa bir martalik dialog (Import / Keep server) — **jim yo'qotish yo'q**; server API 404 → eski yo'l. WE guruhlari legacy markup; Objects'da «Convert to real edit» → `text.replace`.

## 4. Dvigatel (`backend/app/services/pdf_engine/`)

| Fayl | Vazifa |
|---|---|
| `__init__.py` | `open_pdf`, `inspect_pages`, `page_text_model`, `apply_ops`, `render_page_preview`, `flatten_markup`, `flatten_forms`, `EngineError` |
| `adapter.py` | **Yagona `import pymupdf`** (AGPL izolyatsiyasi), koordinata konversiyasi, redaksiya, matn/rasm/shakl/havola/vidjet, rotation, save |
| `model.py` | `PageRef`, `TextModel` (blocks→lines→spans→chars{c, quad}, font, flags, color, size, origin) |
| `ops.py` | Pydantic → dataclass |
| `apply.py` | §3 tartibi → `EngineResult{pdf_bytes, sha256, page_count, pages, changed_regions, warnings}` |
| `text_edit.py` | Nishon (`block_id`+`expect_text`, zaxira bbox), char-quad redaksiyasi **[S1]**, shrift **[S2]**, wrap/shrink/overflow |
| `fonts.py` | Reestr + almashtirish (Arial/Helvetica→Arimo/LiberationSans, Calibri→Carlito, Cambria→Caladea, Times→LiberationSerif, Courier→LiberationMono), `substituted_from` |
| `inspect.py` | `scanned`, `has_text`, Type3/vertikal/RTL → `editable:false` **[S8]** |
| `render.py` | 1 sahifali preview, LRU ≤64 / 200 MB |
| `markup.py` | v7 markup chizish (whiteout pastki / qolganlar yuqori bosqich) |
| `forms.py` | Vidjet yaratish/o'qish/flatten **[S5]** |
| `errors.py` | `stale_target`, `target_not_found`, `scanned_page_not_editable`, `undrawable_character`, `text_overflow`, `pdf_encrypted`, `pdf_corrupt`, `unsupported_span` |

**`text.replace`:** (1) `block_id`, `expect_text` (NFC) tekshiruvi → 409 `stale_target`; (2) faqat shu qatorlarning **char quad'lari** redaksiya (graphics/images NONE) — **oq quti yo'q** **[S1]**; (3) runs `box` ichida, birinchi baseline = asl; (4) `wrap` toshsa → 422 `text_overflow{lines_over, needed_h_pt}`, `shrink` 0.5 pt qadam (min 6), `overflow` → yoziladi + warning; qo'shni matn **hech qachon surilmaydi**; (5) glif tekshiruvi `fonts.embedded_refusal`.

**Nima qayerda:**
| Qism | Versiya | Eksport | Final |
|---|---|---|---|
| content/structure ops | ✔ | ✔ | ✔ |
| markup whiteout | ✗ | ✔ | ✔ imzodan PASTDA |
| markup qolganlari | ✗ | ✔ (`include_markup`, default ✔) | ✔ imzodan USTIDA |
| imzo, slot sanalari | ✗ | ✗ | ✔ **bir marta** |
| form vidjetlari | ✔ interaktiv | interaktiv / `flatten_forms` | **flatten** |

`services/final_pipeline.py: build_final`: v7 whiteout → `PdfBuilder.build_final_contract(..., finish=False)` → yuqori markup → flatten forms → metadata + sha256. Default `finish=True` yo'li bayt-bir xil. Server markup + body `annotations` ikkalasi → 422 `annotations_in_two_places`. Test: imzo XObject soni = `signatures` qatorlari soni.

**Idempotentlik:** `commit_request_id` UNIQUE; draft PUT `payload_hash`; eksport `(version_id, options_hash)`.
**Validatsiya:** `needs_pass` → 422 `pdf_encrypted`; ochilmasa `pdf_corrupt`; skan sahifada `text.*` → 422 `scanned_page_not_editable` («OCR — alohida funksiya»); chegara tashqarisi → 422 `op_outside_page` (matnsiz).
**Audit (matnsiz):** `document_created`, `document_version_committed` (version_no, parent, hash, by_kind, content_changed, pages_before/after, `redactions:[{page_uid, origin_page, rect}]`, `images_removed`), `document_draft_discarded`, `review_reset_by_edit`, `signatures_superseded`, `document_markup_saved`, `document_exported`, `asset_added/deleted`, `share_link_created/revoked` (token yo'q), `form_submission_received/accepted/rejected` (qiymatsiz).

## 5. Review integratsiyasi (`services/document_review.py`)

1. **`on_commit`** — `content_changed` (content yoki markup-content) va `review_status != 'pending'`: `review_status='pending'`, override maydonlari tozalanadi, `reviewed_by/at` saqlanadi, `status='parsing'` → reparse → `analyzed`; audit `review_reset_by_edit`.
2. **Imzolar (Q3 qarori):** content commit'da `signatures` bo'lsa → 409 `signatures_present{count}`; UI ochiq ogohlantirish bilan `discard_signatures:true` qayta yuboradi → qatorlar va fayllar o'chiriladi, har biriga `signature_removed{reason:"superseded_by_version"}`. Structure/markup commit'da imzolar qoladi, pozitsiya `page_uid` dan qayta hisoblanadi.
3. **Reviewer tuzatishlari** — `changed_regions` bilan kesishgan maydonlar uchun tashlanadi (audit `corrections_dropped`), qolganlari saqlanadi.
4. **Sahifa xaritasi:** `origin_to_position`; `extract(..., page_map)` (`None` → missing); `template_match(..., page_map)`; `pending_signature_slots(..., present_pages)`; `signing_comparison` slotga `page=position`, `page_uid`, o'chgan sahifadagi slotlar tashlanadi (frontend `slot.page` ni avvalgidek o'qiydi).
5. **Slot/joylashuv:** `_resolve_signature_slot` → `position = origin_to_position[page]` (`None` → 422 `page_removed`), qatorga `page_number` + `page_uid`; finalda `page_uid` dan qayta hisob; `_resolve_dates`, `api/dates.py:48` ham. Layout offsetlari `field_id` bo'yicha — xarita kerak emas. `page.rotate` 90/270 — B2 `_Mapper` tuzatmasigacha 422 `rotation_not_supported_yet`.
6. **Override o'zgarmaydi.** Sahifa o'chirilgani sababli yo'qolgan majburiy maydon = `missing_required`. Slotli sahifani o'chirish → `inconsistencies` `{type:"page_removed_with_slots", severity:"critical"}` (override mumkin, FMCSA emas); slotli sahifani o'chirishga ruxsat faqat reviewer/admin. `can_sign` tegilmaydi.

## 6. API (`api/documents.py`, prefiks `/api/contracts/{contract_id}/document`)

Har endpoint: `get_contract_or_404` + `assert_can_view_contract`; yozuvchi endpointlar + `assert_can_edit_document` (**Q2: admin, reviewer, yuklagan**; contractor-by-email faqat markup + fill). `completed` → 409 `contract_completed`. **Revision tanada** (`revision`); GET'da `ETag: "rev-N"` faqat ma'lumot.

| Metod va yo'l | So'rov | Javob / xatolar |
|---|---|---|
| `GET /document` | | `DocumentOut{document_id, current_version, versions_count, markup_revision, draft{owner_name,is_mine,lease_expires_at,revision}\|null, capabilities{can_edit,can_edit_markup,reason}}` (dangasa yaratadi) |
| `GET /document/versions` | | `[VersionOut{version_no, kind, file_hash, page_count, content_changed, review_reset, created_by_name, created_at, op_summary}]` |
| `GET /document/versions/{no}/file` | | PDF, `Cache-Control: no-store`, `X-Content-SHA256` |
| `GET /document/versions/{no}/pages` | | `[PageOut{page_uid, position, origin_page, rotation, width_pt, height_pt, has_text, scanned}]` |
| `GET /document/versions/{no}/pages/{page_uid}/text` | | `TextModel` (ixcham) / 404 |
| `GET /document/markup` | | `{revision, elements}` |
| `PUT /document/markup` | `{revision, elements}` | 200 `{revision}`; 409 `revision_conflict{current_revision}`; 409 `draft_active`; 422 |
| `POST /document/draft` | `{base_version_no, client_session_id, take_over?}` | 201/200 `DraftOut{revision, base_version_no, ops, markup, lease_expires_at}`; 409 `draft_locked{owner_name, lease_expires_at}`; 409 `base_outdated` |
| `GET /document/draft` | | `DraftOut` / 404 |
| `PUT /document/draft` | `{revision, client_session_id, ops, markup}` | 200 `{revision, saved_at, lease_expires_at, warnings}`; 409 `revision_conflict` / `draft_taken_over` / `base_outdated`; 413; 422 `{ops:[{id,reason}]}` |
| `POST /document/draft/lease` | `{revision}` | `{lease_expires_at}` (45 s da bir, lease 120 s) |
| `DELETE /document/draft` | `?revision=N` | 204 (Cancel) / 409 |
| `GET /document/draft/pages/{page_uid}/preview` | `?revision=N&hide=b12` | 1 sahifali PDF |
| `GET /document/draft/preview.pdf` | `?revision=N` | To'liq draft PDF |
| `POST /document/draft/commit` | `{revision, commit_request_id, discard_signatures?}` | 201 `CommitOut{version, content_changed, review_reset, reparse_started, remapped{signatures_kept, signatures_removed}, warnings}`; 409 `revision_conflict`/`base_outdated`/`signatures_present{count}`/`stale_target{op_id}`/`contract_completed`; 422 engine xatolari |
| `POST /document/exports` | `{version_no?, include_markup=true, flatten_forms=false}` | 202 `ExportJobOut{id,status}` |
| `GET /document/exports/{id}` · `/file` · `DELETE` | | holat / PDF (409 not ready) / 204 |
| `GET /document/versions/{no}/fields` | | `[FieldDef + value]` |
| `POST /document/detect-fields` | `{page_uids?}` | `{suggestions:[{suggestion_id, page_uid, type, rect, label, confidence}]}` (saqlanmaydi) |
| `POST /api/contracts/{cid}/annotation-assets` | mavjud + `{from_asset_id}` | galereyadan nusxa |
| `GET/POST /api/assets`, `GET /api/assets/{id}/image`, `DELETE /api/assets/{id}` | | faqat egasi; soft delete |
| `GET /api/fonts` | | `[{family, label, styles[], license, substitutes_for[], scripts[]}]` |
| `POST /api/signatures` | mavjud + `typed:{text ≤64, font}` | server PNG render, `source='typed'` |
| Share | | §8 |

Xato formati: `{error, message, details}`; 422 da so'rov tanasi/matn qaytmaydi.

## 7. Frontend

**Sessiya** (`workspace/session/`): `useEditSession.ts` (`mode view|edit`; `status idle|starting|saving|saved|dirty|offline|conflict|committing|error`; `start/save/cancel/resume/discardRecovered`); `useDraftAutosave.ts` (debounce **1500 ms**, maxWait **10 s**, bitta PUT navbat bilan, backoff 1/2/5/10 s, `pagehide` → `fetch(keepalive)` ≤64 KB, `localStorage["swm.draft.<cid>"]` zaxira). Recovery banner «Unsaved changes from 14:32 — Resume / Discard»; lokal zaxira yangiroq → «Restore from this browser». Konflikt modal «Reload» / «Download my changes (JSON)». `edit` rejimida Sign ▼ va «Generate final contract» o'chiq («Save or cancel your edits first»); markup ikkala rejimda.

**Top bar** `EditorTopBar.tsx`: `[Edit PDF]` yoki `[Save changes] [Cancel editing]`, save status (`editor-save-status`), `[Download PDF]` (eksport dialogi, «Include annotations (N)» — yashirilgan annotatsiyalar ham sanaladi, jim olib tashlanmaydi), `[Generate final contract]` (`editor-finalize`).

**Toolbar** (`ToolMenu = text|forms|images|sign|whiteout|annotate|shapes`): Select (V) | **Text ▼** (Add text T `textbox`, Edit text E v2, Date D) | **Links** | **Forms ▼** (Check K, Cross X, Filled square Q; Text field, Multiline, Dropdown, Radio, Checkbox, Signature box; Detect form fields) | **Images ▼** (Image M, Stamp gallery, Date/time stamp, Delete original image) | **Sign ▼** (Signature S, Initials I, My signatures…) | **Whiteout** W (▾ Redact) | **Annotate ▼** (Highlight H, Underline U, Strikeout C, Freehand highlight F, Draw P, Note N, Show/hide annotations) | **Shapes ▼** (Rect R, Ellipse O, Line L, Arrow A) | Undo | Redo. Sejda tokenlari `styles/sejda.css`, `.workspace` ichida scoped: `--sj-accent:#0099FF; --sj-canvas:#E9E9E9; --sj-border:1px solid #D0D0D0; --sj-radius:4px; --sj-ctl-h:34px; --sj-font:13px`. Boshqa sahifalar tegilmaydi. **Sejda skrinshotlari hali olinmagan** — kelsa vizual moslashtirish qayta tekshiriladi.

**Mavjud matnni tahrirlash UX:** `useServerTextModel.ts` (`/text`, LRU 10, versiya hash kaliti); `TextBlockLayer.tsx` hover ramka (`scanned`/`editable:false` → `not-allowed` + sabab); klik → `TextBlockEditor.tsx` (contenteditable, eng yaqin char chegarasida kursor; 2× = so'z, 3× = qator; Shift/Ctrl+strelka; oddiy matn paste NFC; Ctrl+B/I; Backspace/Delete). **Preview:** tahrirchi ochilganda `…/draft/pages/{uid}/preview?hide=<block>` (server faqat shu blok gliflarini olib tashlagan 1 sahifali PDF) → `usePdfViewer.setPageOverride`; javobgacha vaqtinchalik CSS maska (rang canvas medianidan, oq qotirilmaydi); tahrirchi matni server tanlagan **o'sha** shriftda (`@font-face`) → preview = eksport. Blur/Enter → `text.replace` (bir xil id) → autosave → yangi preview. Surish/o'lcham (o'ng tutqich), wrap `measureTextPt` bilan, server tasdiqlaydi. Toshish → qizil punktir zona + «Text overflows by N lines…» (Shrink to fit / Allow overlap / Keep editing) → `fit`. **Suzuvchi matn toolbar** `TextFloatingToolbar.tsx`: `Bold | Italic | Size ▼ (8,9,10,11,12,14,18,24,30,36,48,60,72 + qo'lda, ↑↓) | Font ▼ | Color ▼ | Link | Move | Duplicate | Delete`; `FontPicker.tsx` qidiruvli, joriy shrift belgisi, nom preview, «More fonts…» (litsenziya va «Calibri → Carlito (substitute)» chip), faqat `/api/fonts`. Move ✥ tanlashdan ajratilgan. Duplicate = yangi id, siljish, darhol tanlanadi, bitta undo. Undo: har blok commit = `'ops'` kanalida bitta qadam.

**F2 UI:** formalar (`workspace/forms/FieldLayer.tsx` — edit rejimida 8 tutqich + `FieldProperties.tsx`; fill rejimida HTML inputlar → `field.value`; nom to'qnashuvi ogohlantirishi + «Rename to X_2», radio istisno; `FieldDetectPanel.tsx` Accept/Reject/Accept all; tab tartibi drag). Rasmlar (`AssetGallery.tsx`, rotation, opacity, `image-remove`); Date/time stamp — sana/vaqt **bo'sh boshlanadi**, «Now» yo'q (09-25 qarori). Sign ▼: `SignatureModal` ga «Type» tab'i (3 ta OFL script shrift, `POST /api/signatures {typed}`); slot guruh surish, individual sozlash, masshtab, sana shrifti o'zgarishsiz. Annotate/Shapes: opacity, dash, fill_color, heads, freehand highlight; «Show annotations» faqat ekran. Sahifalar `PagesPanel.tsx`: raqam, eskiz, drag (`page.move`), burish (`page.rotate`), «Insert page here», o'chirish (dialog: shu sahifadagi imzo/slot/maydon/havola/markup soni); sahifa ops'dan keyin `draft/preview.pdf`, scroll `page_uid` bo'yicha tiklanadi, qidiruv keshi tozalanadi.

**Viewer:** `setPageOverride(page, bytes|null)` (≤6, oynadan tashqarida bo'shatiladi), `replaceSource(bytes, anchor{page_uid, fraction})`; `useContractDocument` joriy versiya (404 → `/file`).

## 8. Share-link (faqat LAN, faqat to'ldirish — Q4)

`share_links`: `id`, `document_id`, `contract_id`, `version_id`, `purpose='fill'`, `token_hash` CHAR(64) UNIQUE (token saqlanmaydi), `allowed_fields` JSON, `recipient_label`, `expires_at` (default 72 soat, maks 14 kun), `max_submissions` (default 1), `submission_count`, `revoked_at/by`, `last_used_at`, `created_by/at`.
Token `secrets.token_urlsafe(32)`, faqat yaratishda bir marta ko'rsatiladi; URL `http://10.31.0.58:5173/s#t=<token>` (**fragment** — log/Referer'ga tushmaydi); `pages/SharedForm.tsx` `X-Share-Token` header bilan. LAN: `settings.share_allowed_cidrs` (10/8, 172.16/12, 192.168/16, 127/8), `request.client.host`, XFF e'tiborsiz, tashqi → 404. Noma'lum/muddati o'tgan/bekor/limit — **bir xil 404**; `KeyedThrottle` IP boshiga 10 daqiqada 20 muvaffaqiyatsiz → 429; `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.
Ommaviy: `GET /api/share/session`, `GET /api/share/file` (versiya PDF, markup/imzosiz), `POST /api/share/submissions {values, submitter_name?, submitter_email?}` (faqat `allowed_fields`, tur/options/max_len/required/glif; qiymatlar `core/pii.py` bilan shifrlanadi).
Ichki: `POST/GET/DELETE /api/contracts/{cid}/share-links`, `GET …/submissions`, `POST …/submissions/{id}/accept` (→ yangi draft `field.value`, egasi Save → content commit → review pending), `POST …/reject {reason}`.
`form_submissions`: `id`, `share_link_id`, `document_id`, `version_id`, `values_encrypted`, `submitter_name/email`, `ip_address`, `user_agent`, `status received|accepted|rejected`, `reviewed_by/at`, `reject_reason`, `created_at`. Kontrakt ID ommaviy javoblarda chiqmaydi.

## 9. Bosqichlar

Har bosqich oxiri: pytest (bazaviy **911 passed / 2 xfailed**, kamaymaydi), `npm run typecheck` 0, `npm run build` 0, `python tests\ui\run_all.py` (bazaviy **255/255**), **jonli E2E haqiqiy backend bilan** (ZZSYNTH, izolyatsiyalangan headless Chromium, LAN IP, zaxira + tozalash + sanoqlar teng). Frontend — `_staging\<faza>` nusxasida, direktor sinxronlaydi. Backend fayllari jonli papkada tahrirlanadi (server `--reload`siz — restartgacha ta'siri yo'q), restart direktor tomonidan.

- **B1-0** (blokirovka): `app/schemas/document.py` + `frontend/src/api/document.ts` — API kontrakti muzlatiladi.
- **B1 Fondament:** `requirements.txt` (`pymupdf`, `pypdfium2`), `models/document.py`, relationship'lar, `signatures.page_uid`, alembic 0005 + lokal migratsiya skripti, `pdf_engine/*` (faqat `text.replace`/`text.delete`), `services/documents.py`, `services/document_review.py`, `api/documents.py`, integratsiya (`api/signatures.py`, `rule_locator.py`, `comparison_engine.py`, `pdf_parser.py`, `workers/tasks.py`, `api/dates.py`, `api/contracts.py`, `audit.py`), markup GET/PUT. Testlar: `test_pdf_engine_text.py`, `test_api_documents.py`, `test_api_document_commit.py`, `test_document_review_reset.py`, `test_finalize_versions.py`, `test_migrate_local.py`.
- **B2 Dvigatel ops + shriftlar:** redact, image.remove, link.*, page.*, v7 markup, `final_pipeline.py`, `pdf_builder` (`finish`, `_Mapper` 90/270), `Asset`/`ExportJob`, `api/assets.py`, eksportlar, `typed_signature.py`, yangi shrift oilalari (Arimo, Carlito, Caladea, DejaVu, Amiri, script) + `SOURCES.md`, `api/fonts.py`, `gen_font_coverage.py`. Testlar: `test_pdf_engine_ops.py`, `test_pdf_engine_pages.py`, `test_page_map.py`, v5 rotation xfail → pass, `test_api_exports.py`, `test_api_assets.py`, `test_typed_signature.py`.
- **B3 Formalar:** `pdf_engine/forms.py`, `field.*`, fill, tab, flatten, fields/detect endpointlari. Testlar `test_pdf_engine_forms.py`, `test_api_detect_fields.py`.
- **F1a** (B1 bilan parallel): `styles/sejda.css`, `EditorTopBar.tsx` qobig'i, toolbar qayta tashkil (`types.ts`, `registry.ts`, `FloatingToolbar.tsx`), `test_toolbar_v2.py`, eski toolbar testlari ko'chiriladi.
- **F1b** (B1 dan keyin): `api/document.ts`, sessiya/autosave, `markupWire.ts`, server sync + migratsiya, viewer override, `TextBlockLayer/Editor`, `TextFloatingToolbar`, `FontPicker`, edit-text v2, `textbox`, `'ops'` kanali, harness route'lari. Testlar `test_session.py`, `test_text_edit_v2.py` (o'rniga), `test_markup_migration.py`.
- **F2 Qolgan UI:** link, forma asboblari, square-mark, stamp, image-remove, freehand-highlight, redact; forms/assets/pages panellari; Type imzo; PropertiesBar; eksport dialogi. Testlar `test_links.py`, `test_forms.py`, `test_images.py`, `test_sign_menu.py`, `test_annotate_v2.py`, `test_shapes_v2.py`, `test_pages.py`.
- **F3 Share + final:** `models/share.py`, `api/share.py`, `api/share_public.py`, throttle, config, alembic 0006, `SharedForm.tsx`, `ShareDialog.tsx`, `SubmissionsSection.tsx`, `useFinalize` (server markup → `annotations` yo'q), `FinalContract.tsx` (versiya raqami). Testlar `test_api_share_links.py`, `test_api_submissions.py`, `test_share.py`, `test_final_v2.py`.
- **QA:** to'liq regressiya, `tests/visual/` (pypdfium2 golden ≤0.5 pt), 44 bet (commit <3 s, preview <300 ms p95), 100% va 209% zoom ±0.5 pt, access-control matritsasi, spetsifikatsiyaning 18 test bandi, JURNAL.

**Parallellik:** B1 ∥ F1a; B2 ∥ F1b; B3 ∥ F2 (forma UI'dan tashqari); F3 oxirida. Doimiy ketma-ket: `api/signatures.py` (B1→B2→F3), `schemas/contract.py` (B2→F3), `tests/ui/_harness.py` (F1b→F2→F3), `Workspace.tsx`, `lib/annotations.ts`, `pdf_builder.py` (faqat B2).

## 10. Xatarlar

### 10.2 Spike'ga bog'liq [S]
- **[S1]** char-quad redaksiyasi qo'shni gliflar/chiziqlar/jadvallarni buzmasligi; bo'lmasa qator darajasi + vertikal siqish; u ham bo'lmasa B1 to'xtaydi.
- **[S2]** asl subset shriftni qayta ishlatish; default metrik-mos almashtirish (ochiq ko'rsatiladi).
- **[S3]** CropBox≠MediaBox va `/Rotate` koordinatalari (Δ ≤0.1 pt).
- **[S4]** PyMuPDF saqlagan faylni pypdf/pdfplumber/pdf.js o'qishi, hajm, `/SWMPageId`.
- **[S5]** radio/combobox/signature vidjetlari va flatten.
- **[S6]** asl rasmni o'chirish.
- **[S7]** 44 betda commit/preview vaqti.
- **[S8]** Type3/vertikal/RTL/`Tz`/`Tc` span'lar `editable:false`.

### 10.2.1 Spike natijasi (2026-09-29, PyMuPDF 1.28.2) — MAJBURIY
- **Usul:** `get_text("rawdict")` → faqat nishon belgilarning **yadro** to'rtburchagi (kenglikning ichki 60 %, balandlikda baseline−0.55·size … baseline−0.15·size) → `add_redact_annot(fill=False)` + `apply_redactions(images=PDF_REDACT_IMAGE_NONE, graphics=PDF_REDACT_LINE_ART_NONE, text=PDF_REDACT_TEXT_REMOVE)` → yangi matn asl baseline/chap/o'lcham/rangda (`insert_text` yoki `TextWriter`; ko'p qatorli — `insert_textbox`, rect `y0 = baseline − ascender·size`) → saqlashdan oldin `doc.subset_fonts()`. Natija: 3 ekstraktorda eski matn yo'q, Δ baseline/chap **0**, qator tashqarisi **0 px**, fon/jadval chizig'i/`____` saqlanadi, **oq quti yo'q**.
- 🚩 **MuPDF xatosi:** content filtri `Tj` dan keyingi `0 0 Td` ni no-op deb o'chiradi → keyingi glif ≈3 pt suriladi. Chrome/Skia (Docusign) PDF'larida **bo'sh redaksiya ham** 44 betdan 36 tasini buzdi (529 271 px). `clean_contents()` ham buzadi; absolyut `Tm` ga aylantirish yordam bermaydi. **Tuzatish (adapter.py'da majburiy):** har redaksiya/`clean_contents` oldidan sahifa va Form XObject'larida `0 0 Td`/`TD` → `1e-4 0 Td` (`1e-5` juda kichik — yana nol deb tashlanadi). Tokenlash pypdf `ContentStream` bilan. Natija 41/44 bet 0 px, 3 tasida 1–16 px (piksel yaxlitlash). Regressiya testi: bo'sh redaksiyadan keyin sahifaning qolgan qismida >16 farq yo'q, golden = Chrome-uslubidagi PDF. MuPDF yangilanganda qayta tekshirilsin.
- **[S2] o'zgardi:** asl subset shriftni qayta ishlatish amalda mumkin emas (nishon shriftda 54 glif, yangi matnning 17 belgisi yo'q; 16/56 shrift qoplaydi) → **default Liberation metrik-mos almashtirish** + `substituted_from`; asl shrift faqat barcha belgilar uchun `has_glyph` ✔ bo'lsa. Liberation'da ✔ ✓ ☐ yo'q.
- **[S3] ✔:** rawdict va `insert_text` bitta koordinatada, **derotatsiyasiz** (`derotation_matrix` qo'llansa noto'g'ri); ekranga `page.rotation_matrix`, orqaga teskarisi. `/Rotate` 90/180/270 + CropBox≠MediaBox Δ 0.
- **[S4] ✔:** pypdf, pdfplumber, pdf.js 4.8.69 o'qiydi; hajm `subset_fonts` bilan asldan kichik (2.97→2.54 MB). Incremental saqlash asl baytlarni prefiks sifatida saqlaydi (audit uchun variant).
- **[S5] qisman:** text/multiline/combobox/checkbox/signature ✔, qiymat, `bake` flatten ✔; **radio** — `field_value=True` bilan `ValueError: bad xref`, guruh `/V` Off → B3 da `/AP` holat nomlari va `/V` past darajada.
- **[S6] ✔** (`delete_image`, redaksiya `IMAGE_REMOVE`). **[S7] ✔:** tahrir ~55 ms, saqlash ~110–190 ms (+subset ~200–280 ms), commit ≈0.3 s; preview 1 bet 85–120 ms; barcha betlar rawdict 0.5–0.8 s → **keshlash shart**. **[S8]:** namunalarda Type3/vertikal/RTL/skan yo'q; `Tc`/`Tz` aniqlash ochiq.
- **`text.replace` qoidalari:** `insert_textbox` rc<0 bo'lsa **hech narsa yozmaydi** → avval scratch sahifada quruq sinov; shrink 0.5 pt qadam (min 6); **qisman almashtirish = moslik boshidan qator dumini qayta yozish** (aks holda ustma-ust); to'qnashuv = yangi matn yadro polosasi ∩ sahifadagi boshqa belgilar (o'z belgilari hisobga olinmaydi) → 422 `text_overflow` yoki ogohlantirish; `insert_htmlbox` ISHLATILMASIN (siljish, pdfplumber topmaydi).
- **Fayl holati:** 60 % kesilgan fayl `is_repaired=True` bilan ochiladi — `pdf_corrupt` emas, ogohlantirish; axlat → `FileDataError`. Parol → `needs_pass`.
- Litsenziya: "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License" (PyPI; pymupdf.readthedocs.io/en/latest/about.html).

### 10.3 Boshqa
- **AGPL:** faqat ichki LAN'da muammo yo'q; internetga chiqsa/tarqatilsa majburiyat — `adapter.py` izolyatsiyasi.
- Storage o'sishi (har content commit to'liq PDF 2–5 MB) — `DEPLOYMENT.md` ga hisob.
- SQLite bitta yozuvchi — lease + CAS yetarli, engine tranzaksiyadan tashqarida.
- Amiri: RTL rad qoidasi (`fonts.py:127`) saqlanadi.
- Kriptografik (PAdES) imzo doiradan tashqarida; UI'da «visual / field / cryptographic» farqi yoziladi.
- `test_edit_text.py` (20) qaror bo'yicha almashtiriladi.

## 11. Foydalanuvchi qarorlari (2026-09-28)

1. Dvigatel — **PyMuPDF** (Apryse rad etildi).
2. Tasdiqdan keyin **asl matn yoki sahifa** o'zgarsa → tasdiq bekor, qayta review.
3. **Q1:** tasdiqdan keyin **mazmun qo'shimchalari** (yangi matn qutisi, whiteout, redaksiya, shtamp, rasm, strikeout, legacy text/date annotatsiyalar) ham review'ni qayta ochadi; highlight, underline, shakllar, qalam, sticky note, ✔/✖ — yo'q (faqat audit).
4. **Q2:** mazmunni tahrirlab saqlash — **admin, reviewer, kontraktni yuklagan**; email orqali kirgan contractor faqat markup va forma to'ldirish.
5. **Q3 (Q5 plan):** imzo qo'yilgandan keyin mazmun tahrirlansa — **ochiq ogohlantirish va tasdiqdan keyin imzolar o'chiriladi** (audit qoladi), qayta imzolanadi.
6. **Q4:** share-link — **faqat LAN, faqat to'ldirish** (imzo mavjud oqimda).
7. Slotli sahifani o'chirish — faqat reviewer/admin, kritik `page_removed_with_slots` (izoh bilan override mumkin) — direktor default.
8. Shtamp sanasi — faqat qo'lda (09-25 qarori), istisno yo'q — direktor default.
9. Ilgari rad etilgan sahifa o'chirish/qo'shish/burish va **typed imzo** — spetsifikatsiya bo'yicha qaytariladi.
10. Hammasi **mavjud SignWithMe ichida** quriladi (alohida loyiha emas).
