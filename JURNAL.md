# JURNAL — Signwithme1 (Contract Signing App)

FMCSA haydovchi shartnomalarini shablon asosida tekshirish, taqqoslash va
imzolash tizimi. Bo'sh shablon yuklanadi, tizim to'ldiriladigan maydonlarni
aniqlaydi va `field_schema` yozadi; keyingi shartnomalar shu sxemaga qarab
tekshiriladi, yonma-yon ko'rib chiqiladi, canvas'da imzolanadi va yakuniy PDF
audit izi bilan yig'iladi.

Stek: React 18 + TypeScript + Vite (frontend), FastAPI + SQLAlchemy 2.0 +
Alembic + Pydantic v2 (backend), Celery + Redis (worker), PostgreSQL 15 +
MinIO/S3 yoki fayl tizimi (ma'lumot).

---

## Holat

| Element | Holat (2026-09-05) |
|---|---|
| Manba kodi | `C:\PTG\signwithme` ga ochilgan |
| Ishga tushirish yo'li | Docker'siz mahalliy (SQLite + inline task + local storage) |
| Backend | `backend/local_run.py` orqali uvicorn, 0.0.0.0:8000 |
| Frontend | Vite dev server, 0.0.0.0:5173 |
| Docker / Postgres / Redis / MinIO | Ishlatilmaydi (Docker o'rnatilmagan) |
| Kirish doirasi | Faqat xususiy tarmoq (LAN + Tailscale) |
| `app/` kodi | O'zgartirilmagan |

---

## 2026-09-05

### 1. Loyiha ochildi

Arxiv `C:\Users\ultra\Downloads\Telegram Desktop\Signwithme1-main.zip` dan
`C:\PTG\signwithme` ga chiqarildi. PTG konvensiyasiga ko'ra loyihaning o'z
jurnali (shu fayl) shu kuni ochildi.

### 2. Muhit tekshiruvi

- Windows 10 Pro
- Python 3.12.10
- Node v24.18.0, npm 11.16.0
- **Docker O'RNATILMAGAN** — shuning uchun README dagi asosiy yo'l
  (`docker compose up --build -d`) bu mashinada ishlamaydi. `docker-compose.yml`,
  `docker-compose.gcp.yml` va `entrypoint.sh` yo'llari chetlab o'tildi.

### 3. Docker'siz mahalliy ishga tushirish yo'li tanlandi

Uchta tashqi xizmat o'rniga repo o'zining test muhitidagi yechimlari olindi:

- **PostgreSQL → SQLite.** Alembic'ning boshlang'ich migratsiyasi
  `0001_initial_schema.py` faqat Postgres uchun (pgcrypto kengaytmasi, JSONB,
  INET ustunlar, plpgsql trigger), ya'ni `alembic upgrade head` SQLite'da
  o'tmaydi. Sxema o'rniga `Base.metadata.create_all` orqali quriladi — xuddi
  `tests/conftest.py:121` dagidek. Buni repo o'zi ham nazarda tutgan: modellar
  dialektdan mustaqil ustun turlarida yozilgan (README, "Tests").
- **Redis/Celery → inline task.** Broker yo'q, shuning uchun `analyze_template_task`
  va `process_contract_task` `.delay()` o'rniga sinxron chaqiriladi. Usul —
  `bind=True` task'ning `.__wrapped__` dekoratsiyalanmagan funksiyasini olish,
  ya'ni `tests/conftest.py:169` dagi bilan bir xil.
- **MinIO/S3 → `STORAGE_BACKEND=local`.** Bu vaqtinchalik mock emas: repo'dagi
  haqiqiy `LocalStorage` backend'i, README bo'yicha kichik hostlar uchun tavsiya
  qilingan variant. Fayllar baribir faqat API orqali beriladi.

Buning uchun `backend/local_run.py` yozildi. U faqat to'rt ish qiladi: CWD ni
`backend/` ga qadab `.env` ni ishonchli yuklaydi, SQLite sxemasini yaratadi,
ikki Celery task'ni inline'ga almashtiradi va 0.0.0.0 da xizmat qiladi.
**`app/` ichidagi hech narsa o'zgartirilmadi.**

### 4. LAN orqali kirish

- Backend: uvicorn `0.0.0.0:8000` (`API_BIND` / `API_PORT` env orqali boshqariladi).
- Frontend: Vite `host: true` → `0.0.0.0:5173`.
- Mashina manzillari: LAN IP **10.31.0.58**, Tailscale **100.81.27.127**.

Faqat xususiy tarmoq. README aniq ogohlantiradi: ilova ochiq internetga
chiqarish uchun mo'ljallanmagan; tavsiya etilgan shakl — Tailscale/WireGuard
orqali kirish. Shu qoidaga rioya qilindi, hech qanday port tashqariga ochilmadi.

### 5. `backend/.env` yaratildi

- `DATABASE_URL` — SQLite fayli
- `STORAGE_BACKEND=local` + mahalliy `STORAGE_DIR`
- `JWT_SECRET` — yangi tasodifiy qiymat yaratildi (rotatsiya qilindi)
- `PII_ENCRYPTION_KEY` — yangi tasodifiy qiymat yaratildi (rotatsiya qilindi)
- `CORS_ORIGINS` — LAN IP qo'shildi

**Sirlarning qiymati bu jurnalda saqlanmaydi.** Faqat `backend/.env` da.
`app/config.py:68` ilovani namunaviy yoki bo'sh `JWT_SECRET` bilan (debug'dan
tashqari) ishga tushishdan bosh tortadi — shuning uchun bu qadam majburiy.
`PII_ENCRYPTION_KEY` yo'qolsa saqlangan SSN/EIN qiymatlarini qayta o'qib
bo'lmaydi (README, "Backups") — kalitni alohida joyda zaxiralang.

---

## 2026-09-07 — Uchta xatolik tuzatildi (appsec ko'rigi)

Ko'rib chiqishda topilgan uchta xatolik surgik tarzda tuzatildi. **`can_sign`
imzolash darvozasi (comparison_engine.py:187-191) HECH O'ZGARTIRILMADI** — har
bir tuzatish yo faqat ko'rsatish (display), yo darvozani QATTIQROQ qiladigan
qo'shimcha. Hech biri darvozani bo'shashtirmaydi.

### XATOLIK 1 — CDL/format soxta-ijobiy (substring mosligi)

- **Sabab:** `_mentions` maydon id ichidan token'ni *substring* sifatida
  qidirardi, ya'ni id'i tarkibida 'cdl' bo'lgan har qanday maydon (masalan
  Yes/No maydoni) soxta CDL/SSN format ogohlantirishini keltirib chiqarardi.
- **Tuzatish:** `_mentions` endi faqat butun `_`-bilan-ajratilgan segmentlarni
  taqqoslaydi — [comparison_engine.py:361-366](signwithme/backend/app/services/comparison_engine.py#L361-L366)
  (`parts = set(field_id.lower().split("_"))`).

### XATOLIK 2 — "highlight yo'q" (ikki qatlamli: backend + frontend)

Format ogohlantirishlarida bbox bo'lmagani uchun UI qutini chiza olmasdi.

- **Backend:** `_validate_field` qaytaruvchi dict'ga maydonning o'z bbox'i
  qo'shildi — [comparison_engine.py:256](signwithme/backend/app/services/comparison_engine.py#L256)
  (`"bbox": field.get("bbox")`). Kross-field muammolar (bir nechta maydon)
  ataylab bbox'siz qoldi.
- **Frontend tip:** `Inconsistency` interfeysiga `bbox?: BBox | null`
  qo'shildi — [comparison.ts:28](signwithme/frontend/src/types/comparison.ts#L28).
- **Frontend flatten:** umumiy `pushIssue` (ham inconsistencies, ham warnings
  uchun) endi bbox'ni o'tkazadi — [useComparison.ts:97](signwithme/frontend/src/hooks/useComparison.ts#L97)
  (`bbox: issue.bbox ?? null`). FMCSA push (:114) haqiqatan kross-field
  bo'lgani uchun `bbox: null` qoldi.
- **Xavfsizlik:** faqat display-qo'shimcha (bitta bbox kaliti). `can_sign`
  faqat severity'larni o'qiydi — bunga ta'sir qilmaydi.

### XATOLIK 3 — imzo aniqlashda soxta-manfiy

- **Sabab:** imzo label'lari faqat ikki nuqta (`:`) bilan tugaganda aniqlanardi;
  underscore fill bilan tugagan ("Signature ___", "X_____") o'tkazib yuborilardi
  → talab qilingan imzo maydoni yozilmasdi.
- **Tuzatish:** uchta imzo pattern'i `\s*:` o'rniga `\s*(?=[:_])`
  lookahead'ga o'tkazildi — [field_detector.py:83-85](signwithme/backend/app/services/field_detector.py#L83-L85).
  Lookahead underscore'larni ISTE'MOL QILMAYDI, shuning uchun `_fill_region`
  hali ham yoziladigan qutini o'sha underscore'larga anchor qiladi. `:` yoki `_`
  darhol so'zdan keyin kelishi shart bo'lgani uchun matn ichidagi "signature"
  hech qachon mos kelmaydi.
- **Xavfsizlik:** ko'proq label aniqlanadi → ko'proq talab qilingan imzo maydoni
  → `can_sign` darvozasi QATTIQROQ (hech qachon bo'shroq emas). `has_ink`
  (pdf_parser.py:223-249) O'ZGARTIRILMADI — uni bo'shashtirish soxta-ijobiy
  xavfini tug'diradi (darvozani zaiflashtiradi); faqat tavsiya sifatida qoldi.

### TuzatilMAGAN (ataylab)

- **XATOLIK 4 — "required-by-default" bloklash semantikasi** — bu xavfsizlik
  darvozasi (bo'sh majburiy maydon imzolashni bloklaydi). Tuzatilmadi, faqat
  tavsiya sifatida qoldirildi.

---

## 2026-09-07 — Imzolash: aniqlash bo'shliqlari (B1–B8) + foydalanuvchi imzo kutubxonasi (Ish A)

**Kontekst / taxminni tekshirish.** Talab: "ilova faqat shartnomani TAHLIL qiladi,
IMZOLASH imkoni yo'q" degan taxminni sinash. **Taxmin noaniq bo'lib chiqdi** —
to'liq imzolash quvuri *shartnoma doirasida* allaqachon mavjud (STORE/SELECT/PLACE/
SAVE): imzo kanvasda chiziladi → base64 PNG place endpoint'iga boradi → pypdf/
reportlab yakuniy PDF quradi (pdf_builder.py) → sha256 bilan saqlanadi. Haqiqiy ikki
bo'shliq bor edi: **Ish A** — foydalanuvchiga bog'langan qayta-ishlatiladigan imzo
kutubxonasi yo'q edi (eski `Signature` jadvali `contract_id` ga bog'langan,
`user_id` yo'q); **Ish B** — `field_detector.py` da imzo/sana aniqlashning sakkiz
bo'shlig'i (B1–B8). Foydalanuvchi qarori: **Ikkalasi (B → A)** — avval xavfsiz
additiv aniqlash tuzatishlari, keyin imzo kutubxonasi. Ikkalasi yozildi va sinaldi.

### Ish B — imzo/sana maydon-aniqlash bo'shliqlari (B1–B8)

Yuqoridagi XATOLIK 3 (uchta imzo pattern'ini `\s*(?=[:_])` lookahead'ga o'tkazish)
ning **kengaytmasi** — regression bazasi (182 maydon / 41 imzo / 56 sana) o'sha
lookahead tuzatuvini allaqachon o'z ichiga oladi; Ish B undan **yuqoriga** qo'shadi.
FAQAT [field_detector.py](signwithme/backend/app/services/field_detector.py)
o'zgardi (boshqa hech bir servis fayli emas — mtime bilan tasdiqlangan).

- **B2** — `Signature` label'i orasidagi matnli qism (masalan "Signature of
  Contractor") endi tortiladi: `Signature[^:_\n]{0,40}?(?=[:_])`. *Tuzatish jarayonida:*
  boshidagi `\b` OLIB TASHLANDI — `_` ham `\w` belgisi bo'lgani uchun "Signature___"
  da so'z chegarasi yo'q, `\b` mos kelmay p45 da sana pattern'i qatorni o'g'irlab
  maydon tipini ag'darardi (1 LOST). Boshidagi `(?<!\w)` gvardi so'z-ichi mosligini
  allaqachon bloklaydi.
- **B3/B4** — yangi imzo label'lari: `Initials?`, `Signed`, `Sign here`, `/s/`,
  `Recorded by` (barchasi lookahead bilan).
- **B6** — `Date` label'i ham `\s*(?=[:_])` lookahead'ga.
- **B5/B1** — grafik gid-chiziqlardan anchor (`_fill_anchors` width>40 / height<2.0
  + `_anchor_from_guides` dy oynasi −6.0..26.0) `_detect_line_fields`/`_fill_region`
  orqali ulandi.
- **B8** — `_pair_signatures_with_dates` juftlash oynasi `dy < -40.0 or dy > 55.0`
  ga kengaytirildi.
- **Ataylab TASHLANDI:** **B7** — "Contractor's Signature" apostrof varianti eng
  umumiy `signature` sifatida allaqachon tutiladi; apostrof-variant qo'shish faqat
  overlap'ni yutib 21 id'ni qayta nomlaydi (21 LOST, 0 yangi qamrov). **"By"
  pattern'i** — p41 dagi "...for review by:" o'rtacha-gap prozasi soxta REQUIRED
  maydonga aylanardi; uning o'rniga uzunroq/proza-xavfsiz `Recorded by` qoldirildi.
- **Regression (47-sahifali shablon PDF):** 182/41/56 → **195/53/57** (Δ +13 maydon
  / +12 imzo / +1 sana), **LOST_COUNT=0** (hech bir asosiy maydon yo'qolmadi;
  set-diff har bir sanoq ≥ baza ekanini tasdiqladi).
- **Xavfsizlik invarianti:** har yangi imzo/sana maydoni `required` — additiv-faqat.
  Shu sabab `can_sign` (comparison_engine.py:187-191) faqat **QATTIQROQ** bo'lishi
  mumkin, hech qachon bo'shroq emas. `has_ink` (pdf_parser.py:223-249) va
  pdf_builder.py TEGILMADI (ikkalasi ham 2026-08-29 mtime — mustaqil tasdiqlangan).

### Ish A — foydalanuvchi imzo kutubxonasi (per-user, qayta-ishlatiladigan)

Yangi, mavjud imzolash quvuriga yondosh — server placement mantig'i O'ZGARMADI
("Option A": saqlangan imzoning base64 PNG'i O'ZGARMAGAN place endpoint'ining
`image_data` maydoniga uzatiladi).

- **DB:** yangi `user_signatures` jadvali —
  [0002_user_signatures.py](signwithme/backend/alembic/versions/0002_user_signatures.py)
  (id UUID, `user_id` NOT NULL + FK users.id ON DELETE CASCADE, label, image_path,
  signature_hash, is_default, created_at, `idx_user_signatures_user` index). Eski
  `contract_id`-bog'langan `Signature` teginilmadi.
- **API:** [user_signatures.py](signwithme/backend/app/api/user_signatures.py)
  `prefix="/api/signatures"` — POST "" (201, audit `user_signature_saved`),
  GET "" (**egaga-cheklangan** `where(user_id == user.id)`), GET "/{id}",
  GET "/{id}/image" (StreamingResponse image/png, `Cache-Control: no-store`),
  DELETE "/{id}" (audit `user_signature_deleted`).
- **IDOR himoyasi:** `_owned` guard — egalik mos kelmasa `NotFoundError` (**403
  emas, 404** → mavjudlik sirini oshkor qilmaydi). JWT `user.id` (UUID) —
  soxtalashtirib bo'lmaydigan egalik FK'si (security.py:68-78).
- **Frontend:** `userSignaturesApi` (list/save/remove + `imageAsDataUrl` Option A
  ko'prigi: authorized fetch → blob → dataURL), `SignaturePage` da picker modal
  (saqlangan imzoni tanlash) + draw rejimi; tip `UserSignature` (ataylab bayt yo'q).
- **Sinov:** backend **99/99 yashil** (12 yangi test, shu jumladan **3 IDOR
  manfiy testi**); frontend `tsc --noEmit` toza + to'liq `vite build` muvaffaqiyatli.
  Hech qanday regressiya yo'q; qattiq invariantlar teginilmadi.

**Holat:** ikkala track ham TUGADI va tekshirildi. **Commit/push/deploy QILINMADI**
(ruxsat berilmagan — signwithme git-repo ham emas; o'zgarish doirasi fayl mtime'lari
bilan tasdiqlandi). Keyingi bosqich uchun yagona darvoza — commit/deploy ruxsati.

---

## 2026-09-07 — Deploy urinishi: Lokal Docker stack (BLOKLANDI — Docker yo'q)

**Qaror:** foydalanuvchi "Lokal Docker stack" ni tanladi — shu mashinada
`docker compose up -d --build` (Postgres + Redis + backend + worker + frontend),
migratsiya `0002` konteyner ichida avtomatik ishlashi kerak. Ish **devops**
agentiga taqsimlandi.

**Natija: BLOKLANDI.** Bu mashinada konteyner dvigateli umuman yo'q.
Preflight dalillari:

- `docker` / `docker compose` → *command not found* (Bash) va
  *CommandNotFoundException* (PowerShell).
- Barcha standart o'rnatish yo'llari yo'q: `C:\Program Files\Docker\...`,
  `C:\ProgramData\DockerDesktop\...`, `%LOCALAPPDATA%\Docker\bin\docker.exe`.
- Docker jarayoni/xizmati yo'q (`com.docker.backend`, `dockerd`,
  `com.docker.service`); `C:\` bo'yicha rekursiv `docker.exe` qidiruvi bo'sh.
- WSL distro yo'q; muqobil runtime (podman/nerdctl/rancher) ham yo'q.
- Bu MEMORY bilan mos: bu ish-stansiyada Docker tashlangan (worktrack/screenview
  faqat-EXE). Shuningdek "Ma'lum cheklovlar" #4 buni oldindan aytgan.

**Bajarilgan (qaytariluvchan, kod tegilmagan):** devops agent ildizda
`C:\PTG\signwithme\.env` yaratdi (avval mavjud emas edi — hech narsa
qayta yozilmadi; ilova/manba kodi O'ZGARMADI) — kuchli tasodifiy sirlar bilan
(`DB_PASSWORD`, `JWT_SECRET`, `PII_ENCRYPTION_KEY`, `SEED_ADMIN_*`,
`STORAGE_BACKEND=local`, `FRONTEND_BIND=127.0.0.1`, `DEBUG=false`).
Docker paydo bo'lgach, bitta buyruq (`docker compose up -d --build`, faqat baza
compose — GCP override YO'Q) deploy'ni yakunlaydi.

**Tekshirib bo'lmadi:** migratsiya `0002` (user_signatures jadvali +
`alembic_version=0002`) qo'llanmadi; salomatlik tekshiruvi (backend :8000,
frontend :3000, `docker compose ps`) ishlamadi. Hech narsa masofaga
yuborilmadi; boshqa loyihalar tegilmadi.

**Kutilmoqda — foydalanuvchi qarori:** (1) Docker Desktop o'rnatishga ruxsat
(winget `Docker.DockerDesktop`; WSL2/Hyper-V, ehtimol qayta yuklash kerak —
qaytarilishi og'ir amal); yoki (2) deploy'ni Docker mavjud boshqa hostga
ko'chirish (xuddi shu `.env` + baza compose bilan). Qaror kelgunча hech qanday
o'rnatish/qaytmas amal bajarilmaydi.

---

## 2026-09-07 — Xatolik tuzatildi: "PDF ni edit qilish imkoni bo'lmayapti" (appsec)

Foydalanuvchi xabari: shartnomani ko'rib chiqib imzolashda "PDF ni edit qilish
imkoni bo'lmayapti". Sabab uch qismli ekan; ikkitasi asosiy, biri UX. **Hech bir
INVARIANT fayl va `can_sign` mantiqi o'zgartirilmadi** — bloklash to'g'ri
ishlayotgan edi, faqat unga yo'l va sabab yo'q edi.

### Ildiz sabab (uch qism)

1. **`SignaturePage.tsx`** — `blocked` bo'lsa canvas'dagi va slot'dagi barcha
   interaksiya jimgina yutilardi (hech qanday sabab/yo'nalish ko'rsatilmasdan).
   Foydalanuvchiga bu "edit qilib bo'lmayapti" bo'lib ko'rinardi.
2. **`ReviewPage.tsx`** — Approve tugmasi `disabled={busy || !result?.can_sign}`.
   `can_sign=false` bo'lsa shartnomani UI orqali umuman tasdiqlab bo'lmasdi →
   SignaturePage doim bloklangan qolardi.
3. **`contracts.ts`** — `correctField` (PATCH) API-mijozda bor edi, lekin
   **hech qaysi komponent uni chaqirmasdi** → maydonni to'g'rilab qayta
   taqqoslash uchun UI yo'l yo'q edi. Kritik nuqson hech qachon yechilmasdi.

### Tuzatma

**A qism — `frontend/src/pages/SignaturePage.tsx` (NON-invariant):** jim yutishni
farqlangan bloklash xabari bilan almashtirdim, **`blocked` darvozasini
zaiflashtirmasdan**:
- L190 `blocked` gate O'ZGARMADI: `review_status !== 'approved' || can_sign === false`.
- L194 `notApproved`, L195–202 `criticalIssues` (severity==='critical' bo'yicha
  missing_required+inconsistencies+fmcsa_violations ni sanaydi).
- L213 `<PdfViewer interactive={!blocked}`; L215/L234 canvas va slot bosishida
  `if (blocked) return;` (O'ZGARMADI).
- L264–288 farqlangan alert: tasdiqlanmagan bo'lsa "reviewer approve qilishi
  kerak"; aks holda "N ta kritik masala qolgan, imzolash bloklangan" + **"Go to
  review"** tugmasi (`/contracts/${id}/review` ga o'tadi).

**B qism (asosiy tuzatma) — mavjud `correctField` endpoint'ini Review oqimiga
uladim:**
- `frontend/src/pages/ReviewPage.tsx` (NON-invariant): L48–64 `correctField`
  orkestratori — `correctField` (PATCH) → `rerunComparison` (POST /compare) →
  `refresh()` (GET detail + GET compare). Bu tartib SHART: `correct_field`
  yakka o'zi taqqoslashni qayta yurgizmaydi, `refresh` esa faqat saqlangan
  natijani qayta GET qiladi. L66–68 `canCorrect` = reviewer + `review_status ===
  'pending'`. L121–126 `can_sign=false` bo'lsa ogohlantirish hint'i. L139 Approve
  darvozasi `disabled={busy || !result?.can_sign}` O'ZGARMADI.
- `frontend/src/components/ComparisonSidebar.tsx` (NON-invariant, yagona
  iste'molchi = ReviewPage): L18–20 yangi propslar (`onCorrectField`,
  `canCorrect`, `correcting`); L137–178 har bir maydonli masala uchun inline
  "Correct value" → input + "Save & re-check" formasi (faqat
  `canCorrect && onCorrectField && issue.fieldId` bo'lganda).

### Jonli tekshiruv (Playwright MCP, jonli backend + Vite)

- **A qism — CONFIRMED** (shartnoma 4120ac6f, reviewing/rejected, can_sign=false):
  SignaturePage farqlangan blok xabari + "Go to review" CTA ko'rsatildi, canvas
  non-interaktiv, finalize disabled.
- **B qism (mexanizm) — CONFIRMED** (shartnoma 3827021f, pending): "State"
  (p1_state) maydonini "Texas" ga to'g'rilab "Save & re-check" bosildi. Tarmoq
  zanjiri aynan kutilgan tartibda: **PATCH /fields/p1_state (200) → POST
  /compare (200) → GET /{id} (200) → GET /compare (200)**. Natija: completion
  60→61%, filled 71→72, missing_required (kritik) 42→41, to'g'rilangan maydon
  missing bucket'dan chiqdi. **can_sign `false` bo'lib qoldi**, Approve disabled
  qoldi — ya'ni mexanizm ishlaydi VA `can_sign` darvozasi zaiflashmagan.
- **Imzolash yo'li — CONFIRMED** (shartnoma 6fc89a8d, completed/approved,
  can_sign=true): SignaturePage bloklanmagan (alert yo'q, CTA yo'q), canvas bor,
  completion 100%. Approve/imzo tasdiqlangan shartnomada baribir ishlaydi.

### Cheklov (PLAUSIBLE)

can_sign `false`→`true` ga o'tib Approve qayta yoqilishi to'liq jonli
ko'rsatilmadi: 3827021f dagi qolgan 41 kritik masala imzo/sana tipida (matn
bilan to'g'rilab bo'lmaydi). Ammo bu kod bilan tasdiqlangan: `rerun_comparison`
(`backend/app/api/contracts.py:156–180`) can_sign ni qayta hisoblaydi va Approve
darvozasi (`ReviewPage.tsx:139`) o'shanga bog'langan. To'g'rilash MEXANIZMI —
CONFIRMED.

### O'zgartirilmagan (INVARIANT — tegilmadi)

`backend/app/services/comparison_engine.py`, `field_detector.py`, `pdf_parser.py`,
`pdf_builder.py`, `frontend/src/hooks/useComparison.ts`,
`frontend/src/types/comparison.ts`, `backend/app/api/user_signatures.py`, alembic
versiyalari. `can_sign` mantiqi zaiflashtirilmadi. `tsc --noEmit` EXIT=0.

---

## Ishga tushirish

Ikki terminal kerak.

**1. Backend** (`C:\PTG\signwithme\backend`):

```powershell
cd C:\PTG\signwithme\backend
.\.venv\Scripts\Activate.ps1        # virtual muhit
python local_run.py
```

Konsolda `db`, `storage`, `cors`, `serving` qatorlari chiqadi — ishga tushishdan
oldin shu to'rt qatorni tekshiring. `alembic upgrade head` ni ishlatmang: bu
konfiguratsiyada u ataylab chetlab o'tilgan (3-band).

**2. Frontend** (`C:\PTG\signwithme\frontend`):

```powershell
cd C:\PTG\signwithme\frontend
npm run dev
```

**3. Ochish:**

- Shu mashinadan: `http://localhost:5173`
- LAN dan: `http://10.31.0.58:5173`
- Tailscale dan: `http://100.81.27.127:5173`

Yangi manzil qo'shilsa, uni `backend/.env` dagi `CORS_ORIGINS` ga ham qo'shish
kerak, aks holda brauzer API so'rovlarini bloklaydi.

### 2026-09-07 — Mahalliy ishga tushirish tasdiqlandi (Docker'siz, jonli HTTP bilan)

**Natija: ISHLADI.** Yuqoridagi ikki-terminal yo'li (`local_run.py` +
`npm run dev`) shu mashinada haqiqiy HTTP so'rovlar bilan tekshirildi.
Docker'siz: SQLite + SQLAlchemy `create_all`; alembic migratsiyalar bu yo'lda
ataylab chetlab o'tildi (3-band).

**Backend** — FastAPI/uvicorn, `.venv` python, PID 25404, `0.0.0.0:8000`:

- `GET /health` → 200 `{"status":"ok","service":"Contract Signing App"}`
- `GET /docs`, `GET /openapi.json` → 200

**Frontend** — Vite dev server, PID 31832, `http://127.0.0.1:5173` → 200.
CORS ruxsat etilgan originlar: `localhost:5173`, `127.0.0.1:5173`,
`10.31.0.58:5173`, `100.81.27.127:5173`.

**Ish A — foydalanuvchi imzo kutubxonasi:** `/api/signatures` marshrutlari
OpenAPI'da mavjud (`GET`/`POST /api/signatures`; `GET`/`DELETE
/api/signatures/{signature_id}`; `GET /api/signatures/{signature_id}/image`).
Autentifikatsiyasiz `GET` → 401 (mounted + himoyalangan). `user_signatures`
jadvali `create_all` orqali yaratildi; `0002_user_signatures.py` migratsiya bu
yo'lda uxlab turadi (kutilgan holat).

**Ish B — shartnoma taqqoslash dvigateli:** jonli sinovda
`/api/contracts/{id}/compare` → 200. Xavfsizlik xatti-harakatlari kuzatildi:
rate-limit → 429; IDOR (egalik mos kelmaganda) → 401/404; path-traversal
bloklandi (`../../etc/passwd` → 404).

**Eslatma (hal qilingan, blokager emas):** eski/stale backend jarayoni
(PID 34168) 8000-portni band qilib turgani uchun dastlabki urinish (background
task `bwkfrzqf7`) exit 255 bilan tugagan edi; devops agenti uni to'xtatib,
portni bo'shatib, yangi backend (PID 25404) ni ishga tushirdi.

**Kod holati:** hech qanday manba fayl ham, `.env` ham o'zgartirilmadi — faqat
run, qaytariluvchan holat.

---

## 2026-09-07 — IP access tuzatishi (tekshiruv)

**Shikoyat:** boshqa qurilmadan / mashina IP manzili orqali kirilganda "access
denied", localhost'da esa ishlaydi.

### Xulosa (avval)

Hozirgi ishlab turgan konfiguratsiyada muammo **qayta chiqmadi** — LAN va
Tailscale IP orqali kirish uchdan-uchgacha ishlaydi (dalillar quyida). "Access
denied" ning klassik sababi — server faqat loopback'ga (127.0.0.1) bog'langanda
uzoq qurilma "connection refused" oladi — bu yerda **allaqachon bartaraf etilgan**:
ikkala server ham `0.0.0.0` da tinglaydi va firewall Node/Python uchun inbound
ruxsatga ega. Hech qanday fayl o'zgartirilmadi (ishlab turgan sozlamaga tegilmadi).

### Manba (fayl:qator) — bog'lanish holati

- **Frontend Vite** `frontend/vite.config.ts:8` — `host: true` → `0.0.0.0:5173`
  (IPv4 + IPv6). `netstat`: `0.0.0.0:5173` va `[::]:5173` LISTENING.
- **Backend uvicorn** `backend/local_run.py:75` — `host = os.getenv("API_BIND",
  "0.0.0.0")`, `:76` port 8000. `netstat`: `0.0.0.0:8000` LISTENING.
- **CORS** `backend/app/main.py:22-29` — `allow_origins=settings.cors_origin_list`
  (wildcard EMAS). `backend/.env` `CORS_ORIGINS` da LAN (10.31.0.58) va Tailscale
  (100.81.27.127) manzillari 5173-port bilan allaqachon bor.
- **TrustedHostMiddleware YO'Q** (`main.py` da qo'shilmagan) → "Invalid host
  header" bo'lishi mumkin emas.
- **Frontend so'rovlari** `frontend/src/api/client.ts` — barcha `fetch` NISBIY
  yo'l (`/api/...`) ishlatadi; grep bo'yicha `frontend/src` da hardcode qilingan
  `localhost`/`127.0.0.1`/absolyut backend URL **yo'q** → uzoq brauzer API'ni
  o'zining localhost'iga yo'naltirmaydi. So'rov Vite proxy orqali (same-origin)
  backendga o'tadi, shuning uchun proksilangan oqimda CORS umuman qo'zg'almaydi.

### Reproduksiya dalillari (LAN 10.31.0.58, Tailscale 100.81.27.127)

1. `GET http://10.31.0.58:5173/` → **HTTP 200** (Vite root uzoq IP'da ochiladi).
2. `curl -H "Host: desktop-ptg.local:5173" http://10.31.0.58:5173/` → **HTTP 200**
   — Vite hostname'ni **bloklamaydi** ("Blocked request. This host is not
   allowed." chiqmaydi). Sabab: o'rnatilgan `vite` = **5.4.11**
   (`frontend/package.json` da qat'iy pin, `^` yo'q). Host-check majburlashi
   faqat 5.4.12+ da; hozirgi versiya bloklamaydi. Qo'shimcha: IP-manzilli Host
   sarlavhasi yamlangan Vite'da ham hech qachon bloklanmaydi — faqat non-IP
   hostname'lar. Foydalanuvchi IP orqali kiradi → `allowedHosts` bu holatga
   umuman aloqador emas.
3. `POST http://10.31.0.58:5173/api/auth/login` (yomon parol) →
   **HTTP 401** `{"error":"unauthorized","message":"Incorrect email or
   password","details":{}}` — proksilangan API uzoq IP orqali backendga yetadi.
4. `GET http://10.31.0.58:8000/health` → **HTTP 200**
   `{"status":"ok","service":"Contract Signing App"}` — backend to'g'ridan-to'g'ri
   LAN'da javob beradi.
5. `GET http://100.81.27.127:5173/` → **HTTP 200** — Tailscale IP orqali ham.

### Rad etilgan farazlar (dalil bilan)

- **Vite `allowedHosts` "Blocked request"** — RAD. Vite 5.4.11 host-check qilmaydi
  (test 2 → 200) va IP orqali kirishga bu mexanizm baribir tegishli emas.
- **"Invalid host header" (TrustedHostMiddleware)** — RAD. Bunday middleware
  qo'shilmagan (`main.py`).
- **Faqat loopback bog'lanish (connection refused)** — hozirgi holatda RAD;
  ikkala server `0.0.0.0` da (netstat), bu asl simptomning ehtimoliy sababi
  bo'lgan, ammo allaqachon tuzatilgan.
- **Firewall bloki** — RAD. Profillar yoqilgan, ammo Node/Python uchun inbound
  allow qoidalari bor; LAN ulanishlari yuqoridagidek 200 qaytaradi (firewall
  bloki odatda timeout/refused beradi, "access denied" emas).
- **Frontend API base URL hardcode localhost** — RAD (grep: 0 ta moslik).

### Qo'llangan o'zgartirish

**Yo'q.** Ishlab turgan sozlama IP orqali to'g'ri xizmat qilmoqda; ishlayotgan
uzoq sessiyani buzmaslik uchun `vite.config.ts` ga speculativ tahrir kiritilmadi.

### Ixtiyoriy kelajakka tayyorlash (xavfsizlikni saqlagan holda)

Agar kelajakda IP emas, **hostname** (masalan `desktop-ptg.local`) orqali kirish
kerak bo'lsa VA Vite 5.4.12+ ga yangilansa, host-check faollashadi. O'shанда
**wildcard EMAS**, aniq hostlarni qo'shish kerak (Vite keyingi qayta ishga
tushishida kuchga kiradi):

```ts
server: {
  port: 5173,
  host: true,
  allowedHosts: ['desktop-ptg.local'],   // faqat kerakli hostlar, hech qachon 'all'/true
  proxy: { '/api': { target: process.env.VITE_API_TARGET ?? 'http://localhost:8000', changeOrigin: true } },
}
```

Hozircha (IP orqali kirish) bu shart emas.

### Tekshirish (uzoq qurilmadan)

- Brauzer: `http://10.31.0.58:5173` yoki `http://100.81.27.127:5173` →
  UI ochilishi kerak (login sahifasi).
- `curl -s -o NUL -w "%{http_code}" http://10.31.0.58:5173/` → **200**.
- `curl -s -o NUL -w "%{http_code}" http://10.31.0.58:8000/health` → **200**.
- Yangi manzil (LAN/Tailscale IP) qo'shilsa, uni `backend/.env` `CORS_ORIGINS`
  ga ham `:5173` bilan qo'shing (proksilanmagan to'g'ridan-to'g'ri chaqiruvlar
  uchun; proksi oqimiga shart emas, ammo izchillik uchun tavsiya).

**Kod holati:** manba fayl ham, `vite.config.ts` ham, `.env` ham
o'zgartirilmadi — faqat tekshiruv/reproduksiya. INVARIANT fayllarga tegilmadi.

---

## Ma'lum cheklovlar

1. **Bu prod konfiguratsiya emas.** SQLite + inline task + mahalliy fayl
   saqlash — bir mashinadagi ishlash uchun. Prod baribir PostgreSQL (JSONB) +
   Redis/Celery.
2. **Alembic migratsiyalari bu muhitda ishlamaydi.** Sxema `create_all` dan
   keladi, ya'ni migratsiya tarixi qo'llanilmagan. Postgres'ga o'tilganda sxema
   migratsiyalardan qayta qurilishi kerak; SQLite bazasi ko'chirilmaydi.
3. **Inline task = sinxron.** Shablon tahlili va shartnoma qayta ishlash HTTP
   so'rov ichida bajariladi, katta PDF bo'lsa so'rov uzoq turadi. Navbat,
   qayta urinish va worker izolyatsiyasi yo'q.
4. **Docker yo'li hech qachon jonli ishlatilmagan** (README, "Known limits") —
   compose faqat ko'rib chiqish va CI image build bilan tasdiqlangan. Docker
   o'rnatilganda birinchi ishga tushishda muammo chiqishi kutiladi.
5. **Aniqlash aniqligi haqiqiy shablonda o'lchanmagan** (README, "Not yet
   built") — detektor faqat sintetik PDF larda sinalgan. Haqiqiy hujjat
   yuklanib, Template Manager'da har bir sahifa qo'lda tekshirilishi kerak.
6. **OCR yo'q.** Skanerlangan PDF aniqlanadi va rad etiladi, o'qilmaydi.
   Kriptografik PDF imzo (PAdES) ham yo'q — imzolar rasm sifatida qo'yiladi.
7. **JWT bekor qilinmaydi** (8 soatgacha amal qiladi), parolni tiklash oqimi
   yo'q, login cheklovi jarayon ichida — bu repo'ning o'z cheklovlari.
8. **Avtomatik zaxira yo'q.** `scripts/backup.sh` Postgres uchun yozilgan; bu
   SQLite konfiguratsiyasiga mos kelishi tekshirilmagan — **ma'lumot yetarli
   emas**.
9. **Uchdan-uchgacha sinov o'tkazilmagan** — shablon yuklash → shartnoma
   yuklash → ko'rib chiqish → imzolash → yakunlash zanjiri bu muhitda hali
   tekshirilmadi. `pytest` (87 test) ham ishga tushirilmagan.

---

## 2026-09-07 — IP access: aniq root-cause tekshiruvi (devops, aniqlashtirish)

**Shikoyat (qayta):** boshqa qurilmadan IP orqali kirilganda ishlamayapti,
localhost ishlaydi.

### Jonli dalil (netstat, o'sha daqiqada)

- `TCP 0.0.0.0:5173 LISTENING 31832` — node (Vite) 0.0.0.0 da tinglaydi.
- `TCP 0.0.0.0:8000 LISTENING 25404` — python (uvicorn) 0.0.0.0 da tinglaydi.
- **`TCP 10.31.0.58:5173 ESTABLISHED 10.31.0.229:60834` (PID 31832)** — MASOFAVIY
  LAN qurilma (10.31.0.229) AYNAN HOZIR 5173 ga ulangan va qabul qilingan.
  → **5173 boshqa qurilmadan LAN orqali yetib boradi; firewall uni bloklamaydi.**
- 8000 dagi barcha ESTABLISHED — PID 29676/16092 = **iVMS-4200 (Hikvision CCTV)**
  ning CHIQUVCHI ulanishlari (10.10.0.25:8000 / 10.31.0.90:8000). signwithme
  backendiga masofaviy KIRUVCHI ulanish YO'Q — bu kutilgan (Vite `/api` ni
  server tomonda localhost:8000 ga proksilaydi, `vite.config.ts:9-13`).
- Manzillar: LAN `10.31.0.58` (Ethernet 2, DHCP), Tailscale `100.81.27.127`.
- Profillar: Ethernet 2 = **DomainAuthenticated** (Domen), Tailscale = **Private**
  (IPv4Connectivity = NoTraffic → Tailscale hozir faol emas).

### Oldingi yozuvni tuzatish

Yuqoridagi 2026-09-07 "IP access tuzatishi" yozuvi "firewall Node/Python uchun
inbound allow qoidalariga ega" degan edi — **noaniq**. Bu sessiyada tekshiruv
(Get-NetFirewallPortFilter / application-filter / inbound allow enumeratsiyasi)
5173 yoki 8000 uchun, node/python/uvicorn dasturi uchun **hech qanday allow
qoidasi YO'Qligini** ko'rsatdi. Firewall siyosati barcha profilda
BlockInbound,AllowOutbound. LAN kirishi aniq qoida bilan emas, **Domain profili
(korporativ tarmoq) tufayli de-fakto** ruxsat etilgan. Yakuniy natija ("5173 IP
orqali ishlaydi") to'g'ri, faqat mexanizm noto'g'ri tavsiflangan edi.

### Root-cause (aniq)

- LAN (10.31.0.x, Domain) qurilmalar uchun firewall/tarmoq **sabab EMAS** —
  5173 hozir masofaviy qurilmadan ishlayapti (yuqoridagi ESTABLISHED). Agar
  foydalanuvchi shu LAN'da bo'lib ham "ishlamayapti" desa, sabab ehtimol
  **noto'g'ri URL** (`:8000` yoki `https://` yoki eskirgan IP) — ilova FAQAT
  `:5173` da.
- Faqat **Private/Tailscale yoki domendan tashqari** segmentda 5173 uchun aniq
  allow qoidasi yo'qligi bloklashi mumkin (Private BlockInbound). Bu holatgina
  firewall qoidasi kerak.

### Qo'llangan o'zgartirish

**Yo'q.** (a) Firewall LAN'ni bloklamayapti, shuning uchun qoida qo'shish LAN
xatti-harakatini o'zgartirmaydi; (b) bu session admin emas (PTG\ultra) —
`New-NetFirewallRule` baribir "Access denied" beradi. Config/INVARIANT fayllar,
`vite.config.ts`, `.env` — tegilmadi.

### Tavsiya (agar nishon qurilma Tailscale/Private bo'lsa, admin bilan)

Xavfsizlikni saqlab, FAQAT 5173, FAQAT Domain+Private (Public EMAS, 8000 EMAS):
`New-NetFirewallRule -DisplayName "signwithme dev 5173" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow -Profile Domain,Private`

Test URL (faqat 5173): `http://10.31.0.58:5173` (LAN),
`http://100.81.27.127:5173` (Tailscale, avval Tailscale ulanishi shart).

---

## 2026-09-10 — Bo'sh imzo/sana sloti "to'ldirilgan" deb o'tib ketishi tuzatildi (appsec)

**Shikoyat:** "tekshiruvda imzo qo'yilishi kerak bo'lgan bo'sh joylar e'tiborga
olinmay o'tib ketyapti." Gipoteza (bo'sh slotdagi chiziq vektor stroke sifatida
"siyoh" hisoblanadi) so'zma-so'z RAD ETILDI — `has_ink` keng yassi chiziqni
filtrlaydi, qisqa chiziq 1 stroke (< 4), `rects` umuman hisobga olinmaydi.
Shikoyat esa uchta BOSHQA ildiz sabab bilan TASDIQLANDI (reproduksiya +
haqiqiy DB):

| # | Ildiz sabab | Dalil |
|---|---|---|
| A | `has_ink` matn fallback'i (`pdf_parser.py`) bbox ichidagi HAR QANDAY ≥3 belgili matnni imzo deb olardi; imzo bbox esa chiziqdan 34pt YUQORIGA cho'ziladi (`field_detector.py` `SIGNATURE_HEIGHT`), ya'ni yuqoridagi shablon prozasi/yorlig'i qutiga tushadi | Bo'sh shablon `ad1a4236` → `p1_contractor_signature=True` (`'eement:'`); bo'sh `befea049` → `p1_company_rep_signature=True` (`'ollowing pages. This document is'`); kontrakt `6fc89a8d` `can_sign=True/completed` faqat TEXT shoxi bilan |
| B | Rasm ostonasi `0.05` — yuqori qatordagi imzo rasmi pastdagi company_rep qutisiga 6–18 % oqib kirib `True` berardi | Bo'sh `befea049` `p12/p14/p25/p30/p31_company_rep_signature=True`, slotda rasm YO'Q |
| C | Shablonlar bo'sh forma emas: `befea049` (CD_Package) 40 slotdan 33 tasida bir xil imzo rasmi (`f972970eed…`), `02fc4235` (CD Contract) 33/33 imzo + 41/41 sana to'ldirilgan — imzolangan hujjat shablon sifatida yuklangan; kontrakt `c190c353` shablon bilan bayt-bayt bir xil | DB: `c190c353` 33 slot `has_signature=True conf=0.95 in_missing=False` — hammasi shablondan meros |
| D | Sana: `__/__/____` placeholder `_` olib tashlangach `'//'` → `is_blank=False` → "to'ldirilgan"; qo'shni `Date:` yorlig'i 14pt qutiga oqib kiradi | Bo'sh `befea049` `p37_date='//'`, `p10_date='Date:'`; DB'da 5/5 kontraktda `p10_date filled=yes in_missing=False` |

Mavjud test `test_unsigned_signature_region_is_not_reported_as_signed` o'tardi,
chunki `conftest` imzo chizig'i ustida 34pt ichida hech qanday matn chizmasdi.

### Qo'llangan tuzatishlar (faqat "qat'iyroq" yo'nalish — soxta True qo'shilmaydi)

1. **Shablon-diff (C)** — `backend/app/services/pdf_parser.py`:
   `PdfParser.with_template_baseline(template_pdf, schema)` har imzo/sana
   maydoniga ADDITIVE `template_baseline` kaliti qo'shadi
   (`image_hashes`, `strokes`, `text_tokens`, `prefilled`); mavjud kalitlar
   o'zgarmaydi. `extract` da: shablon rasmi bilan hash-bir xil rasm e'tiborsiz,
   shablon stroke soni ayiriladi, shablon token'lari matndan olib tashlanadi.
   Yondashuv: baseline `analyze_template_task` da BIR MARTA hisoblanib
   `field_schema` ga saqlanadi (`backend/app/workers/tasks.py`); DB'dagi eski
   (baseline'siz) shablonlar uchun `process_contract_task` shablon
   `source.pdf` dan lazily hisoblab additive saqlaydi — qayta tahlil shart
   emas. Audit `TEMPLATE_ANALYZED` details ga `prefilled_signatures`,
   `prefilled_dates` qo'shildi (shablon o'zi to'ldirilgan bo'lsa ko'rinadi).
   **2026-09-17 da bekor qilindi** — hash/stroke/token ayirish haddan oshgan edi (kontraktda ko'rinib turgan imzo = kontraktda bor); geometrik himoyalar (A, B) saqlangan. 2026-09-17 bo'limiga qarang.
2. **Matn oqib kirishi (A)** — `_PageContext.text_on_rule`: matn faqat chiziq
   qatori + undan 8pt yuqoridagi polosadan (`SIGNATURE_TEXT_BAND`; 12pt eng
   tor leading dan kichik) olinadi; `:` bilan tugagan yorliq token'lari
   tashlanadi; terilgan ism (chiziq ustida) hali ham `True`.
3. **Qo'shni rasm (B)** — rasm faqat (quti qoplami ≥ 0.05) VA (markazi qutining
   pastki yarmida — chiziq ustida, YOKI rasmning ≥ 60 % qismi quti ichida)
   bo'lsa hisoblanadi. O'lchov: haqiqiy stamp markazi doim pastki yarmda
   (0.07–0.47 qoplam), oqib kirgan rasm markazi yuqori chekkada va o'z
   maydonining faqat 13–43 % i quti ichida. Faqat nisbat (0.15) yetarli emas —
   kontraktorning kengroq stampi company_rep qutisiga 0.18 bilan kirardi.
4. **Placeholder (D)** — `validators.is_blank`: faqat `[\s/\-_.]` dan iborat
   qiymat bo'sh (`'//'`, `'-'`, `'...'`); `'0'`, `'1-2'` bo'sh emas. Sana
   shoxida `Date:` kabi yorliq token'lari tashlanadi, `01//30//2026`
   (placeholder ustiga terilgan sana) → `01/30/2026`.
5. **Regressiya testlari** — `tests/conftest.py`: imzo chizig'idan 12pt
   yuqorida proza qatori + `__/__/____` sana placeholder. ESKI kod bilan:
   3 test yiqildi (`test_unsigned_signature_region...` `True is False`;
   `test_uploaded_contract_is_parsed_and_compared` `can_sign True`;
   `test_a_reviewer_cannot_approve_a_contract_the_engine_blocked` 200≠409 —
   imzosiz kontrakt approve bo'lib ketdi, ya'ni shikoyatning uchdan-uchgacha
   reproduksiyasi). Yangi `tests/test_template_baseline.py` (9 test): 12pt
   proza → False; terilgan ism → True; shablon rasmi = kontrakt rasmi → False,
   boshqa rasm → True; shablonda oldindan bosilgan sana → blank, boshqa sana →
   qiymat; placeholder'lar blank; API: baseline saqlanadi; legacy sxema
   kontrakt task'ida baseline oladi va `can_sign=False` qoladi.

**pytest:** 108 passed (eski 99 + 9 yangi), 0 failed.

**Haqiqiy DB'da qayta parse (faqat o'qish, PII'siz):**
- `c190c353` (shablon `befea049` bilan bir xil fayl): imzo 36× True→False,
  endi 0/40 True; 14 meros sana filled→blank; `can_sign` False→False,
  `missing_required` 48→98.
- `6a5927d3` (shablon `02fc4235`, kontraktor o'z stampini qo'ygan): 22/33
  kontraktor sloti True qoladi (boshqa rasm), 6 company_rep sloti (faqat oqib
  kirgan rasm) True→False; `missing_required` 42→49.
- `6fc89a8d` / `4120ac6f` (toza 1-sahifali shablon): o'zgarish yo'q
  (terilgan ism True, bo'sh slot False).

**MUHIM:** mavjud kontraktlarning `extracted_data` / `comparison_result` i
ESKI aniqlash bilan hisoblangan va avto-migratsiya QILINMADI.
`POST /api/contracts/{id}/compare` faqat qoidalarni qayta hisoblaydi, PDF'ni
QAYTA PARSE QILMAYDI (`api/contracts.py` `rerun_comparison`) — yangi
aniqlash faqat yangi yuklangan kontraktlarga (yoki qayta yuklashga) tegishli.
Shablonlar `befea049`/`02fc4235` imzolangan hujjatlar — toza bo'sh forma
yuklash tavsiya etiladi (audit endi `prefilled_*` bilan ogohlantiradi).

Tegilmagan (keyingi bosqich uchun): `comparison_engine.py`, `api/`,
`schemas/`, frontend. `field_detector.py` o'zgartirilmadi (eslatma:
`__/__/____` placeholder'da `_{2,}` birinchi `__` ni oladi — sana bbox tor;
baseline/parser bunga bog'liq emas, lekin detector uchun keyingi ish).

---

## 2026-09-10 — Per-slot imzo + sana, `signature_slots` bucket'i, finalize per-slot tekshiruvi (appsec + frontend + E2E)

Yuqoridagi "Bo'sh imzo/sana sloti ... tuzatildi" yozuvining (shu sana) davomi —
u aniqlash (parser) qatlamini tuzatgan edi; bu yozuv o'sha aniqlashni review va
imzolash oqimiga ulaydi. Aniqlash tafsilotlari TAKRORLANMAYDI — o'sha bo'limga
qarang.

### Foydalanuvchi shikoyati (uch qism)

1. **Imzo saqlash/tanlash bo'limi** — qaysi sana va qaysi imzoni qayerga qo'yishni
   tanlab bo'lmaydi.
2. **Tekshiruvda bo'sh imzo/sana joylari o'tkazib yuborilyapti** — ildiz sabab
   yuqoridagi aniqlash bo'limida (shablon imzosi meros bo'lib o'tishi + matn/rasm
   qo'shni qutiga oqib kirishi, A–D). Bu yozuv o'sha tuzatishning natijasini UI'da
   ko'rsatadi va finalize'da majburlaydi.
3. **Review'da imzo/sana muammolari ko'rsatilmayapti** — engine bo'sh slotni
   alohida bucket sifatida qaytarmasdi, sidebar'da esa bunday toifa yo'q edi.

### Tasdiqlangan qarorlar (foydalanuvchi tanladi)

- **Model:** BITTA shablon (`field_schema`) + BITTA kontrakt. Ikki-hujjat
  (alohida "imzo shabloni") modeli YO'Q.
- **Har imzo sloti — alohida imzo + alohida (juft) sana.** Global "Contract date"
  = "Apply to all date fields" qulayligi, majburiy emas.
- **Bo'sh imzo/sana = alohida `signature_slots` bucket'i** (`severity:"info"`).
  `missing_required` ga KIRMAYDI, `can_sign` ga TA'SIR QILMAYDI (approve
  bloklanmaydi). Majburlash nuqtasi — **finalize**: har majburiy slot tekshiriladi,
  bo'sh bo'lsa **409 + `details`**.
- **Sana kanali:** finalize `dates:[{field_id,value}]`, `apply_paired_dates:false`.
  **DB migratsiya YO'Q.**

### Backend (pytest 108 → **132 passed**, 0 failed)

- **`app/services/comparison_engine.py`** — :22 `SEVERITY_INFO`, :28
  `SLOT_FIELD_TYPES = ("signature", "date")`; :39
  `ComparisonResult.signature_slots`; :171-191 `compare()` halqasi:
  `_is_filled` → `elif _is_slot_field` → `elif required` (missing bloki :180-191
  AYNAN saqlangan, faqat slot tarmog'i oldiga qo'shildi); :222-223 `_is_filled`
  `@staticmethod` ga; :376-431 yordamchilar — `_paired_date_ids`,
  `_is_slot_field` (:389), `_slot_entry`, `pending_signature_slots` (:409) —
  review VA finalize uchun BITTA haqiqat manbai.
  **`can_sign` ifodasi (:201-205) MATNAN O'ZGARMAGAN** — isbot:
  `test_data_gate_is_unchanged_when_slots_are_empty` (critical missing / critical
  inconsistency / critical FMCSA → har birida `can_sign False`) + HTTP
  `test_a_reviewer_cannot_approve_a_contract_the_engine_blocked` (`p1_cdl`
  o'chirilganda approve → 409).
- **`app/schemas/comparison.py`** — :9 `Severity` += `"info"`; :38-52
  `SignatureSlot`; :62 `ComparisonOut.signature_slots` (bu bo'lmasa
  `response_model` kalitni jimgina tashlab yuborar edi — frontend hech qachon
  ko'rmasdi).
- **`app/schemas/contract.py`** — :70-92 `SignaturePlacementIn`: `page`/`bbox`
  ixtiyoriy, `field_id` yo'q bo'lsa majburiy (aks holda 422); :128-136
  `DatePlacementIn`: `field_id` majburiy, `bbox` olib tashlandi.
- **`app/api/signatures.py`** — :68-109 `place_signature`: `field_id` berilsa
  koordinata SHABLONDAN olinadi (mijoz `page`/`bbox` e'tiborsiz — imzoni boshqa
  bandga ko'chirib bo'lmaydi), noto'g'ri/matn maydoni → 422, band slot → 409;
  :195-243 `finalize_contract`: "No signatures" tekshiruvi (:215-216) saqlangan +
  per-slot tekshiruv (:224-243) → 409 `details.missing_signature_slots` /
  `details.missing_date_fields`; :302-339 `_resolve_dates`: explicit sanalar
  faqat shablon `field_id` orqali (aks holda 422), `apply_paired_dates` fallback
  saqlangan.
- **`app/workers/tasks.py:158`** — audit `details` ga `signature_slots` soni.
- **Testlar:** `test_comparison_engine.py` (`SCHEMA_WITH_SLOTS` + 6 yangi);
  `test_api_signing.py` (3 tuzatildi, ~12 yangi — shu jumladan XAVFSIZLIK:
  begona `field_id` → 422, mijoz bbox e'tiborsiz, bir slotga ikki imzo → 409);
  `test_api_flow.py:70-76` yangi semantikaga; `test_template_baseline.py:210-217`
  yangi semantikaga moslandi.

### Frontend (`npm run typecheck` EXIT 0, `npm run build` OK)

- `src/types/comparison.ts` — :3 `'info'`, :47-62 `SignatureSlot`, :69
  `signature_slots?`, :75 `'signature_slot'` issue turi.
- `src/hooks/useComparison.ts` :118-131 — slotlarni flatten (polling / 409
  mantiqi TEGILMADI).
- `src/components/ComparisonSidebar.tsx` :28, :31, :138-139 — "Signature / date
  required" toifasi.
- `src/components/ComplianceBadge.tsx` :17-27 — "Ready to sign · N slot(s) to sign".
- `src/styles/index.css` — `.highlight--info` (dashed), `.slot--date`,
  `.slot-card*`, `.issue--info`.
- `src/api/signatures.ts` :10-27 — `DatePlacementInput` (`field_id` majburiy,
  bbox yo'q) + `FinalizeConflictDetails`.
- `src/pages/SignaturePage.tsx` — per-slot kartalar (Signed ✓ / Not signed,
  [Sign]/[Remove], juft sana uchun DatePicker), "Other dates", "Free placements"
  (+ "Does not satisfy a signature slot" izohi), "Contract date" + [Apply to all
  date fields], finalize 409 ro'yxati "Show" bilan; Finalize tugmasi barcha
  majburiy slot to'lmaguncha o'chiq.
- `src/components/DatePicker.tsx` :18-19 — bo'sh preview.

### Uchdan-uchga tekshiruv — PASS (sintetik shablon/kontrakt, PII'siz)

Backend qayta ishga tushirildi: eski PID 25404/34516 (2026-09-07 dan beri eski
kod — `local_run.py` `--reload`siz) → yangi launcher 40848 / uvicorn 37512,
`/health` 200. Jonli zanjir:

1. Shablon ready → kontrakt analyzed → `GET /compare`: `can_sign:true`,
   `missing_required:[]`, `signature_slots` = 2.
2. Review UI: "Signature / date required 2", dashed highlight, Approve YOQILGAN →
   approve 200.
3. Sign sahifasi per-slot; Finalize o'chiq. Free imzo (`field_id`siz) + finalize
   → **409** (`p1_contractor_signature`, `p1_date`).
4. `field_id` + mijoz bbox `0/0/1/1` → 201, saqlangan bbox = shablon
   `200/549.277/152.9/34` (mijoz koordinatasi e'tiborsiz — tasdiqlandi).
   Ikkinchi imzo o'sha slotga → 409. `p1_full_name` (matn maydoni) → 422.
5. Brauzerda chizib + [Apply to all] → finalize **200 completed**.
6. Yakuniy PDF: imzo rasmi (200, 549.3)–(302, 583.3) va `09/10/2026` (x=442,
   top=580.1) — ikkalasi shablon bbox ichida; hash `intact:true`. Audit zanjiri
   to'liq.

Artefaktlar: `%TEMP%\swm_e2e\` (`ev_*.json`, `screens\*.png`).

### Semantik o'zgarish (MUHIM)

Imzosiz, lekin ma'lumoti to'liq kontrakt endi **`can_sign=True`** — bu ATAYLAB:
imzo review darvozasida emas, **finalize'da** talab qilinadi. Shu jurnaldagi
avvalgi ta'rif — 2026-09-07 "Ish B" bo'limi, "Xavfsizlik invarianti" bandi
(JURNAL :195-198: "har yangi imzo/sana maydoni `required` → `can_sign` faqat
QATTIQROQ") va XATOLIK 3 dagi shunga o'xshash jumla (:139-142) — endi shunday
o'qiladi: **"har yangi imzo/sana maydoni → qo'shimcha majburiy finalize sloti"**.
Ma'lumot xatoliklari (bo'sh majburiy matn maydoni, critical inconsistency, FMCSA)
uchun darvoza o'zgarmagan — yuqoridagi ikki test bilan isbotlangan.

### Cheklovlar / eslatmalar

- `POST /compare` PDF'ni qayta parse QILMAYDI — yangi aniqlash faqat yangi yoki
  qayta yuklangan kontraktlarga (aniqlash bo'limida ham aytilgan).
- Free placement (`field_id`siz) imzo hali ham qabul qilinadi va yakuniy PDF'ga
  chiqadi, lekin slotni qanoatlantirmaydi — dizayn qarori (UI'da izoh bor).
- `bbox:null` imzo sloti (nazariy; detector har doim bbox beradi) — UI'da [Sign]
  o'chiq, "no position on page"; backend `field_id` bilan bbox'siz qabul qiladi.
- E2E qoldiqlari DB'da (DELETE endpoint yo'q; sintetik, PII yo'q): kontrakt
  `4bfcc69d-ef0c-41e0-bb5e-f2b2c0d2d262` (completed); foydalanuvchilar
  `e2e-signer-1789046890@example.com` (`16fbfaa8-...`),
  `e2e-signer-1789046834@example.com` (`98fc7eb6-...`); shablon `35643ece-...`
  archived (DELETE 200).

### Ochiq topilmalar (bu ishda O'ZGARTIRILMADI)

1. **Avtorizatsiya, avvaldan mavjud — TUZATILDI (appsec, 2026-09-10)** — `DELETE
   /api/contracts/{id}/signatures/{sig_id}`: yagona guard
   `assert_can_view_contract` edi, ya'ni kontraktni ko'ra oladigan HAR KIM
   istalgan imzoni (jumladan kompaniya vakilinikini) o'chira olardi. Foydalanuvchi
   tasdiqlagan siyosat: faqat imzoni QO'YGAN foydalanuvchi yoki admin.
   `Signature` modelida imzo qo'yuvchi ustuni YO'Q edi (`signer_email` — mijoz
   bergan matn, ishonchsiz) → **migratsiya bor:** `models/signature.py:38-43`
   `placed_by_user_id` UUID NULL, FK `users.id` `ondelete=SET NULL`
   (`contract.py:53` uslubi), index; `alembic/versions/0003_signature_placed_by.py`
   (additive: `add_column` + FK + index; DESTRUKTIV yo'q; `0002` uslubida).
   `api/signatures.py:140` `place_signature` endi `placed_by_user_id=user.id`
   yozadi; `:67-79` yangi `_assert_can_remove_signature` — admin → o'tadi;
   `placed_by_user_id == user.id` → o'tadi; aks holda `PermissionError_` (403,
   `exceptions.py:29`); `:195` `remove_signature` ichida `assert_can_view_contract`
   va finalize-409 tekshiruvidan KEYIN chaqiriladi (ko'rish sharti va
   yakunlangan-kontrakt taqiqi avvalgidek). **Legacy (NULL) qatorlar → faqat
   admin** (xavfsiz default; jonli DB'da 2 ta shunday qator, ikkalasi yakunlangan
   `4bfcc69d-…` kontraktda). Audit `signature_removed` allaqachon `actor=user`
   bilan yozilardi — tegilmadi. Rad etilgan (403) urinish auditga TUSHMAYDI —
   kichik kuzatuv, xavfsizlikka ta'siri yo'q.
   Testlar: `tests/test_api_signing.py:566-696` — 5 yangi (qo'yuvchi yozilishi;
   qo'yuvchi o'zinikini o'chiradi → 200; reviewer (kontraktni ko'radi, ro'yxatni
   oladi) → **403**, qator va rasm DB/storage'da qoladi; admin → 200; legacy NULL
   qator: contractor → 403, admin → 200); `conftest.py` tegilmadi. Mavjud testlar
   (`test_a_slot_takes_only_one_signature` o'chirish, `test_api_user_signatures`
   IDOR) buzilmadi. **pytest: 141 passed** (136 + 5), 0 failed.
   **Jonli DB:** `local_run.py:36` `create_all` mavjud jadvalga ustun QO'SHMAYDI va
   alembic bu SQLite'da ishlamaydi (`0001` Postgres-only, `alembic_version` yo'q)
   → restartdan OLDIN migratsiya bilan aynan bir xil additive `ALTER TABLE
   signatures ADD COLUMN placed_by_user_id CHAR(32) REFERENCES users(id) ON
   DELETE SET NULL` + index qo'lda; zaxira `backend/local.db.bak-20260910-placed_by`
   (o'chirish mumkin). Postgres boot yo'li (`entrypoint.sh` → `alembic upgrade
   head`) `0003` ni oladi. Jonli tasdiq (seed admin `.env`dan login, parol
   chop etilmagan; yangi sintetik contractor `appsec-proof-1789052354@example.com`;
   dalil `%TEMP%\swm_e2e\ev_6x_*.json`): admin DELETE yakunlangan `4bfcc69d-…`
   legacy imzosi → **409** (finalize guard birinchi, o'chirilmadi); sintetik
   kontrakt `16ff5706-…`: admin `company_rep` imzo qo'ydi → contractor (kontrakt
   egasi) DELETE → **403**, ro'yxatda qoldi → contractor o'z imzosini → **200**
   → admin o'zinikini → **200**; audit: `signature_added`/`signature_removed`
   to'g'ri actor bilan. Qoldiq: shablon `7032b09a-…` archived (DELETE 200),
   kontrakt `16ff5706-…` (imzosiz, DELETE endpoint yo'q) va proof-foydalanuvchi
   sintetik. Backend restart: cmd 1856 → launcher 27096 → uvicorn 34004
   to'xtatildi → **cmd 27076 → launcher 21724 → uvicorn 39012**, `/health` 200;
   frontend :5173 tegilmadi.
2. **Bug, avvaldan mavjud, E2E'da topildi — TUZATILDI (appsec, 2026-09-10)** —
   `POST /api/auth/users/{user_id}/role` → 500 (`set_role`, `str` qiymat UUID
   ustuniga). Tuzatish `api/auth.py` (`:1` `from uuid import UUID`; `:80-95`
   `set_role`): `:82` `user_id: str` → `user_id: UUID` (asosiy tuzatish,
   `contracts.py:131` uslubi); `:85` qo'lda admin tekshiruvi →
   `Depends(require_role(ROLE_ADMIN))` (`templates.py:38` uslubi); `:90-91`
   topilmasa `AuthError` (401) → `NotFoundError` (404, `deps.py:70` uslubi).
   **Xatti-harakat o'zgarishi (ataylab):** admin bo'lmagan endi **403** — eski kod
   noto'g'ri **401** qaytarar edi (`AuthError`, `exceptions.py:24-26`); boshqa
   barcha admin-gate'lar `require_role` orqali 403 beradi, endi bu ham izchil.
   Testlar: `tests/test_api_auth.py:56-116` — 4 yangi (admin rol o'zgartiradi →
   200 + DB `role=="reviewer"`; noto'g'ri UUID → 422; mavjud bo'lmagan → 404;
   admin bo'lmagan → 403, rol o'zgarmagan); `conftest.py` tegilmadi.
   **pytest: 136 passed** (132 + 4), 0 failed. Jonli tasdiq (yangi backend, seed
   admin token, sintetik `16fbfaa8-…` foydalanuvchida): `role=reviewer` → 200;
   `not-a-uuid` → 422; `00000000-0000-4000-8000-000000000000` → 404;
   `role=contractor` (qaytarish) → 200. Haqiqiy foydalanuvchilar / seed admin
   paroli tegilmadi. Backend restart: 40848/37512 → cmd 1856 → launcher 27096 →
   uvicorn 34004, `/health` 200; frontend tegilmadi.
3. **Qoldiq, doiradan tashqari (tegilmadi)** — `api/auth.py:87-88`: noto'g'ri rol
   NOMI (`role not in ROLE_RANK`) hali `AuthError` (401) beradi; to'g'riroq javob
   422 (validatsiya xatosi). Kichik, xavfsizlikka ta'siri yo'q (admin-gate :85 da
   undan oldin ishlaydi).

**Kod holati:** commit/push/deploy QILINMADI (avvalgidek — signwithme git-repo
emas). Tegilmagan INVARIANT fayllar: `field_detector.py`, `pdf_parser.py`
(bugungi aniqlash bo'limidan keyin qo'shimcha o'zgarish yo'q), `pdf_builder.py`,
`api/user_signatures.py`, alembic versiyalari.

---

## 2026-09-10 — Imzo kutubxonasi sahifasi + rasm yuklash (qattiqlashtirilgan), detektor "to'ldirilgan shablon" chidamliligi, reanalyze/reparse, haqiqiy kontrakt bloki tahlili (appsec + frontend + E2E)

Shu sanadagi ikki yozuvning (aniqlash tuzatishi → per-slot imzo/finalize) davomi.
Ular sintetik va shablon-meros holatlarini yopgan edi; bu yozuv HAQIQIY
Docusign kontraktidagi qolgan blokni ochadi, detektorni to'ldirilgan shablonga
chidamli qiladi, qayta-tahlil endpointlarini beradi va foydalanuvchi so'ragan
imzo kutubxonasi + rasm yuklashni (backend validatsiyasi bilan) qo'shadi.

### Foydalanuvchi so'rovi (skrinshot bilan, uch qism)

1. Haqiqiy Docusign kontrakti review'da "Approval is blocked while critical
   issues remain", Approve o'chiq — "buni ham yechish kerak".
2. Imzo shablonlarini saqlash/foydalanish bo'limi (alohida sahifa).
3. Imzoni rasm sifatida yuklash.

### A. Haqiqiy kontrakt bloki — tahlil (appsec, faqat o'qish) va yechim

- Kontrakt `128b724c-8678-431d-ad78-78bb6d1d0efb` (31 bet, Docusign envelope
  `2D8D446D…`), shablon `02fc4235` "CD Contract.pdf" (to'ldirilgan/imzolangan
  hujjat, baseline `prefilled`), bugun 15:22 da yangi parser bilan yuklangan
  (`signature_slots` 46). Bloklovchi: `missing=3, inconsistencies=0, fmcsa=0`:
  `p1_state` (SOXTA), `p2_state` (SOXTA), `p3_vehicle_assignment` (HAQIQIY —
  ikkala katak `☐`, PyMuPDF bilan tasdiqlangan).
- **SOXTA mexanizm (CONFIRMED, belgi darajasida):** shablonning o'z qiymati
  ("Utah", 9pt) 8 ta yopishgan `_` glif ustida (x=237.7–285.7) turadi va
  pdfplumber so'z-guruhlashini ikkiga bo'ladi → `_fill_region` dagi `_{2,}`
  yugurigi faqat x=261.7 dan boshlanadi → bbox tor va o'ngda → kontraktdagi
  qiymat (x=241.6–255) bbox bilan kesishmaydi → "missing". `p3_phone`
  warning'i ham aynan shu mexanizm. Kontraktni qayta yuklash hal qilmaydi
  (3 rejimda ham `missing=3`). Bugungi imzo tuzatishi 40→3 qilgan edi (37
  soxta ketgan); qolgan 2 soxtasi detektorda edi.
- Eslatma (qamrov bo'shlig'i, OCHIQ): 4 FMCSA qoidasidan 3 tasining
  `field_id` lari bu shablonda umuman yo'q → qoida jimgina o'tkazib yuboriladi
  (`comparison_engine.py:329-332`, `if not known: continue`). Ya'ni "fmcsa=0"
  ning bir qismi qoida bajarilgani emas, qo'llanmagani.

### B. Detektor tuzatishi (appsec) — `app/services/field_detector.py`

- `:34` `UNDERSCORE_GLYPH_GAP = 1.5`, `:37` `UNDERSCORE_ROW_OVERLAP = 0.5`;
  `:206` (build ichida chaqiruv) va `:374` (`def`) `_underscore_glyphs(page)` —
  sahifadagi barcha `_` gliflar; `:333` `_fill_region` da `_{2,}` topilgach
  FAQAT `text_line` uchun `_extend_over_glyph_run` (`:391`) — qatordagi
  yopishgan `_` gliflar bo'ylab chapga/o'ngga kengaytirish; chegara: yorliq
  `x1` va keyingi yorliq `x`; `y`/`height` o'zgarmaydi; faqat KENGAYADI, hech
  qachon toraymaydi. Imzo/sana/checkbox yo'li, `LabelPattern`, `_unique_id`,
  juftlash — tegilmadi.
- **Regressiya (6 shablon, eski vs yangi `build_schema`):** `field_id`
  to'plami hammasida bir xil; o'zgargan bbox faqat `text_line` va faqat
  kengayish: `befea049` — 9, `02fc4235` — 8 (`p1_state` x=261.7/w=24 →
  237.7/48, `p2_state` 356/24 → 332/48, `p3_phone` 232.8/66 → 184.8/114,
  `p2_full_name` w=48 → 270, p12/p20/p21/p25 `full_name` w → 258), qolgan 4
  shablon — 0. Imzo/sana/checkbox bbox — 0 o'zgarish.
- Testlar `tests/test_field_detector.py:154,174,188` (reportlab sintetik):
  bosilgan qiymat bilan bo'lingan chiziq → butun span (eski kod bilan yiqilishi
  tasdiqlangan); bir qatorda 3 maydon → 3 alohida quti; bo'sh forma →
  bayt-bayt bir xil.
- Qo'shimcha: `comparison_engine.py:249` format tekshiruvlari endi faqat
  `text_line`/`date` uchun (checkbox `"yes"` qiymatiga CDL-format soxta
  warning'i ketardi); `can_sign`/missing/slot mantig'iga tegilmadi; test
  `test_comparison_engine.py:130`.

### C. Reanalyze / reparse endpointlari (appsec)

- `POST /api/templates/{id}/reanalyze` (`templates.py:182`, avvaldan bor,
  `require_role(admin)`): endi `template_reanalyzed` auditi (aktor,
  `previous_field_count` — `:207`); arxivlangan shablon → 409 (`:198-199`).
  Eslatma (mavjud xatti-harakat): qo'lda kiritilgan shablon maydon
  tuzatishlarini o'chiradi.
- **YANGI** `POST /api/contracts/{id}/reparse` (`contracts.py:184`,
  `require_role(reviewer, admin)`): `completed` → 409 (`:199-200`),
  `uploaded`/`parsing` → 409 (`:201-202`), `signing` → 409 (`:203-204`, E2E
  agent qo'shdi — imzo turganda qayta o'qilmaydi), shablon `ready` bo'lmasa →
  409 (`:207`); `contract_reparsed` audit; 202 → `parsing` → `analyzed`.
  SAQLANADI: `PATCH .../fields` tuzatishlari (`template_mapper.py:110-183` —
  `reviewer_corrections`/`apply_corrections`/`restore_correction_state`;
  `workers/tasks.py:138-160` — `verified` holati yoki `field_corrected` audit
  yozuvi orqali aniqlanib parser natijasidan ustun bo'ladi), `review_status`
  va notes, kontrakt imzolari, audit zanjiri.
- Testlar `tests/test_api_reprocess.py` (10): reanalyze 202 + sxema + audit,
  403, 404; reparse 202 → analyzed, tuzatish + review saqlangan, qayta tahlil
  qilingan shablon sxemasini oladi, completed/parsing/signing → 409, 403, 404.
- Bugungi ikki yozuvdagi "`POST /compare` PDF'ni qayta parse qilmaydi"
  cheklovi shu bilan yopildi.

### D. Haqiqiy ma'lumotga qo'llash

Zaxira `backend/local.db.bak-20260910-detector` (integrity ok); task
funksiyalari inline chaqirildi, restart'siz.

- Shablon `02fc4235`: `field_count` 118 → 120 (DB sxemasi eskirgan edi —
  joriy kod eski `_fill_region` bilan ham 120 beradi: +`p22_initials`,
  `p22_initials_2` majburiy imzo slotlari; 13 anchor bbox yangilandi).
- Kontrakt `128b724c`: missing 3 → **faqat `p3_vehicle_assignment`**; warnings
  2 → 0; `can_sign` False → False (haqiqiy bo'sh katak — TO'G'RI blok);
  slots 46 → 47; `review_status` pending saqlangan; audit
  `template_reanalyzed` / `contract_reparsed`.
- Boshqa 5 shablon va 9 kontrakt bayt-bayt o'zgarmagan.
- Shu shablondagi 4 kontrakt (`6a5927d3`, `81677830`, `3827021f`, `a736d702`)
  hali eski natijada (missing 42/39/41/40, slots 0) — reparse QILINMADI
  (reviewer/admin `POST .../reparse` bilan qila oladi).

### E. "My signatures" sahifasi + rasm yuklash (frontend; `tsc` EXIT 0, `build` OK)

- Yangi `src/hooks/useSignatureUpload.ts`: MIME + kengaytma faqat PNG/JPEG
  (`:12`), SVG rad, `MAX_UPLOAD_BYTES` 2 MB (`:4`, `:36`),
  `createObjectURL` → canvas (asl fayl baytlari serverga BORMAYDI),
  `MAX_EDGE` 1600px (`:18`), oq fon → shaffof `WHITE_THRESHOLD` 235 (`:22`),
  chiqish doim `image/png` data-URL (`:168`).
- Yangi `src/components/SignatureUploadPad.tsx`; yangi `src/pages/MySignatures.tsx`
  (`/signatures`: kartalar, Draw new / Upload image, nom, Delete tasdiq bilan).
- `src/App.tsx:8,:38` marshrut; `src/components/Layout.tsx:19` nav
  "My signatures"; `src/api/userSignatures.ts:17-36` `describeSaveError`
  (413/422/…); `src/pages/SignaturePage.tsx:53` `padMode 'upload'`, `:670`
  "Upload an image instead", `:677-705` upload modal + "Save to my signatures"
  (`:692`); `src/styles/index.css:216-234` `.upload-preview*`, `.sig-*`.
- Brauzer (E2E): chizib saqlash 201, PNG 201, JPEG + shaffoflik 201, SVG /
  5 MB mijozda rad, DELETE 200; imzo modalida upload →
  `POST /api/contracts/{id}/signatures` 201 (`field_id` bilan) + "Signed ✓".

### F. Backend rasm validatsiyasi (appsec) — `app/api/deps.py:39-160`

`decode_signature_image` (`:71`) + `_normalise_signature_image` (`:113`);
`core/exceptions.py:39-41` `PayloadTooLargeError` (413).

**Avval:** faqat b64 dekod + 8/3 bayt magic + 5 MB chegara dekoddan KEYIN (422).
**Topilmalar (CONFIRMED):**

| # | Topilma | Dalil |
|---|---|---|
| 1 | Decompression bomb — 32 KB 10000×10000 PNG saqlanib, finalize'da `pdf_builder.py:149` `ImageReader` ~400 MB → xotira DoS | reproduksiya, `ImageReader` dekod |
| 2 | Polyglot (PNG + html/script quyrug'i) aynan saqlanib `/api/signatures/{id}/image` orqali qaytardi | reproduksiya |
| 3 | 5 MB chegara dekoddan oldin yo'q — b64 matn chegarasiz | `deps.py` eski kod |

**Endi:** `data:` prefiksi faqat png/jpeg (422) → b64 uzunlik VA bayt ≤ 2 MB
(413, `:98`, `:106`) → magic PNG/JPEG (422) → Pillow lazy open: format,
`MAX_SIGNATURE_DIMENSION` 4000 (`:39`), `MAX_SIGNATURE_PIXELS` 20 MP (`:40`,
`:132`), jarayon bo'yicha `Image.MAX_IMAGE_PIXELS` (`:48`; reportlab
`ImageReader` ham qamraladi) → bomba 422 → `verify()` + reopen (`:143-145`) →
buzilgan/kesilgan 422 → HAR DOIM RGBA PNG'ga qayta kodlash (`:158-160`; hash
shu baytlardan). Ikkala endpoint (`user_signatures.py:57`, `signatures.py:126`)
bitta funksiya; so'rov shakli o'zgarmagan; Pillow 11.0.0 allaqachon
`requirements.txt:16`. Mavjud DB imzolari o'zgarmagan.
Testlar: `test_api_user_signatures.py` +13 (jami 22: round-trip re-encode,
JPEG → PNG, data-URL, 6× rad-format, buzilgan/kesilgan, >2 MB → 413, bomba,
4500×4000, polyglot tozalanadi), `test_api_signing.py` +1.
OCHIQ: umumiy JSON body chegarasi yo'q — reverse-proxy `client_max_body_size`
(devops).

### G. Yakuniy tekshiruv (E2E agent)

- pytest **171 passed** (141 → 157 → 170 → 171), 0 failed. Muhit: bir marta
  `MemoryError` (xotira bosimi), qayta ishga tushirilganda o'tdi.
- Backend restart: 37212/5340/17668 → **cmd 28848 → launcher 18376 → uvicorn
  39180**, `/health` 200; frontend 31832 tegilmadi.
- Jonli: `GET /compare` `128b724c` — missing faqat `p3_vehicle_assignment`,
  inconsistencies/fmcsa/warnings 0, slots 47, `field_count` 120; review UI
  "Missing required 1" (Vehicle Assignment · Page 3), "Signature / date
  required 47", Approve o'chiq (TO'G'RI — haqiqiy bo'sh katak).
- Sintetik: reanalyze 202 → ready; reparse 202 → analyzed (slots 2);
  contractor → 403; `signing` holatida → 409 (jonli); upload-imzo 201 +
  "Signed ✓"; tozalash: imzolar DELETE 200, shablon archived.
- Qoldiq (DELETE endpoint yo'q; sintetik, PII yo'q): kontrakt `1638db0b-…`
  (signing), foydalanuvchilar `e2e-sig-1789055410@…`,
  `e2e-rp-20260910211749@…`.
- Artefaktlar: `%TEMP%\swm_e2e\`, `%TEMP%\swm_sig\`,
  `c:\PTG\.playwright-mcp\swm_e2e\` (skrinshotlar).

### Ochiq / keyingi bosqich

1. FMCSA qoidalari `field_id` qamrovi — 4 qoidadan 3 tasi `02fc4235` da
   qo'llanmaydi (A bandi).
2. `reanalyze` qo'lda shablon tuzatishlarini o'chiradi (mavjud xatti-harakat).
3. Umumiy JSON body chegarasi — proxy darajasida (devops).
4. `02fc4235` dagi 4 eski kontrakt reparse qilinmagan (D bandi).

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `pdf_parser.py`, `validators.py`, `conftest.py`,
`models/user_signature.py`; `can_sign` ifodasi
(`comparison_engine.py:201-205`) o'zgarmagan. Zaxiralar:
`backend/local.db.bak-20260910-detector` (bugungi), `…-placed_by` (avvalgi
yozuv) — ikkalasi o'chirilishi mumkin.

---

## 2026-09-10 — Reviewer "Approve anyway" override (faqat bo'sh majburiy maydon), 4 eski nusxani reparse, blok siyosati qarori (appsec + frontend + devops)

Shu sanadagi uchinchi yozuvning (detektor tuzatishi → `128b724c` da faqat
`p3_vehicle_assignment` qoldi) davomi. Foydalanuvchi "muammo yechilmadimi"
dedi — qolgan blok HAQIQIY bo'sh katak, ya'ni texnik xato emas, siyosat
masalasi. Ikki qaror so'raldi va tanlandi:

1. **Blok siyosati:** reviewer izoh bilan o'tkaza olsin — FAQAT "bo'sh
   majburiy maydon" (`missing_required`) muammolari uchun. Kritik FMCSA
   yoki nomuvofiqlik (inconsistency) bo'lsa override MUMKIN EMAS. Qaror
   auditda, kontrakt yozuvida va yakuniy PDF'da qayd etiladi.
2. **Eski nusxalar:** shu shablondagi 4 eski kontrakt zaxira bilan reparse.

`comparison_engine.py` `can_sign` ifodasi (`:201-205`) O'ZGARMAGAN —
override review va signing DARVOZALARIDA amalga oshirilgan, engine
natijasi avvalgidek hisoblanadi.

### A. 4 eski kontrakt reparse (devops, faqat API)

Zaxira `backend/local.db.bak-20260910-reparse4` (integrity ok). Shablon
`02fc4235`, hammasi `analyzed`; `POST /api/contracts/{id}/reparse` 4/4 →
202 → ~2 s ichida `analyzed`; `review_status` pending saqlangan:

| kontrakt | missing oldin→keyin | qolgan | slots oldin→keyin |
|---|---|---|---|
| `6a5927d3` | 42→1 | `p3_vehicle_assignment` | 0→47 |
| `81677830` | 39→1 | `p3_vehicle_assignment` | 0→76 (boshqa, kattaroq hujjat — 33% to'ldirilgan; bir xil envelope emas) |
| `3827021f` | 41→1 | `p3_vehicle_assignment` | 0→47 |
| `a736d702` | 40→1 | `p3_vehicle_assignment` | 0→46 |

Har birida inconsistencies/fmcsa/warnings 0 (avval warnings 3). Boshqa
kontraktlarga tegilmadi. Oldingi yozuvdagi "Ochiq / keyingi bosqich" 4-band
shu bilan yopildi.

### B. Backend override (appsec) — pytest 171 → **191 passed** (20 yangi)

- YANGI `app/services/review_override.py`: `:18`
  `BLOCKING_CATEGORIES = ("fmcsa_violations", "inconsistencies")`; `:25`
  `blocking_categories()` (kritik yozuvi bor kategoriyalar); `:31`
  `critical_missing_field_ids()`; `:41` `override_permits_signing(contract)` —
  override yozilgan VA JORIY comparison'da bloklovchi kritik kategoriya yo'q
  bo'lsagina `True`.
- `app/api/review.py`: `:21` `OVERRIDE_REASON_MIN_CHARS = 10`; `:60-64` flag
  yo'q → 409 (avvalgidek); `:67-71` izoh trim < 10 → 422 "Override requires
  a reason (at least 10 characters)"; `:72-78` kritik fmcsa/inconsistency →
  409 `details={"reason":"override_not_allowed","blocking_categories":[...]}`;
  `:88-90` persist `review_override` / `_reason` / `_fields` — HAR submit
  (oddiy approve yoki reject) ularni qayta yozadi, ya'ni eski override keyingi
  qarorga o'tib qolmaydi (xavfsiz tomonga); `:92-100` audit `review_override`
  (actor, `field_ids`, `reason`); `:110` `review_submitted` details'ga
  `override: bool`. Contractor → 403 (mavjud `require_role`).
  `can_sign=true` + flag → override YOZILMAYDI.
- `app/api/signatures.py`: `:63` `_assert_signable` —
  `not can_sign and not override_permits_signing(contract)` → 409;
  `place_signature` (`:103`) va `finalize_contract` (`:228`) ikkalasi shu
  guard'dan o'tadi; `:284` finalize → `_override_metadata(contract, index)`;
  `:390-409` `OVERRIDE_KEYWORD = "reviewer-override"`, reason ≤ 500 belgi,
  `subject = "Reviewer override: <reason> - fields: <label> (<field_id>)"`.
- `app/services/pdf_builder.py` faqat additive: `:67-68` `subject` /
  `keywords` ixtiyoriy parametrlar; `:97-101` `/Subject`, `/Keywords`
  metadata. Audit/sertifikat sahifasi yo'qligi sababli metadata yo'li
  tanlandi; qayd imzolangan baytlar ICHIDA → `final_file_hash` va
  `/verify` uni ham qamrab oladi.
- `app/models/contract.py:62-66` 3 ustun (`review_override` Boolean NOT NULL
  default false, `review_override_reason` Text, `review_override_fields`
  JSON); `app/schemas/contract.py:33-44` `ContractDetailOut` 3 maydon
  (None → false / [] validator bilan), `:87`
  `ReviewSubmission.override_missing: bool = False`;
  `app/services/audit.py:27` `REVIEW_OVERRIDE`;
  `alembic/versions/0004_review_override.py:23-30` additive (`add_column` ×3).
- Lokal SQLite: alembic ishlamaydi (avvalgi yozuvlardagi sabab) → zaxira
  `backend/local.db.bak-20260910-override` (integrity ok) → Python `sqlite3`
  `BEGIN IMMEDIATE` bilan 3 ta `ALTER TABLE contracts ADD COLUMN` (0004 bilan
  bir xil) → `PRAGMA table_info` tasdiq. Backend restart: 28848/18376/39180 →
  **cmd 36688 → launcher 40884 → uvicorn 36524**, `/health` 200 (jurnal
  yozilayotganda qayta tekshirildi: uchala PID tirik, `/health` 200).
  Eslatma: Git Bash `cmd //c start` osilib qoldi → PowerShell `Start-Process`
  ishlatildi, log `%TEMP%\swm_e2e\backend.log`.
- Testlar `tests/test_api_review.py` — 16 funksiya, biri 5-parametrli → 20
  test: override 200 + detail + audit; izoh 422 ×5 (`None`, `""`, bo'shliq,
  "too short", 9 belgi); flag yo'q → 409; FMCSA → 409 `override_not_allowed`;
  inconsistency → 409; ikkalasi birga qaytariladi; warning-darajali
  inconsistency bloklamaydi; contractor → 403; `can_sign=true` + flag →
  yozilmaydi; keyingi oddiy qaror override'ni tozalaydi; imzo 201 + finalize
  200 + PDF metadata + verify intact; override'siz PDF'da qayd yo'q;
  override'dan KEYIN topilgan FMCSA / inconsistency → place/finalize 409;
  XAVFSIZLIK: DB'da `review_override=true` lekin `approved` emas → 409;
  reparse override'ni saqlaydi. `conftest.py` tegilmadi.
- Jonli API (sintetik kontrakt `9adfbe83…`, shablon `34c9c55d…` keyin
  arxivlandi): compare `can_sign=false`, missing `[p1_cdl]` → "short" izoh
  422 → flag'siz 409 → override 200 (`signing`) → detail
  `review_override=true`, fields `[p1_cdl]` → compare O'ZGARMAGAN → imzo 201
  → finalize 200 → PDF `/Subject` / `/Keywords` qaydi, verify intact → audit
  `review_override, review_submitted, signature_added, contract_finalized`.
  Sintetik `completed` kontrakt DB'da qoldi (DELETE endpoint yo'q).

### C. Frontend override (general-purpose; `tsc` EXIT 0, `build` OK)

- `src/types/contract.ts:28-56` — `ContractDetail` + `review_override*`
  (ixtiyoriy) + `ReviewOverride` + `readOverride()` normalizatsiya
  (`false / null / []`).
- `src/api/contracts.ts:6` `OVERRIDE_NOTES_MIN = 10`; `:31-47`
  `describeReviewError()` (422 + override → izoh talabi matni; 409
  `override_not_allowed` → bloklovchi kategoriyalar matni); `:92-103`
  `submitReview(..., {overrideMissing})` — `override_missing: true` faqat
  so'ralganda qo'shiladi (oddiy body o'zgarmagan). Eslatma: `src/api/review.ts`
  YO'Q — review chaqiruvi `contracts.ts` da.
- `src/pages/ReviewPage.tsx:72-137` (mantiq), `:169-272` (UI) — 3 holat:
  `can_sign` → avvalgidek; FAQAT kritik missing → ogohlantirish qutisi
  "Approval is blocked: N required field(s) are empty: <label · Page>", izoh
  majburiy `N/10` hisoblagich, `.button--warning` "Approve anyway (override)"
  (izoh ≥ 10 da yoqiladi), `window.confirm`, muvaffaqiyat → `/sign`; oddiy
  Approve shu holatda YASHIRIN (ikki tugma chalkashligi oldini olish);
  FMCSA/inconsistency → "Override is not available: FMCSA violation /
  cross-field inconsistency must be corrected first."
- YANGI `src/components/OverrideBadge.tsx` ("Approved with override · N
  required field(s) skipped · reason"); `ComplianceBadge.tsx:6-24` `override`
  prop (amber "Approved with override · N issues skipped", `criticalCount`
  hisobi o'zgarmagan); `ComparisonSidebar.tsx:21-22,45,81` prop uzatish;
  `SignaturePage.tsx:4,16,241-246,441-443` — `blocked` override'da o'chadi
  (server hakam, `:246`), badge; `styles/index.css:63-67` `.override*`,
  `:86-87` `.button--warning`.
- Playwright route-mock (haqiqiy kontraktga tegilmadi, `%TEMP%\swm_override\`
  9 skrinshot): A `can_sign`; B1 missing-only qutisi, `0/10`, tugma o'chiq;
  B2 `5/10` → 53 belgi yoqiladi, confirm matni, Cancel so'rov yubormaydi; B3
  422 xabari, body `{decision, notes(trim), override_missing: true}`; B4 409
  xabari; B5 200 → `/sign` badge; B6 review badge; C FMCSA — tugma yo'q, sabab
  qatori; D regressiya — eski javob shakli (override maydonlarisiz) buzilmaydi.
- Doiradan tashqari (qilinmadi): Dashboard / FinalContract sahifalarida
  override belgisi.

### D. Brauzer uchdan-uchga (frontend + backend birga)

**BAJARILDI — PASS** (2026-09-13, jonli brauzer E2E, route-mock'siz).
Skrinshotlar: `c:\PTG\.playwright-mcp\swm_ovr\01…09b`.
(09 va 09b — haqiqiy kontrakt ko'rinishi, PII sabab 2026-09-15 da o'chirildi)

- Sintetik kontrakt `61418c12…`: `can_sign=false`, 1 `missing_required`
  (`p1_cdl` / «CDL» / page 1, critical), 0 FMCSA, 0 inconsistencies.
- Review sahifasi: «Approval is blocked: 1 required field is empty: … CDL ·
  Page 1»; izoh hisoblagichi `0/10` → «short» `5/10` (tugma o'chiq) → ≥10
  belgida `57/10 characters · reason recorded in the audit trail` (tugma
  yoqildi); oddiy «Approve for signing» ko'rinmadi (`ReviewPage.tsx:169-272`
  kutilgani bilan mos).
- `window.confirm` → **Cancel** → tarmoq panelida `/api/review` ga **0 ta**
  so'rov; **Accept** → `POST /api/review/{id}` tanasi `{decision:"approved",
  notes:"…", override_missing:true}` → **200** → `/sign`.
- Sign sahifasi: «Approved with override» + «1 required field skipped ·
  <sabab>», blok ogohlantirishi yo'q. Slotlar to'ldirildi → `POST …/finalize`
  → **200**, `completed`.
- PDF: `Subject: Reviewer override: … - fields: CDL (p1_cdl)`, `Keywords:
  reviewer-override`; `GET /api/contracts/{id}` → `review_override=true`,
  `review_override_fields=["p1_cdl"]`; `/verify` → `intact: true`.
- Review sahifasiga qaytganda sidebar badge «Approved with override · 1 issue
  skipped».
- **Salbiy holat tasdiqlandi:** kritik nomuvofiqlikli kontraktda override
  tugmasi YO'Q, «Override is not available: FMCSA violation / cross-field
  inconsistency must be corrected first.» qatori bor; jonli API ham **409**
  `{"reason":"override_not_allowed","blocking_categories":["inconsistencies"]}`
  va qisqa izohga **422**.
- **Haqiqiy kontrakt `128b724c` faqat ko'rish uchun ochildi** — «Missing
  required 1» (Vehicle Assignment · Page 3), «Signature / date required 47»,
  izoh `0/10`, override tugmasi o'chiq. Hech narsa yozilmadi/bosilmadi; audit
  hamon 2026-09-10 da tugaydi, `review_status=pending`,
  `review_override=false`.
- Ikki kichik nomuvofiqlik (nuqson EMAS): (a) kontrakt `completed` bo'lgach
  review sahifasida «Approved for signing» kartasi ko'rsatilmaydi
  (`ReviewPage.tsx:265` shunday yozilgan), faqat sidebar badge qoladi;
  (b) sidebar matni «1 **issue** skipped» (birlik).

### Ochiq / keyingi bosqich

1. Dashboard / FinalContract sahifalarida override belgisi (C bandi).
2. Yakuniy PDF'da qayd faqat metadata (`/Subject`, `/Keywords`) — ko'rinadigan
   audit/sertifikat sahifasi hali yo'q.
3. Oldingi yozuvdagi 1–3 bandlar (FMCSA `field_id` qamrovi, `reanalyze`
   tuzatishlarni o'chirishi, proxy body chegarasi) hali ochiq.

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
`comparison_engine.py` `can_sign` (`:201-205`) o'zgarmagan; `pdf_parser.py`,
`field_detector.py`, `validators.py`, `deps.py`, `conftest.py` tegilmagan.
Zaxiralar: `backend/local.db.bak-20260910-reparse4`,
`backend/local.db.bak-20260910-override` (bugungi; oldingi `-detector`,
`-placed_by` bilan birga o'chirilishi mumkin). Haqiqiy kontrakt `128b724c`
override QILINMADI — bu reviewer qarori, UI orqali endi mumkin.

---

## 2026-09-12 — Imzo/sana chiziqqa o'tirmasligi tuzatildi, imzo sahifasi 2 rejim + avto-to'ldirish (appsec + frontend)

Foydalanuvchi 4 narsa aytdi: (1) imzo va sana uchun «hammasiga bitta» yoki
«alohida» degan tanlov bo'lsin; (2) imzo rasm holida qo'yilyapti — o'sha joy
o'zgarsin; (3) **imzolar sal ko'tarilib ketmoqda, chiziqning ustiga
tushmayapti**; (4) imzo va sana avtomatik qo'yilsin yoki shundan tanlov
bo'lsin.

Uch qaror so'raldi va tanlandi: (a) **2 rejim** — «Hammasiga bitta» / «Har
birini alohida» (3-rejim «har sahifaga» rad etildi); (b) imzo **rasm** bo'lib
qolaveradi — yozma-matn imzo va fon tozalash rad etildi, faqat joylashuvi
tuzatiladi; (c) **avto-to'ldirish tanlovi bor, sukut bo'yicha YOQIQ**.

### A. Joylashuv tuzatishi (appsec) — o'lchangan

Sintetik shablonda o'lchandi (chiziq baseline top-left 592.000, ko'rinadigan
`_` siyohi 592.825–593.375; imzo PNG 300×100, siyoh alfa-bbox
(30,20)-(270,75)):

| O'lchov | OLDIN | KEYIN | Maqsad |
|---|---|---|---|
| Imzo siyohi pastki qirrasi → chiziq siyohi | **+18.05 pt** | **+1.83 pt** | 0–3 pt |
| Rasm to'rtburchagi (top-left) | (200.0,549.28)-(302.0,583.28) | (200.0,565.50)-(302.0,599.50) | — |
| Aspect (kirish 3.0000) | 3.0000 | 3.0000 | saqlanadi |
| Sana baseline'i chiziqdan | +4.05 pt | +2.83 pt | ~2 pt |
| Detektor bbox pastki qirrasi → chiziq | +9.55 pt | +9.55 pt (ataylab o'zgarmadi) | — |

**Ikki ildiz sabab, qo'shiladi = 18.05 pt (CONFIRMED):**

1. **9.55 pt** — `field_detector.py:335` dagi
   `top = run.y - height if pattern.field_type == "signature" else ...` da
   `run.y` = `_` **glif qutisining TEPASI**, chiziq emas (pdfplumber: `_`
   char box top=583.277, bottom=594.277, size=11, matritsadan
   baseline=592.0) → bbox pastki qirrasi ko'rinadigan siyohdan bir shrift
   tanasi (0.868 em = 9.55 pt) yuqorida.
2. **8.50 pt** — `pdf_builder.py` eski 157–166: `drawImage(..., anchor="sw")`
   **rasm to'rtburchagining** pastini qo'yardi; capture'da siyohdan pastda
   25/100 piksel shaffof hoshiya bor.

Sana uchun alohida sabab: `baseline = y + max((height - 11) / 2, 1)` — matnni
quti O'RTASIGA qo'yardi, chiziqqa emas.

**Tuzatma — faqat `app/services/pdf_builder.py`:**

- `:49-63` konstantalar: `RULE_SEARCH_ABOVE = 6.0`, `RULE_SEARCH_BELOW = 16.0`,
  `RULE_MIN_COVERAGE = 0.5`, `RULE_MAX_THICKNESS = 2.0`,
  `SIGNATURE_INK_GAP = 1.0`, `DATE_BASELINE_GAP = 2.0`,
  `INK_ALPHA_THRESHOLD = 8`, `INK_LUMA_THRESHOLD = 250`.
- `:114-122` `_RuleIndex` bilan ishlash — manba PDF bir marta ochiladi va
  faqat chiziladigan narsa bo'lsa (`if (sigs_by_page or dates_by_page)`);
  sinfning o'zi `:263-291`.
- `:180` va `:187` — `_rule_y(source_page, placement.bbox)` +
  `_seated_on(placement.bbox, rule)` (`:294`): **quti o'lchami tegilmaydi**,
  faqat pastki qirrasi haqiqiy chiziqqa ko'chiriladi.
- `:305-360` `_rule_y` — chiziqni hujjatning O'ZIDAN topadi: ingichka vektor
  chiziq/rect → siyoh tepasi, yoki `_` gliflari → `char["matrix"][5]` dan
  aniq baseline. Faqat bbox kengligining ≥50% ini qoplagan va
  `[y1-6, y1+16]` oynasidagi nomzod qabul qilinadi, eng yaqini olinadi;
  topilmasa **None** → eski xatti-harakat.
- `:363-400` `_ink_metrics` (Pillow) — RGBA alfa bo'yicha siyoh bbox'i, alfa
  yo'q bo'lsa oqqa flatten, bo'sh yoki o'qib bo'lmasa butun rasm (xavfsiz
  fallback).
- `:200-236` `_draw_signature` — `scale = min(w / iw, h / ih)` bilan aspect
  saqlanadi, `draw_y = rule + 1.0 - (pixel_height - ink_bottom) * scale`,
  gorizontal joylashuv avvalgidek chapdan.
- `:238-260` `_draw_date` — chiziq topilsa `baseline = rule + 2.0`, aks holda
  eski formula.

**`field_detector.py` ATAYLAB tegilmadi (muhim qaror):** `pdf_parser.py`
`text_on_rule` ni `bbox.y1 - 8 … bbox.y1 + 12` oynasi bilan o'qiydi
(`pdf_parser.py:378-379`) va `has_ink` `centre_y >= bbox.y + height / 2` ni
ishlatadi (`pdf_parser.py:342`) — ikkalasi ham "bbox.y1 = `_` glif qutisi
tepasi" semantikasiga sozlangan. Bbox'ni 9.55 pt pastga surish **terilgan ism
bilan imzolangan** slotni "imzosiz" deb o'qishga olib kelardi (detektsiya
zaiflashardi) va bazadagi mavjud `field_schema` lar qayta tahlil talab
qilardi. Hozirgi yechim eski shablonlarda ham darhol ishlaydi.

**Testlar** `tests/test_pdf_builder.py` — endi 13 test, shundan **7 tasi
yangi** (`:199-302`); fixture va yordamchilar shu faylda (`:123-197`),
`conftest.py` tegilmadi: siyoh chiziqda (≤3 pt); 70% shaffof hoshiya
ko'tarmaydi; juda keng (10:1) va juda baland (1:5) rasm bbox'ga sig'adi,
aspect saqlanadi; bo'sh capture xavfsiz; sana baseline chiziqda; chiziqsiz
quti o'z qirrasini saqlaydi (free placement regressiya qo'riqchisi).
**pytest 191 → 198 passed.** Vakkum emasligi tekshirildi: eski chizish kodi
qaytarilganda 4 ta yangi test yiqiladi.

**Jonli:** to'liq oqim (login → shablon → kontrakt → approve → imzo →
finalize → download) — ink gap +1.83 pt, sana +2.83 pt, `/verify`
`intact: true`.

### B. Imzo sahifasi: 2 rejim + avto-to'ldirish (frontend; `tsc` EXIT 0, `build` EXIT 0)

- `src/pages/SignaturePage.tsx` qayta yozildi (1164 qator): rejim
  almashtirgichi `:758`, bulk panel `:835`, faqat-o'qish slot ro'yxati va
  per-slot tarmoq bitta shartning ikki tarmog'i (`:861-970`; per-slot markup
  o'zgarmagan), `applyToAll` `:351`, `runBulkApply` `:426`, avto-to'ldirish
  effekti `:471-527` (bir martalik `useRef` guard — `autoFillRan` `:100`),
  `undoAutoFill` `:530`.
- YANGI `src/components/BulkSignPanel.tsx` (193 qator) — preview, sana,
  qamrov qatori, Apply/Replace, jarayon ko'rsatkichi, xato bergan slotlar
  ro'yxati.
- YANGI `src/hooks/useStoredPreference.ts` (62 qator) — `useStoredChoice` /
  `useStoredFlag`, localStorage, private-rejimga chidamli.
- YANGI `src/lib/pool.ts` (25 qator) — `runPool`, ≤3 parallel, hech qachon
  reject qilmaydi.
- `src/styles/index.css:60-62` (`.alert--good`, `.alert__actions`) va
  `:188-195` (`.mode-switch`, `.bulk-*`, `.button--small`); rejim
  almashtirgichi mavjud `.chip` uslubini qayta ishlatadi.
- Saqlanadigan tanlovlar (`SignaturePage.tsx:43-44`, `:88-89`):
  `swm.signMode` (sukut `bulk`), `swm.autoFill` (sukut yoqiq).
- Ataylab og'ish: faqat **sanalar** bo'sh qolganda «Apply to all» imzo
  tanlanmasdan ham yoqiladi (qayta yuklashdan keyin sana qiymatlari faqat
  mijozda bo'lgani uchun); «Replace all» hamon imzo talab qiladi.
- Brauzer tasdig'i (sintetik shablon + 2 sahifali kontrakt, 2 imzo + 2 sana
  sloti; haqiqiy kontraktlar ochilmadi): bo'sh kutubxona → hech narsa
  qo'yilmaydi + maslahat; avto-to'ldirish → 2× POST 201, «4 places were
  filled automatically», StrictMode ikki marta to'ldirmadi; Undo → 2× DELETE
  200; Bulk Apply → 2× POST 201; per-slot regressiya (Remove 200 → Sign →
  201, kartalar / juft DatePicker / «Contract date» o'zgarmagan); finalize
  200 `completed`, hash tasdiqlandi; qayta yuklashda rejim va belgi
  saqlanadi; «Replace all» 2× DELETE + 2× POST. Skrinshotlar
  `c:\PTG\.playwright-mcp\swm_bulk\01…09`.

### C. Ochiq topilmalar (bu ishda o'zgartirilmadi)

1. **Bulk endpoint tavsiya etiladi:** har slot butun base64 rasmni qayta
   yuboradi. Chizilgan imzo ~9.5 KB — muammo emas; lekin
   `app/api/deps.py:35-38` rasmga 2 MiB ruxsat beradi → 11 slot × 2 MiB
   skanerlangan imzo = **~29 MiB yuklash va 11 marta bir xil Pillow
   dekod/qayta-kodlash**. `POST /signatures/bulk`
   (`{image_data, field_ids[]}`) buni bitta yuklash + bitta dekodga
   tushirardi. Hozircha loop ≤3 parallel.
   **Yangilanish (o'sha kuni, ikkinchi qism) — BAJARILDI:**
   `POST /api/contracts/{contract_id}/signatures/bulk` qo'shildi va frontend
   unga ulandi; quyidagi «2026-09-12 (ikkinchi qism)» yozuvining **B** va **D**
   bo'limlariga qarang (jonli o'lchov: 66.7% tejash, dekod 3 → 1).
2. **`app/services/storage.py:132`** — `self.root = Path(root or
   settings.storage_dir)`, ya'ni nisbiy yo'l **jarayon CWD**'siga nisbatan
   hal qilinadi; `local_run.py:25` (`os.chdir(BACKEND_DIR)`) uni qotiradi,
   lekin yalang'och `uvicorn app.main:app` qotirmaydi → sinov paytida bir
   marta jonli `Refusing to access a key outside the storage root:
   signatures/….png` xatosi kuzatildi (`storage.py:138`; darhol qayta
   urinish o'tdi). Alohida tuzatilmoqda.
   **Yangilanish (o'sha kuni, ikkinchi qism) — TUZATILDI, lekin jonli 502 ning
   sababi BOSHQA chiqdi:** nisbiy `storage_dir` endi `backend/` ga bog'landi va
   guard'da kutilmagan joydan haqiqiy prefiks-bypass topildi (**A** bo'limi),
   biroq kuzatilgan xatoning ASOSIY ildiz sababi Windows `\\?\` poyga sharti
   bo'lib chiqdi va uni CWD tuzatishi bartaraf etmagan edi (**C** bo'limi).
3. Kutubxonada «sukut imzo» tanlash boshqaruvi yo'q (`is_default` hamisha
   false) → avto-to'ldirish eng oxirgi yaratilganni oladi. `/signatures`
   sahifasiga sukut-tanlagich qo'shish mumkin.
   **Yangilanish: foydalanuvchi RAD ETDI** — 2026-09-12 da nima qilinsin deb
   so'ralganda faqat bulk endpoint tanlandi, bu band qilinmaydi.
4. UI yorliqlari inglizcha («One for all» / «Each one separately») — ilovada
   i18n qatlami yo'q, qolgan matnlar bilan bir xil.
   **Yangilanish: foydalanuvchi RAD ETDI** — 2026-09-12 da nima qilinsin deb
   so'ralganda faqat bulk endpoint tanlandi, bu band qilinmaydi.

### D. Server holati

:8000 da hech narsa LISTENING emas edi (2 kunlik tanaffusdan keyin eski
jarayonlar yo'q) → qayta ishga tushirildi: launcher PID 35160 → xizmat
ko'rsatuvchi **PID 29116** (`0.0.0.0:8000`), `/health` 200 (jurnal
yozilayotganda qayta tekshirildi: PID 29116 hamon LISTENING). Frontend :5173
(PID 10144) tegilmadi. Baza zaxirasi
`backend/local.db.bak-20260912-sigalign`. Sintetik shablon / kontrakt /
imzolar o'chirildi (audit izi saqlanib, `contract_id` / `template_id` NULL
qilindi); haqiqiy kontraktlar va PII'ga tegilmadi.

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `comparison_engine.py` `can_sign`, `pdf_parser.py`,
`validators.py`, `field_detector.py`, `conftest.py`. Kutilayotgan: 2026-09-10
dagi override oqimining brauzer tekshiruvi hali bajarilmagan (rate-limit
tufayli to'xtagan; API testlari va 191-testli to'plam o'tgan edi) — o'sha
yozuvdagi «D. Brauzer uchdan-uchga» bandi hamon ochiq. Zaxiralar:
`backend/local.db.bak-20260912-sigalign` (bugungi; oldingi `-20260910-*`
to'rttasi o'chirilishi mumkin).

---

## 2026-09-12 (ikkinchi qism) — Storage guard'idagi Windows poyga sharti (13% 502), bulk imzo endpointi, storage traversal qattiqlashtirish (appsec + frontend + devops)

Yuqoridagi yozuvning «C. Ochiq topilmalar» ro'yxatidan ikkitasi olindi: 1-band
(bulk endpoint) va 2-band (`storage.py` CWD). Ikkinchisini tuzatish jarayonida
jonli 502 ning haqiqiy sababi CWD emas, **Windows `\\?\` poyga sharti** ekani
aniqlandi; 3- va 4-bandlar foydalanuvchi tomonidan rad etildi.

### A. Storage: birinchi tuzatish (root CWD + prefiks bypass)

- `app/services/storage.py:34-36` — `BACKEND_DIR =
  Path(__file__).resolve().parents[2]`; `:146-165` `_resolve_root`; `:162-164`
  — nisbiy `storage_dir` endi **jarayon CWD**'siga emas, `backend/` ga
  bog'lanadi (`local_run.py:21` bilan bir xil anchor); absolut qiymat
  o'zgarishsiz ishlatiladi; root konstruktorda (`:211-215`) BIR MARTA hal
  qilinadi va ishlab turgan jarayon ostida siljiy olmaydi. Windows nuqtasi:
  `Path("/data/storage").is_absolute()` → **False** → `.resolve()` uni CWD
  diskiga tushirardi; Linux'da (Docker image) o'sha qiymat absolut va
  tegilmaydi.
- **Guard'da haqiqiy traversal bypass topildi va PoC bilan ko'rsatildi
  (CONFIRMED):** eski `storage.py:137` dagi
  `str(candidate).startswith(str(root))` — bu **prefiks** tekshiruvi, komponent
  tekshiruvi emas. `../storage-evil/pwned.png` kaliti `…\storage` bilan
  boshlanuvchi **qo'shni** katalogga chiqib ketardi va tekshiruvdan o'tardi;
  PoC natijasi: `WROTE OUTSIDE ROOT -> True b'ESCAPED'`. CWE-22
  (Path Traversal). Amalda ekspluatatsiya yo'li yopiq edi — yozish kalitlari
  server tomonda UUID'dan yasaladi — shuning uchun og'irlik **HIGH-potensial**
  (haqiqiy ta'sir emas, sinfiy jiddiylik). Tuzatildi: `is_relative_to`, ya'ni
  komponent bo'yicha solishtirish (`.resolve()` orqali symlink hal qilish o'sha
  bosqichda saqlangan edi).
- `tests/test_storage_local.py` ga +5 test. **pytest 198 → 203 passed.**
- Halol baho: `.env` da `STORAGE_DIR` absolut qilib berilgani uchun kuzatilgan
  jonli 502 ni CWD nuqsoni bilan TUSHUNTIRIB BO'LMAYDI — shu bosqichda sabab
  faqat **PLAUSIBLE** deb qoldirilgan edi (haqiqiy sabab keyinroq C bo'limida
  topildi).
- Yon topilma, **TUZATILMAGAN**: `app/config.py:8`
  `SettingsConfigDict(env_file=".env", …)` — yo'l CWD'ga nisbiy, shuning uchun
  boshqa papkadan ishga tushirilganda `.env` umuman yuklanmaydi va ilova
  `RuntimeError: JWT_SECRET is unset` bilan yiqiladi (jonli tasdiqlandi),
  `storage_backend` ham sukut `s3` ga tushadi. Ya'ni `local_run.py:25` dagi
  chdir HAMON kerak. Tavsiya: `env_file=Path(__file__).resolve().parents[1] /
  ".env"`. Foydalanuvchiga aytildi, qaror kutilmoqda.

### B. Bulk imzo endpointi

- Sabab (o'lchangan): har bir slot uchun butun base64 rasm qaytadan
  yuborilardi; `app/api/deps.py:35-38` rasmga 2 MiB ruxsat beradi → 11 slot ×
  2 MiB = **~29 MiB yuklash va 11 marta Pillow dekod**.
- `POST /api/contracts/{contract_id}/signatures/bulk` — **har doim 200**,
  chunki qisman muvaffaqiyat normal natija. Body: `image_data`, `field_ids`,
  `signer_name` / `signer_email` / `signer_role`; **`bbox` va `page` sxemada
  YO'Q** (koordinata faqat shablondan). Javob
  `response_model=BulkSignatureResult`:
  `placed[{field_id, signature_id, page_number, bbox}]`,
  `skipped[{field_id, reason:"already_signed"}]`,
  `failed[{field_id, reason:"unknown_field" | "not_a_signature_field"}]` —
  so'ralgan har bir `field_id` uchta ro'yxatdan AYNAN bittasida bo'ladi.
- Butun so'rovni rad etish holatlari: rasm yaroqsiz 422 / juda katta 413 /
  dekompressiya bombasi 422, `_assert_signable` 409, IDOR 403, `field_ids`
  bo'sh yoki >200 yoki **dublikatli** → 422 (dublikat bo'yicha qaror mavjud
  `_resolve_dates` uslubiga mos qilingan).
- `app/api/signatures.py:78-185` — `SLOT_*` konstantalari va
  `_resolve_signature_slot` / `_template_index` / `_taken_slot_ids` /
  `_store_signature`: ikkala endpoint uchun **yagona** darvoza va yagona
  qator-yozish yo'li, shuning uchun ular bir-biridan drift qila olmaydi (bu
  xavfsizlik bir xilligining strukturaviy isboti, sinov emas). `:202-256`
  `place_signature` shu helperlarga o'tkazildi (tashqi xulqi o'zgarmagan);
  `:259-357` `place_signatures_bulk`. Sxemalar:
  `app/schemas/contract.py:128-199` (`MAX_BULK_SIGNATURE_SLOTS = 200`, `:133`),
  `app/schemas/signature.py:1-23`. Audit: `app/services/audit.py:29-31`
  `SIGNATURES_BULK_ADDED` — mavjud hodisa nomlariga tegilmadi va
  `signature_added` har bir joylashtirish uchun saqlanadi.
- Yon kuchaytirish: `_resolve_signature_slot` (`:99-103`) `page is None` ni
  ham rad etadi — ilgari bunday shablon yozuvi `KeyError` → 500 berardi.
- `tests/test_api_signatures_bulk.py` — 15 test, jumladan rasm **bir marta**
  dekod qilinishining spy bilan isboti. **pytest 203 → 218 passed.**
- Jonli o'lchov (~579 KiB PNG, 3 slot): bulk bitta so'rov **792 332 bayt** vs
  eski 3 ta so'rov jami **2 376 825 bayt** → **66.7% tejash**, dekod 3 → 1.
  11 slot × 2 MiB ekstrapolyatsiyasi: ~29 MiB → **~2.7 MiB** (PLAUSIBLE,
  chiziqli hisob — o'lchanmagan). Jonli javob 200, 279 ms; ataylab
  `bbox={0,0,1,1}, page=3` yuborilganda qator shablon bbox'i va `page=1` bilan
  yozildi, ya'ni mijoz koordinatasi e'tiborsiz qoldiriladi.

### C. Storage: ASOSIY ildiz sabab — Windows `\\?\` poyga sharti (CONFIRMED)

- `Path.resolve()` → `ntpath.realpath` (CPython 3.12 `Lib/ntpath.py:726-765`)
  OSdan yo'lni **doim `\\?\` prefiksli** oladi va prefiksni faqat ikki holatda
  olib tashlaydi: (a) prefikssiz shakl muvaffaqiyatli tekshirilsa, yoki (b)
  ikkinchi tekshiruv birinchisi bilan **aynan bir xil** `winerror` bersa
  (`ntpath.py:745-765`).
- Poyga shu yerda: 1-tekshiruvda `signatures/<contract_id>/` hali YO'Q →
  `ERROR_PATH_NOT_FOUND (3)`; oraliqda parallel so'rov `mkdir(parents=True)`
  qilib katalogni yaratadi; 2-tekshiruvda katalog BOR → `ERROR_FILE_NOT_FOUND
  (2)`. `3 != 2` → prefiks joyida qoladi → `candidate = \\?\C:\…` vs `root =
  C:\…` → guard **soxta-musbat** beradi → 502. Darhol qayta urinish o'tib
  ketadi (endi ikkala tekshiruvda ham katalog bor, winerror bir xil) — shuning
  uchun xato «bir martalik g'alati» bo'lib ko'rinardi.
- **Nega ertalabki (A) tuzatish yordam bermadi:** `\\?\C:\…` na `startswith`,
  na `is_relative_to` dan o'tadi — ikkala guard ham bir xil yiqiladi. A bo'limi
  boshqa nuqsonni tuzatgan, bu nuqsonni emas.
- Dalil: sof `Path.resolve()` + threadlar bilan o'lchov — 800 chaqiruvdan
  **40 tasi (5.0%)** `\\?\`-prefiksli qaytdi. Zaiflik nuqtasi
  `app/api/signatures.py:165-166` (`signature_key` → `storage.put_bytes`) →
  `LocalStorage._path`; poyga darchasi har bir kontrakt uchun bir marta, ya'ni
  o'sha kontraktdagi birinchi parallel imzolarda ochiladi.
- **OLDIN/KEYIN o'lchovlari:** `LocalStorage.put_bytes` 4–6 thread bilan:
  **35/600 = 5.8%** → **0/1500**. Jonli `POST …/signatures` 4 parallel:
  **11/84 = 13.1% × 502** → **0/180** (84 + 96, ikki yugurish). «Oldin»
  o'lchovi haqiqiy edi: PID 988 hali eski kod bilan ishlab turgan, 14
  kontraktdan 6 tasi ta'sirlangan.
- Tuzatish, `app/services/storage.py`: `:43-53` `\\?\` / `\\?\UNC\`
  konstantalari, drive-harfi va separator regexlari; `:165` `_resolve_root`
  endi `_strip_extended_prefix(path.resolve())` qaytaradi; `:168-181`
  `_strip_extended_prefix`; `:184-201` `_reject_unsafe_key` (bo'sh kalit, null
  bayt, UNC, drive harfi, absolut yo'l, `..` komponenti); `:217-254` `_path`
  endi **butunlay leksik** — `os.path.normpath` + `is_relative_to`,
  `.resolve()`siz, ya'ni fayl tizimi holatiga umuman tegmaydi; `:256-280`
  `_reject_link_out_of_root` — ikkinchi qator himoya: faqat yozuv MAVJUD
  bo'lganda ishlaydi, ikkala tomonni bir xil normalizatsiyadan o'tkazadi va har
  qanday `OSError`/`ValueError` da «fikrim yo'q» deydi (rad etmaydi) → o'zi
  soxta-musbat bera olmaydi.
- Symlink tahlili (halol): leksik tekshiruv root ICHIDAGI symlink yoki NTFS
  junction'ni ko'rmaydi. Baholash: barcha yozish kalitlari server tomonda
  UUID'dan yasaladi (`storage.py:340-363`), `template_key` / `contract_key` ning
  ixtiyoriy `filename` parametri hech bir chaqiruvchi tomonidan berilmaydi
  (`templates.py:55`, `contracts.py:90` — ikkalasi ham faqat id uzatadi),
  o'qish kalitlari DB ustunlaridan keladi, ular esa `put_bytes(key)` qaytargan
  qiymat. Hech bir endpoint chaqiruvchi tanlagan nom bilan katalog yozuvi
  yaratmaydi → API orqali link o'rnatib bo'lmaydi. Ya'ni bu qatlam faqat
  fayl-tizimiga allaqachon yozish huquqi bor tajovuzkorga qarshi.
- `tests/test_storage_local.py` — **17 passed** (barcha eski traversal testlari
  + 6 yangi). Yangi testlar eski guard'ga qarshi yugurtirilganda **6 tadan 5
  tasi yiqiladi**, ya'ni bular haqiqiy regressiya testlari. Halol izoh: yangi
  rad-holatlarining bir qismi (`\\server\share`, `C:evil.png`, null bayt) eski
  kodda **haqiqiy traversal teshigi EMAS edi** — bu qattiqlashtirish va xato
  tipini tozalash.
- **pytest 218 → 224 passed.**

### D. Frontend bulk'ga ulandi (`tsc` exit 0, `build` OK)

- `src/api/signatures.ts:29-58` tiplar (`BulkPlaced` / `BulkSkipped` /
  `BulkFailed` / `BulkResult`); `:61` `MAX_BULK_SLOTS = 200`; `:65`
  `isBulkUnsupported()` (404/405); `:76` `describeBulkError()`
  (413/409/403/422); `:117` `placeBulk(...)`. `place` / `remove` / `finalize`
  o'zgarmagan.
- `src/components/BulkSignPanel.tsx:11` `BulkSkip`, `:30` `BulkProgress`,
  `:60` `skipped` prop, `:191-195` holat qatori «Applying to N places… (part
  1/2)» → tugagach «X placed, Y skipped, Z failed», `:211-217` «already signed
  — left untouched» tushuntirishi.
- `src/pages/SignaturePage.tsx:64` `BulkOutcome` (`skipped` / `error`), `:78`
  `SLOT_FAILURE_TEXT`; `:384-579` `applyToAll` — `Set` bilan dedup, `replace`
  rejimida avval `remove` (`runPool` 3 parallel), so'ng 200 lik bo'laklarga
  bo'lib ketma-ket bulk, `placed` dan javobdagi `bbox` / `page_number` bilan
  state yangilanadi (qo'shimcha GET yo'q); `:521-533` — 404/405 da `runPool`
  per-slot fallback, 413/422/409/403 da fallback **YO'Q** (bular shu so'rov
  haqidagi haqiqiy javob, uni N marta takrorlash ma'nosiz); `:581`
  `runBulkApply`; `:638` avto-to'ldirish effekti; `:1010` panelga `skipped`
  uzatiladi.
- Brauzer tasdig'i (sintetik 3 sahifali kontrakt, 3 imzo + 3 sana sloti):
  avto-to'ldirish **1 ta** bulk so'rov yubordi (per-slot POST umuman yo'q) →
  «3 placed»; oldindan band slot bilan → «2 placed, 1 skipped» + tushuntirish
  qatori; undo 3× DELETE 200; per-slot regressiya DELETE 200 → POST 201;
  finalize 200 `completed`; rejim/belgi tanlovlari reload'dan keyin saqlandi;
  fallback sinovi (bulk 404 qilib mock qilinganda) → 3× per-slot 201; 413 →
  fallback yo'q, aniq xabar. Skrinshotlar
  `c:\PTG\.playwright-mcp\swm_bulk2\01…07`.
- Ma'lum trade-off: «Replace all» avval DELETE, keyin bulk qiladi — bulk rad
  etilsa eski imzolar o'chgan holda qoladi (xato ko'rsatiladi, qayta urinish
  mumkin). Teskari tartib mumkin emas, chunki endpoint band slot ustiga
  yozmaydi.

### E. Ma'lumot tozalash (devops, foydalanuvchi ruxsati bilan)

Sessiyadan qolgan sun'iy yozuvlar (44 ta `ZZ-STORAGE-RACE-PROBE` kontraktdan
boshlab) har bosqichda zaxira olinib, uch bosqichda o'chirildi. To'liq
raqamlar, o'chirilganlar ro'yxati, zaxiralar va tasdiqlar — «F. Ochiq /
keyingi bosqich» bo'limining **5-bandida** (bu yerda takrorlanmaydi).
Bugungi kod ishlari oldidan olingan boshqa zaxiralar:
`local.db.bak-20260912-sigalign` (imzo joylashuvi tuzatishidan oldin) va
`local.db.bak-20260912-bulksig` (bulk endpoint ishidan oldin).

### F. Ochiq / keyingi bosqich

1. `app/config.py:8` `env_file` CWD'ga nisbiy — TUZATILMAGAN, foydalanuvchi
   qarori kutilmoqda (A bo'limi).
2. **PLAUSIBLE, sinalmagan:** `storage.py:288-299` — `put_bytes` vaqtinchalik
   faylining nomi `<key>.partial`, ya'ni kalitdan kelib chiqadi. Bir xil
   kalitga ikki parallel yozuv (amalda faqat `final_key`, ya'ni bitta
   kontraktni bir vaqtda ikki marta finalize qilish) Windows'da
   `ERROR_SHARING_VIOLATION` berishi mumkin. Arzon tuzatish:
   `.partial.<uuid4>`.
3. «Replace all» atomik emas (D bo'limi trade-off'i).
4. ~~2026-09-10 dagi override oqimining brauzer uchdan-uchgacha tekshiruvi~~ —
   **YOPILDI (2026-09-13, PASS)**. Tafsilot: 2026-09-10 yozuvining «D. Brauzer
   uchdan-uchga» bandi (jonli E2E, `61418c12…` override 200 → finalize 200,
   409/422 salbiy holatlari, `128b724c` faqat ko'rish uchun).
5. Sessiyadan qolgan boshqa sun'iy yozuvlar (`SYNTHETIC` / `E2E` / `e2e-`
   nomlilar) — o'chirish uchun foydalanuvchidan alohida so'raladi.
   **Bajarildi: 2026-09-12/13 da uch bosqichda tozalandi.**
   - (i) 44 ta `ZZ-STORAGE-RACE-PROBE` kontrakt (zaxira
     `local.db.bak-20260912-probecleanup`): contracts 65→21,
     contract_fields −1760, audit o'chirilmadi (746 `contract_id` NULL),
     storage 44+44 katalog. (E bo'limi bilan bir xil bosqich.)
   - (ii) sun'iy hisob/shablon/kontrakt (zaxira `-synthpurge`): users 14→4,
     templates 20→8, contracts 21→14, audit_logs 1075→1075 (65 `contract_id`
     + 130 `template_id` + 26 `actor_id` NULL, `actor_email` saqlandi),
     30 storage katalog. **Muhim:** `ad1a4236` shabloni o'chirish ro'yxatida
     edi, lekin unga saqlanadigan 2 kontrakt (`6fc89a8d`, `4120ac6f`) havola
     qilgani aniqlanib o'chirilmadi; xuddi shunday 3 hisob himoyalangan
     kontraktlarning `uploaded_by` (NOT NULL + RESTRICT) egasi bo'lgani uchun
     qoldirildi.
   - (iii) yakuniy tozalash — «Charles Frye» zanjiri va override E2E
     qoldiqlari. **Bajarildi 2026-09-13.** Zaxira
     `backend/local.db.bak-20260913-finalpurge` (3 084 288 bayt, SQLite
     **online backup API** — backend to'xtatilmadi; `integrity_check` ok,
     `foreign_key_check` bo'sh, sonlar jonli bazaga mos).
     - O'chirishdan oldingi 3 assert o'tdi: (a) himoyalangan ↔ o'chiriladigan
       kesishmasi 0 (contracts/templates/users); `contractor_name IS NULL`
       bo'lgan 12 kontrakt himoyaga qo'shildi va 8 nomli ∪ 12 null-nomli =
       **aynan 13** (qoladigan hamma kontraktni qoplaydi); (b) har 7
       o'chiriladigan shablonga **faqat** o'chiriladigan kontrakt havola
       qiladi; (c) har 7 o'chiriladigan hisob faqat o'chiriladigan
       kontrakt/shablonga bog'liq, `signatures.placed_by_user_id` bo'yicha
       ham kross-havola 0.
     - Sonlar: users **8→1** (faqat seed admin), templates **11→4**,
       contracts **20→13**, contract_fields 1535→1395, signatures 45→37,
       user_signatures 1→1, **audit_logs 1245→1245 (bitta ham
       o'chirilmadi)** — `contract_id` 81, `template_id` 28, `actor_id` 70
       NULL qilindi; `actor_email` / `actor_role` saqlandi.
     - O'chirilganlar — A) «Charles Frye» zanjiri: kontraktlar `37e097d3`,
       `2ee820c6`, `760f4ea4`, `6e473899`; shablonlar `3100d9aa`,
       `9a041418`, `2192d536`, `2151ab28`; hisoblar `swm-bulk-test@`,
       `swm-bulk2-1789228625@`, `swm-bulk2-1789228795@`. B) override E2E
       qoldiqlari: kontraktlar `61418c12`, `ff73f6a9`, `0111a1b2`;
       shablonlar `8b5be210`, `3a1f6c91`, `5d6b62a2`; hisoblar `zz-ovr-a1@`,
       `zz-ovr-b2@`, `zz-ovr-c3@`, `zz-ovr-reviewer@`. `61418c12` dagi imzo
       DB darajasida kontrakt bilan ketdi (API 409 to'sig'i aylanib
       o'tilmadi).
     - Storage: 25 katalog (~108.6 KB) — contracts 7, final 3, signatures 5,
       templates 7, user-signatures 3 (jumladan oldingi bosqichdan qolgan 3
       bo'sh katalog); kutilmagan to'la katalog uchramadi; 10 nishon
       allaqachon yo'q edi (imzosiz/finalize qilinmagan kontraktlar —
       normal).
     - Tasdiq: `integrity_check` ok, `foreign_key_check` bo'sh, 8 ta
       yetim-tekshiruvning hammasi 0, yetim fayl 0; `/health` 200;
       `GET /api/contracts` → 13 ta, 8 himoyalangan joyida;
       **`GET /api/contracts/128b724c…/compare` → 200, `missing_required`
       field_id'lari aynan `['p3_vehicle_assignment']`** (regressiya yo'q);
       `GET /api/templates` → `02fc4235`, `befea049`, `ad1a4236`,
       `1c6f091a` joyida. Backend (PID 37336/30848) va frontend :5173
       (PID 10144) to'xtatilmadi; kod/testlar tegilmadi; `VACUUM`
       qilinmadi.
     - Qolgan xatar: `VACUUM` qilinmagani uchun `local.db` 3 084 288 baytda
       qoldi (o'chirilgan sahifalar freelist'da — «izsiz o'chirish» kerak
       bo'lsa alohida xizmat-to'xtatish oynasi); bazada endi **bitta** hisob
       bor (seed admin) — kontraktor/reviewer oqimini keyin sinash uchun
       yangi hisob kerak; eski 8 ta `local.db.bak-*` zaxirasida o'chirilgan
       sun'iy yozuvlar hamon mavjud.
     - Sinov artefaktlari: `%TEMP%\swm_probe` (54 MB — asosan scratch
       PyMuPDF kutubxonasi + sintetik `d1–d7.pdf`) **o'chirildi**; qolgani
       `%TEMP%\swm_e2e` 832 KB (jonli backend log shu yerda), `swm_sig`
       5.5 MB, `swm_ovr` 63 KB, `swm_bulk` 37 KB, `swm_bulk2` 20 KB,
       `c:\PTG\.playwright-mcp` 3.8 MB (JURNAL havola qilgan skrinshotlar;
       shundan ikkitasi — `swm_ovr\09-real-contract-readonly.png` va
       `09b-…-decision-panel.png` — HAQIQIY kontrakt ko'rinishini o'z ichiga
       oladi).
       (09 va 09b — haqiqiy kontrakt ko'rinishi, PII sabab 2026-09-15 da o'chirildi)
   - Har bosqichda `integrity_check` ok, `foreign_key_check` bo'sh, yetim
     qator/fayl 0, `128b724c` compare o'zgarmagan (`missing_required` =
     `['p3_vehicle_assignment']`), backend restart qilinmadi.
   - Foydalanuvchi `1c6f091a` («222222») shablonini **qoldirishni** so'radi.

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `comparison_engine.py` `can_sign`, `pdf_parser.py`,
`field_detector.py`, `validators.py`, `conftest.py`, `local_run.py`. Backend
qayta ishga tushirildi: launcher PID 37336 → xizmat ko'rsatuvchi **PID 30848**,
`/health` 200. Frontend :5173 (PID 10144) tegilmadi.

---

## 2026-09-14 — Imzo foni, chiziq siyohiga langar, joylashuv sozlamalari (surish/hajm/shrift), o'chirish, sahifa himoyasi, dublikat ogohlantirishi (appsec + frontend + devops)

Foydalanuvchi 5 ta shikoyat bilan qaytdi. Ikkitasi (fon va «teparoqda») bitta
rasmdan kelib chiqqani aniqlandi, yana ikkita mustaqil sabab topildi (render
qilingan chiziq baseline'da emas; imzo ekranida rasm umuman chizilmagan).
Shundan so'ng quyidagilar qurildi: chiziq siyohiga langar, joylashuv paneli,
server tomonda fon olib tashlash, o'chirish, yo'q sahifalar himoyasi va
dublikat ogohlantirishi. Yo'l-yo'lakay yakunlangan kontraktlar yaxlitligi va
2 ta noto'g'ri shablon bog'lanishi faqat o'qish rejimida tekshirildi.
Izoh: `fayl:qator` havolalari yozuv paytida joriy kod bilan spot-check
qilingan. O'lchov raqamlari, DB/hash tasdiqlari va «eski» qator raqamlari
**agent hisobotlariga ko'ra** yozilgan (reporting ularni qayta o'lchamagan).

### A. Foydalanuvchi shikoyati va qarorlari

Shikoyatlar (5):

| # | Shikoyat |
|---|---|
| 1 | Yuklangan imzo **fon bilan** qo'yilyapti |
| 2 | Avto-qo'yilgan imzo/sanalarni **yuqori-pastga surish** imkoni yo'q |
| 3 | Imzo **hali ham azgina teparoqda** |
| 4 | Fayllarni **o'chirish** imkoni yo'q («ishlatib bo'lgach o'chirilsin») |
| 5 | Imzo va sana uchun **hajm va shrift** tanlash |

Qarorlar (AskUserQuestion):

| Mavzu | Qaror |
|---|---|
| O'chirish | **Faqat qo'lda tugma**, tasdiq bilan, faqat admin, audit qoladi. Avto-o'chirish rad etildi. FMCSA 49 CFR 391.51 bo'yicha saqlash talabi haqida ogohlantirilgan |
| Surish | **Hammasini birdan + har slotni alohida**, musbat qiymat = yuqoriga |
| Hajm/shrift | Imzo 60–150%, sana 8–16 pt, shrift Helvetica/Times/Courier; sozlama hammasiga qo'llanadi |
| Fon olib tashlash | 09-12 da rad etilgan edi, **endi foydalanuvchi o'zi so'radi** → sukut bo'yicha yoqiq, avtoritet backend'da |
| Keyinroq | Noto'g'ri bog'langan 2 dublikat kontraktni **o'chirish** (H bo'limi); yuklashda **dublikat ogohlantirishi**, bloklamaydi (F bo'limi) |

### B. Diagnostika (appsec, faqat o'qish) — ikki shikoyat bitta rasmdan + qo'shimcha sabablar

- **R1 (CONFIRMED):** «Make white background transparent» belgisi sukut
  bo'yicha O'CHIQ edi (eski `frontend/src/hooks/useSignatureUpload.ts:83`),
  backend esa fonni olib tashlamasdi (eski `app/api/deps.py:149`). Ikkala
  eski qator raqami agent hisobotiga ko'ra. Kutubxona imzosi `1867b434`:
  940×415, **to'liq noshaffof** (alfa min 255), 89.3% piksel oqqa yaqin. U
  shablon `02fc` dagi yakunlangan kontraktlarda ishlatilgan, shu sabab oq quti
  chiziqni yopgan: rasmning pastki qirrasi baseline'dan 3.51 pt pastda, `_`
  esa 2.0–2.5 pt pastda.
- **R2 (CONFIRMED):** eski `pdf_builder.py:347` `_rule_y` matn
  **baseline**'ini qaytarardi. Docusign shablonida `_` siyohi baseline'dan
  **+2.00…+2.13 pt** pastda (74 slot, pypdfium2). Natijada toza PNG siyohi
  chiziqdan **2.92 pt** yuqorida turardi (median; 35 slotdan 12 tasida >3 pt),
  sana esa **4.0–4.13 pt** yuqorida. 09-12 dagi «+1.83 pt» boshqa shriftli
  reportlab sintetik PDF'idan olingan edi.
- **R3 (CONFIRMED):** `_ink_metrics` `getbbox()` ishlatardi. Telefon surati,
  kremrang qog'oz yoki skaner JPEG'da butun rasm «siyoh» hisoblanadi → siyoh
  chiziqdan **10–12 pt** yuqorida. T1 varianti (threshold 235) sinaldi:
  telefon suratida fon 100% qoladi, kremrangda 100%, skanerda 0.2% dog'.
- **R4 (CONFIRMED, kod):** imzo ekranida rasm umuman chizilmasdi. O'rnida bo'sh
  `div.placed-signature` bor edi (eski `SignaturePage.tsx:894-907`, agent
  hisobotiga ko'ra), uning `border-bottom` i bbox pastida → chiziqdan
  ~11.5 pt yuqorida.
- **Tekshiruv saboq'i:** 09-12 dagi joylashuv tuzatishi faqat sintetik PDF'da
  va faqat yakuniy PDF'da o'lchanib, «tuzatildi» deyilgan edi. Haqiqiy
  shablon, ekrandagi overlay va fonli rasm tekshirilmagan — shikoyat shu
  sabab qaytdi.

### C. B1 — chiziq langari, layout, sahifa himoyasi (appsec)

- YANGI `app/services/rule_locator.py`: `RuleLocator` `:51`,
  `_rendered_rule_top` `:145`, `with_rule_y` `:222`, `ensure_template_rules`
  `:248`, `contract_page_count` `:284`, `signing_comparison` `:322`.
  - Usul: sahifa pypdfium2 bilan 8 px/pt da render qilinadi va bbox ostidagi
    oyna (`y1−6…y1+16`) ko'riladi. Kulrangi <128 piksellar kenglikning
    ≥50% ini qoplagan, qalinligi ≤2 pt tasma chiziq deb olinadi va uning tepa
    qirrasi qaytariladi. Vektor chiziq/rect bo'lsa pdfplumber ishlatiladi.
    Topilmasa `null`.
  - Mustaqil tekshiruv: 16 px/pt to'liq renderda siyoh tepasi 593.438,
    locator 593.5 (farq 0.06 pt).
  - **AFM metrikasi noto'g'ri** (592.825): shrift PDF'ga yozilmagan, pdfium
    boshqa shrift chizadi.
  - `rule_y` `field_schema` ga additive qo'shiladi: yangi shablonlarda tahlil
    paytida (`workers/tasks.py:60`, `:127`). Eski shablonlarda
    `GET /compare` birinchi chaqirilganda bir marta yoziladi.
- `app/services/pdf_builder.py`: `PageOutOfRangeError` `:72`, `Layout` `:82`,
  `signature_ink_bottom` `:129`, `date_baseline` `:133`, sahifa guard `:161`.
  `_projected_box` `:350` va `_ink_metrics` `:380` endi qator/ustun
  proyeksiyasi bilan ishlaydi: ≥0.5% piksel, ≥2 ketma-ket qator yoki ≥5% kuchli
  qator.
- `app/services/comparison_engine.py`: `_slot_entry` ichida `rule_y` `:412`,
  `_slot_page_in_range` `:416`, `pending_signature_slots(..., page_count=None)`
  `:427`. **`can_sign` ifodasi o'zgarmagan** (`:201`).
- `app/api/signatures.py`:
  - `_assert_page_exists` `:101` → 422 `page_out_of_range`; bitta imzo
    yo'lida `:264`, `:272` chaqiriladi.
  - Bulk yo'lida sahifa tashqarisidagi slot `failed` ga tushadi (`:343`).
  - YANGI `GET /api/contracts/{cid}/signatures/{sid}/image` (`:412`,
    `get_signature_image` `:413`): 403/404, `no-store` + `nosniff` (`:430`).
  - Finalize'da yo'q sahifadagi joylashtirishlar → 409 (`:499-508`);
    builder'dan kelgan `PageOutOfRangeError` `:566-570` da ushlanadi.
  - Audit `details.layout` `:599`; `_assert_layout_slots` `:704`.
- `app/api/contracts.py:210` `GET /{contract_id}/compare` endi
  `signing_comparison` ni chaqiradi (`:223`). Sxemalar:
  `schemas/comparison.py:57` `rule_y`; `schemas/contract.py:260`
  `FinalizeLayout` (`extra="forbid"` `:265`; NaN/inf rad etiladi — agent
  hisobotiga ko'ra).
- **Kontrakt v1.1 (erkin imzo):** `line = bbox bottom`; faqat global
  `signature_offset_pt` va `signature_scale` qo'llanadi. Avval bu yerda ham
  jonli chiziq qidirilardi — tuzatildi. Sana x = `bbox.x` (eski
  `bbox.x+2.0` formulasi kontraktga zid edi). Sana formati `MM/DD/YYYY`
  (`services/validators.py:102`).
- **422 ikki formatda keladi:** `layout` validatsiyasi → FastAPI
  `{"detail":[...]}`; noma'lum slot kaliti → `{"error","message",
  "details":{"unknown_slot_offsets":[...]}}`.
- **Sahifa soni:** modelda ustun yo'q, shuning uchun `contract_page_count`
  yuklangan PDF sahifalarini pypdf bilan sanaydi (`file_path` + `file_hash`
  kesh). Saqlangan `comparison_result` da p32/p34 slotlari qoladi; filtr
  o'qish paytida va finalize'da qo'llanadi.
- **Haqiqiy shablon `02fc4235` nusxasida quruq ishga tushirish** (agent
  hisobotiga ko'ra): 76 slotdan 74 tasida `rule_y` bor (null:
  `p1_cdl_expiration`, `p14_cdl_expiration`). Glif bo'yicha
  `rule_y − baseline` mediani +1.97 pt. Siyoh–chiziq oralig'i (pt):

| Slot turi | n | min | median | max | >2 pt | [0.5;1.5] dan tashqari |
|---|---|---|---|---|---|---|
| Imzo | 35 | 0.90 | **1.02** | 1.02 | 0 | 0 |
| Sana | 39 | 0.90 | **0.90** | 1.02 | 0 | 0 |
| `rule_y = null` | 2 | 0.90 | — | 1.02 | — | — |

  Birinchi o'lchashdagi 1.40/−5.85 o'lchash xatosi edi (qo'shni overlaylar
  oynaga tushgan). Izolyatsiyada qayta o'lchandi.
- Testlar: `tests/test_rule_locator.py`, `tests/test_pdf_builder_layout.py`,
  `tests/test_api_layout_pages.py`. **pytest → 326 passed.**

### D. B2 — fon olib tashlash, siyohga qirqish, o'chirish (appsec)

- YANGI `app/services/signature_image.py`: `prepare_signature_image` `:71`,
  `remove_background` `:88`, `ink_box` `:219`, `crop_to_ink` `:261`.
  `app/api/deps.py:164` uni barcha validatsiyadan keyin chaqiradi (413,
  dekompressiya bombasi, `verify()`, format, polyglot tekshiruvlari
  o'zgarmagan).
  - Usul: 256 px nusxada closing + blur bilan yoritish tekislanadi. Har
    piksel uchun mahalliy qog'ozdan qanchalik to'qligi hisoblanadi. Shovqin
    8–16 px chekka halqasidan median + MAD bilan baholanadi, keyin Otsu.
    Alfa yumshoq rampa bilan beriladi: `max(shovqin, Otsu/2)` dan
    `max(Otsu, past+24)` gacha.
  - Qirqish qator/ustun proyeksiyasi bilan: yadro ≥`max(4 px, 5%)`, faqat
    uzluksiz qatorlar orqali o'sadi. Chekka: chap/tepa/o'ng 2 px, **past 0 px**.
  - ≥2% i allaqachon shaffof rasm tozalanmaydi, faqat qirqiladi.
  - Qo'shimcha himoya: to'liq shaffof piksellar ostidagi RGB nolga
    tushiriladi, ya'ni yashirin sahifa mazmuni PNG ichida qolmaydi.
  - Faqat Pillow ishlatiladi, yangi paket qo'shilmagan.
- O'lchov jadvali (sintetik rasmlar, niqob oldindan ma'lum; agent hisobotiga
  ko'ra):

| Holat | Qolgan fon | Recall / precision | Pastki qirra | Vaqt |
|---|---|---|---|---|
| (i) telefon JPEG (oldin 100% fon) | **0.00%** | 100 / 100 | +0 px | 69 ms |
| (ii) skaner q70 | 0.00% | — | +0 px | 73 ms |
| (iii) kremrang | 0.00% | — | +0 px | 65 ms |
| (iv) oq PNG | 0.00% | — | +0 px | 55 ms |
| (v) ingichka dum | 0.00% | — | +1 px | 73 ms |
| (vi) och-ko'k siyoh | 0.00% | — | +0 px | 61 ms |
| (vii) dog'lar | 0.01% | precision 99.9% | — | 68 ms |
| (viii) shaffof pad (tozalash o'tkazib yuboriladi) | — | — | — | 16 ms |
| (ix) **4000×4000** | 0.00% | — | — | **1196 ms** |

  Sintetik rasmlar uchun natija CONFIRMED. Haqiqiy telefon suratlari (stol
  cheti, keskin soya) uchun — PLAUSIBLE.
- **`DELETE /api/contracts/{id}`** (`app/api/contract_admin.py:62-63`):
  - admin → 200; boshqalar 403; yo'q bo'lsa 404; `uploaded`/`parsing` holatida
    409.
  - Bitta tranzaksiyada: audit qatorlarida `contract_id` NULL qilinadi
    (qatorlar o'zi qoladi) va `contract_deleted` yoziladi (`:89`; `details`:
    `contract_id`, `status`).
  - `contract_fields` va `signatures` ORM cascade bilan o'chadi (jonli
    SQLite'da FK o'chiq).
  - Fayllar COMMIT'dan keyin o'chiriladi. Fayl o'chirishda xato bo'lsa log
    yoziladi va baribir 200 qaytadi — yetim fayl qolishi mumkin.
- **`DELETE /api/templates/{id}?purge=true`** (`app/api/templates.py:221`,
  `purge` param `:225`, `_purge_template` `:248`):
  - Kontrakt havola qilsa → 409 `contract_count`; aks holda qator, fayllar va
    `template_deleted` (`:266`).
  - Hisoblash bilan commit orasidagi poyga → `IntegrityError` → 409
    (`:275-281`).
  - `purge` siz chaqiruv avvalgidek arxivlaydi.
- `storage.delete_prefix` (`app/services/storage.py:142` S3, `:348` Local;
  regex `_SCOPED_PREFIX` `:64`): faqat `<prefiks>/<bitta id>` shaklini qabul
  qiladi, har fayl `_path` guard'idan o'tadi. Audit konstantalari
  `app/services/audit.py:42-43`, sxema `app/schemas/deletion.py`.
- Testlar: `tests/test_signature_image.py` (agent hisobotiga ko'ra 16 holat;
  statik sanashda 9 `def test_`, shundan 2 tasi 4 holatli parametrize — ya'ni
  15 ta; pytest yig'ishida tasdiqlanmagan), `tests/test_api_deletion.py` (11).
- **Kutubxona qayta ishlandi** (zaxira
  `backend/storage_backup_20260914/user-signatures/424b0640-…/`, katalog
  mavjud):

| Imzo | Oldin | Keyin | Izoh |
|---|---|---|---|
| `1867b434` | 940×415, alfa>0 **100%** | 855×319, alfa>0 **12.0%** | fon olib tashlandi + qirqildi |
| `d62d89e1` | 1266×559 | 1154×431 | allaqachon shaffof → faqat qirqildi |

  Fayl hash = DB hash tasdiqlangan. Kontrakt imzolari va yakuniy PDF'larga
  tegilmagan (agent hisobotiga ko'ra).
- **Topilma (CONFIRMED):** ikkala kutubxona imzosi ham **Docusign «Signed by:»
  blokidan kesib olingan**: binafsha qavs + pastida boshqa konvertning ID
  matni. Siyohga qirqilganda pastki qirra shu ID matni bo'lib chiqadi, ya'ni
  imzo baribir chiziqdan yuqorida turadi. Buni kod bilan tuzatib bo'lmaydi.
  Foydalanuvchiga faqat imzoning o'zini yuklash tavsiya etildi. Boshqa
  konvertning Docusign ID'sini yangi kontraktlarga bosish yuridik jihatdan
  chalg'ituvchi ekani ham aytildi.

### E. F — frontend (general-purpose; `tsc` EXIT 0, `build` OK)

- YANGI fayllar:
  - `src/lib/layout.ts` — `layoutForRequest` `:146`, `signatureRect` `:176`,
    `dateBaseline` `:197`.
  - `src/hooks/useContractLayout.ts:15`.
  - `src/hooks/useSignatureImages.ts:29` — rasmni token bilan blob URL
    sifatida oladi, keshlaydi.
  - `src/components/LayoutPanel.tsx:83` — «Position & appearance»:
    All signatures / All dates ▲▼ (0.5 pt, Shift bilan 2 pt, oraliq
    [-20, 20]); Signature size 60–150%; Date font; Date size 8–16; Reset;
    «Each slot» ▲▼.
- `src/pages/SignaturePage.tsx`:
  - `readLayoutRejection` `:99` ikkala 422 formatini o'qiydi. Noma'lum
    `unknown_slot_offsets` nomi bilan ko'rsatiladi va saqlangan layout'dan
    olib tashlanadi (`:817-829`; `localStorage` ga yozish
    `lib/layout.ts:135-136`).
  - `page_out_of_range` uchun matn; finalize so'roviga `layout` qo'shildi.
  - Bo'sh div o'rniga §3 bo'yicha `<img>`; sanalar SVG matn sifatida tanlangan
    shrift va o'lchamda chiziladi.
- `src/pages/Dashboard.tsx:36` — kontraktni o'chirish (admin; `completed`
  uchun kuchli tasdiq). `src/pages/TemplateManager.tsx:68` (`removeTemplate`,
  purge) va `:93` (`archiveInstead`) — 409 da «Archive instead»
  taklif qilinadi. `src/styles/index.css` — yashil to'ldirish foni olib
  tashlandi.
- **Asl rasm yuklanadi:** `src/hooks/useSignatureUpload.ts:217`
  `readAsDataUrl`, `:283` fayl va asl data-URL parallel o'qiladi,
  `toPreviewDataUrl` `:302`. Serverga ASL fayl baytlari ketadi (kutubxona,
  bitta slot, bulk); Otsu tozalash faqat preview uchun. «Remove background»
  belgisi olib tashlandi, o'rnida «Background is removed automatically.»
  (`src/components/SignatureUploadPad.tsx:69`, preview uzatish `:92`).
  Sabab: backend ≥2% shaffof rasmni tozalamay o'tkazib yuboradi, mijoz qisman
  tozalasa kulrang qoldiq qolardi.
- Mock tekshiruv (agent hisobotiga ko'ra):
  - joylashuv `line − 1 − offset` dan **0.02 pt** ichida;
  - offset, slot, hajm, shrift, reload va Reset ishlaydi;
  - finalize'dagi `layout` aynan mos;
  - o'chirish oqimlari ishlaydi;
  - upload tanasi = asl JPEG base64 (preview canvas emas);
  - chizilgan imzo o'zgarmagan;
  - 422 ning ikkala formati to'g'ri o'qiladi.

  Skrinshotlar: `c:\PTG\.playwright-mcp\swm_layout\`.

### F. Dublikat ogohlantirishi (appsec; pytest **337 passed**, `tsc`/`build` OK)

- `file_hash` = `hashlib.sha256(data).hexdigest()` asl PDF baytlaridan
  (`services/pdf_parser.py:93-94`, yuklashda `api/contracts.py:140`), ustun
  `String(64)` (`models/contract.py:38`) — CONFIRMED.
- `GET /api/contracts/duplicates?sha256=` (`api/contracts.py:93`):
  - Hash `^[0-9a-fA-F]{64}$` bo'lmasa 422.
  - Javob: `{id, status, review_status, template_id, template_name,
    created_at}` — PII yo'q, ≤20 ta, eng yangisi birinchi.
  - Route `/{contract_id}` (`:197`) dan oldin e'lon qilingan.
  - Ruxsat `_can_view` (`:68`) → `assert_can_view_contract`
    (`api/deps.py:185-193`). Son ham, kesish ham filtrdan keyin, shuning uchun
    ko'rinmaydigan kontraktlar soni oshkor bo'lmaydi.
- Yuklash o'zgarmagan (202). `contract_uploaded` audit yozuviga
  `duplicate_of_count` qo'shildi (`api/contracts.py:143`, `:169`). Testlar:
  `tests/test_api_duplicates.py` (11).
- **🚩 CONFIRMED:** `http://10.31.0.58:5173` da `isSecureContext:false`,
  **`crypto.subtle` yo'q**. Tekshiruv faqat Web Crypto'ga tayanganda
  ishlab chiqarishda hech qachon ishlamas edi. Yechim:
  `frontend/src/lib/sha256.ts` — sof-TS SHA-256 zaxira varianti
  (`sha256Portable` `:40`; NIST vektorlari mos; 50 MiB ≈ 414 ms, Python bilan
  teng — agent hisobotiga ko'ra).
- `src/pages/ContractUpload.tsx`: yuborishdan oldin hash olinadi → modal (sana,
  holat, shablon nomi; shablon farqli bo'lsa sariq belgi) → Cancel / «Upload
  anyway» (`:190`). 404, tarmoq xatosi yoki 8 s timeout
  (`DUPLICATE_CHECK_TIMEOUT_MS` `:9`) bo'lsa jimgina oddiy yuklash
  davom etadi.
- Nomuvofiqlik (zaiflik emas): `list_contracts` kontraktorni faqat
  `uploaded_by` bo'yicha filtrlaydi (`api/contracts.py:193`),
  `assert_can_view_contract` esa `contractor_email` ni ham qabul qiladi
  (`api/deps.py:191`).

### G. Yakunlangan kontraktlar yaxlitligi (faqat o'qish)

Shablon `02fc4235` 34 betlik, kontraktlar esa 31 betlik. 32–34-betdagi slotlar
majburiy deb talab qilingan, ularning joylashtirishlari esa yakuniy PDF'dan
jimgina tushib qolgan.

**Hukm** (agent hisobotiga ko'ra): 4 ta yakunlangan kontrakt — `44032475`,
`e5890a35`, `444c5977` va yangi topilgan **`c440fdb8`** — **to'liq
imzolangan**. Asoslar:

| Tekshiruv | Natija |
|---|---|
| 1–31-sahifalardagi majburiy imzo/sanalar | hammasi yakuniy PDF'da bor |
| `final_file_hash` ↔ fayl SHA-256 ↔ `contract_finalized` audit hash | mos |
| Original hash ↔ DB | mos |
| 1–31-sahifalar ↔ shablon (difflib) | min 0.80, siljish 0 |
| Tushib qolganlar | faqat mavjud bo'lmagan `p32_signature`, `p32_date`, `p34_signature`, `p34_date` |

DB va audit'da ular «qo'yilgan» deb turibdi — shunday qoldirish tavsiya
etildi. Xavf ostida bo'lgan yakunlanmagan kontraktlar: `6a5927d3`, `3827021f`,
`a736d702`, `128b724c`, `8502c141`, `6508d928`. Ularni endi C bo'limidagi
himoya yopadi.

Eslatma: pdfplumber sanani ostidagi `_` bilan bitta so'z qilib o'qiydi
(`_9_9_/…`) va soxta «sana yo'q» signalini beradi. Sanalar pypdf bilan
tasdiqlangan.

### H. Noto'g'ri shablon bog'lanishi (faqat o'qish; SHA-256 bayt-baayt)

| Kontrakt | Aslida qaysi fayl | Bog'langan shablon | Ta'sir |
|---|---|---|---|
| **`81677830`** | `befea049` source.pdf va `c190c353` bilan bir xil fayl (dublikat); difflib `befea049` bilan 1.000, siljish 0, Envelope ID mos | `02fc4235` (34 bet) | 120 maydon (to'g'risi 181), 35–49-betlar tekshirilmagan; 09-10 da shu noto'g'ri bog'lanish bilan reparse qilingan |
| **`4120ac6f`** | `02fc4235` ga bog'langan 10 kontrakt bilan bir xil fayl | 1 betlik sinov shabloni `ad1a4236` | 7 maydon, 30 sahifa tekshirilmagan, 4 ogohlantirish noto'g'ri sxemadan; **shu ma'nosiz natija bilan `rejected`** |

- `befea049` va `02fc4235` bitta Docusign paketining ikki varianti (Envelope
  ID bir xil).
- Qayta bog'lash endpointi yo'q: `template_id` faqat yuklashda beriladi
  (`api/contracts.py:125`, `:146`).
- DB'da `template_id` ni almashtirib reparse qilish XAVFLI:
  - audit bo'shlig'i paydo bo'ladi (`contract_reparsed` faqat yangi id bilan
    yoziladi);
  - `sync_contract_fields` yangi sxemada yo'q eski qatorlarni o'chirmaydi,
    faqat upsert qiladi (`services/template_mapper.py:38-107`);
  - reparse `review_status` ni saqlab qoladi (`api/contracts.py:288`);
  - baza jonli SQLite.
- **Qaror:** ikkala kontrakt rasmiy `DELETE /api/contracts/{id}` orqali
  o'chiriladi. Qayta yuklanmaydi, chunki ular dublikat.
- Bitta fayl bazaga 11 marta yuklangani dublikat ogohlantirishiga (F bo'limi)
  sabab bo'ldi.

### I. Yakuniy integratsiya

Backend restart, to'liq pytest va `http://10.31.0.58:5173` orqali jonli
uchdan-uchga tekshiruv rejalashtirilgan. Tekshiriladi: fonli surat yuklash,
dublikat, sahifa himoyasi, ekrandagi imzo, joylashuv paneli, finalize,
yakuniy PDF o'lchovi, ekran↔PDF mosligi, o'chirish va ruxsatlar. Shuningdek
haqiqiy `128b724c` faqat o'qish rejimida tekshiriladi va
`81677830`/`4120ac6f` o'chiriladi. Natija quyida (I.1–I.7).

Izoh: I.1–I.7 dagi o'lchov, DB va jonli HTTP raqamlari **agent hisobotlariga
ko'ra** yozilgan (reporting ularni qayta o'lchamagan). Yozuv paytida
(2026-09-15) reporting o'zi tekshirgani: `pytest --collect-only` jami **341**,
`tests/test_signature_image.py` **19**; `_defringe`
`app/services/signature_image.py:144` (`remove_background` ichida chaqiruv
`:139`); `local.db.bak-20260914-dupdelete` va
`local.db.bak-20260915-zzintusers` mavjud; PID 35884, 17676, 10144 ishlayapti;
I.7 da o'chirilgan deyilgan `swm_int\state.json`, `swm_repro\count_probe.py`,
`swm_repro\live_parallel.py`, ikkala `page-2026-09-12T…yml` va 09/09b PNG
diskda yo'q.

#### I.1 Integratsiya — PASS

2026-09-14/15, `http://10.31.0.58:5173` orqali jonli, sintetik `ZZ-INT-…`
ma'lumot bilan. pytest **337 passed**, `tsc` EXIT 0, `build` OK. Backend
restart: 37336/30848 → launcher 7964 → xizmat 12316, `/health` 200.

| Qadam | Natija |
|---|---|
| A. Fonli surat | So'rov tanasidagi base64 asl JPEG bilan bayt-baayt bir xil (24 880 bayt); POST 201; saqlangan PNG 4 burchagida alfa 0, noshaffof 7.18% (manba siyoh 6.35%), pastki chekka 0 px |
| B. Dublikat | 1-marta modal yo'q (202); 2-marta «This file was already uploaded» + shablon nomi; Cancel → 0 so'rov; `/duplicates` 200; brauzerda `isSecureContext=false`, `crypto.subtle` yo'q — zaxira SHA-256 to'g'ri ishladi |
| C. Sahifa himoyasi | 3 betlik shablon + 2 betlik kontrakt → 8 slot (p1–p2), p3 slot yo'q, hammasida `rule_y` float |
| D. Ekranda imzo | Avto-to'ldirish 4 imzo (bulk 200), 4 ta `IMG`, «preview unavailable» yo'q, 4 × image endpoint 200 |
| E. Joylashuv paneli | contractor pastki qirra 491.403 (kutilgan 491.402), rep 641.902 (kutilgan 641.902); hajm 120% (34 → 40.8 pt), Courier 13; reload'dan keyin saqlangan |
| F. Finalize | 200 `completed`; `layout` = `{sig +1, date 0, p1_company_rep_signature −0.5, scale 1.2, Courier, 13}` — panel va `contract_finalized` audit bilan bir xil |
| G. Yakuniy PDF | ±0.5 pt ichida (I.2); **imzo ostidagi chiziq piksellari 100% saqlangan** (7110/7110, 5856/5856) — oq to'rtburchak yo'q; `/verify` `intact: true` |
| H. Ekran ↔ PDF | imzolar 0.03–0.15 pt, sanalar 0.00 pt |
| I. O'chirish | Kontrakt DELETE 200, `contracts/`/`signatures/`/`final/` yo'q, `contract_deleted`; shablon 409 `contract_count: 1` + «Archive instead» → kontrakt o'chirilgach purge 200 + `template_deleted` |
| J. Contractor ruxsatlari | Kontrakt DELETE 403, `?purge=true` 403, arxivlash 403 |

#### I.2 G o'lchov jadvali

8× render, 1 px = 0.125 pt; «All signatures ▲×2» hammaga +1.0 qo'shadi.

| Slot | Offset | Kutilgan pt | Haqiqiy pt | DOM↔PDF |
|---|---|---|---|---|
| `p1_contractor_signature` | +1.0 | 2.00 | 2.15 | 0.15 |
| `p1_company_rep_signature` | +0.5 | 1.50 | 1.53 | 0.03 |
| `p2_contractor_signature` | +1.0 | 2.00 | 2.15 | 0.15 |
| `p2_company_rep_signature` | +1.0 | 2.00 | 2.03 | 0.03 |
| `p1_date`, `p1_date_2`, `p2_date`, `p2_date_2` | 0 | 1.00 | 1.000 (Courier 13.0) | 0.00 |

#### I.3 Haqiqiy `128b724c` (faqat o'qish)

- `signature_slots` **47 → 43** (p32/p34 olib tashlandi).
- `missing_required` = [`p3_vehicle_assignment`], `can_sign=false`.
- Slotlarda `rule_y=null` 0 ta. `p1_cdl_expiration`/`p14_cdl_expiration`
  sxemada `null`, lekin kontraktda to'ldirilgan, shuning uchun pending
  ro'yxatiga kirmaydi — xato emas.
- Brauzerda faqat ko'rildi, POST/PUT/DELETE 0.

#### I.4 Dublikatlar o'chirildi (foydalanuvchi ruxsati bilan)

- Zaxira: `backend/local.db.bak-20260914-dupdelete` (online backup API,
  integrity ok).
- `81677830-58da-4e90-a6b7-a65e8327434c` — analyzed, 0 imzo, `c190c353` bilan
  bir xil hash.
- `4120ac6f-9ca7-45a8-9e51-e469c7420347` — reviewing/rejected, 0 imzo,
  `02fc4235` dagi 10 kontrakt bilan bir xil hash.
- `DELETE /api/contracts/{id}` ×2 → 200; juftlarning 11 tasi joyida;
  `contract_deleted` ×2; storage kataloglari yo'q; `128b724c` compare
  o'zgarmagan. Kontraktlar soni **12**.

#### I.5 Integratsiya kuzatuvlari va qarorlar

- **Yo'q sahifadagi majburiy MATN maydonlari** `missing_required` da qoladi.
  Reproduksiya: 3 betlik shablon + 2 betlik kontrakt → `missing_required` =
  [`p3_full_name`, `p3_email`], `can_sign=false`. **Foydalanuvchi qarori
  (2026-09-15): BLOKLASHDA QOLADI** — qisqa kontrakt noto'g'ri shablon
  belgisi (`81677830` aynan shunday edi); override bilan o'tkaziladi; kod
  o'zgarmaydi.
- Siyoh chekkasida och-kulrang **halo** — I.6 da tuzatildi.
- Playwright brauzerida oldingi sessiyadan qolgan route-mock'lar login'ni
  to'sgan — `unrouteAll` bilan tozalandi (muhit masalasi, kod nuqsoni emas).

#### I.6 Halo tuzatildi (appsec, 2026-09-15)

- **Topilma:** saqlangan PNG'da 3261 yarim-shaffof piksel, RGB yorqinligi
  122–183 (median 153). Yumshoq alfa rampasi qog'oz aralash och rangni
  saqlagan → yakuniy PDF'da imzo ostidagi qora matn 61–112 gacha oqargan (har
  slotda 223–262 px, 8× render).
- **Rad etilgan yechim:** aralashma tenglamasi `ink = (P − (1−a)·paper)/a`
  o'lchab ko'rildi va RAD ETILDI (telefon holatida qora fon ustida max 140,
  fringe siyohdan medianda 56 farq) — alfa rampasi haqiqiy qoplanishni
  bildirmaydi.
- **Amalga oshirilgan:** `_defringe` (`app/services/signature_image.py:144`,
  `remove_background` ichida `:139` da chaqiriladi):
  - siyohning har kanal bo'yicha qaytarish koeffitsienti = noshaffof
    piksellarning eng to'q yarmi mediani ÷ qog'oz;
  - × mahalliy qog'oz bahosi → joyiga mos siyoh rangi;
  - `RGB = min(P, siyoh rangi)`; alfa o'zgarmaydi; rangli siyoh saqlanadi;
  - ish alfa bbox'i ichida bajariladi;
  - ≥2% shaffof pad imzolariga tegilmaydi (bayt-bir xil).
- Spot-check eslatmasi: D bo'limidagi `prepare_signature_image` `:71` va
  `remove_background` `:88` qator raqamlari endi `:82` va `:99` (`_defringe`
  qo'shilgani sabab siljigan; D bo'limi o'zgartirilmadi).
- O'lchov (3 px halqa; «qora ustida» max/o'rtacha; «siyohdan ortiq» max;
  yarim-shaffof luma mediani):

| Holat | Qora ustida | Siyohdan ortiq | Luma mediani |
|---|---|---|---|
| (i) telefon surati | 140/52.6 → **36/30.6** | +105 → **+1** | 152 → **35** |
| (iii) kremrang | 145/56.2 → **36/31.6** | +110 → **+0** | 168 → **36** |
| (vi) och-ko'k siyoh | 182/117.7 → **112/101.6** (siyohning o'zi ~112) | +69 → **0** | 191 → **112** |
| (viii) shaffof pad | o'zgarmadi (bayt-bir xil) | +0 | 20 |

- (vi) da hue saqlangan (medianda 3° farq). Mavjud sifat o'zgarmagan: fon
  0.00–0.01%, recall 100%, precision ≥99.9%, pastki qirra +0.
- Tezlik 4000×4000: `prepare_signature_image` 760 → 848 ms (+12%).
- Testlar +4: `tests/test_signature_image.py` **15 → 19** holat (J-10 hal
  bo'ldi; 19 `--collect-only` bilan tasdiqlangan). pytest **341 passed**.
- Restart: 7964/12316 → launcher **35884** → xizmat **17676**.
- Jonli tekshiruv (faqat seed admin, yangi foydalanuvchi yaratilmagan,
  `ZZ-HALO-…`): 8× render, imzo hududida 126 670 qora-matn pikseli —
  oqarish **max 36**, 41–60: 0, >60: 0 (OLDIN 61–112). Sintetiklar DELETE 200
  bilan tozalandi.

#### I.7 Xavfsizlik remediatsiyasi (sinov qoldiqlari)

- **Faol admin hisobi qolgan edi:** integratsiya `admin` rolli
  `zz-int-admin@example.com` va `zz-int-contractor@example.com` hisoblarini
  faol qoldirgan, parollar `%TEMP%\swm_int\state.json` da ochiq turgan.
  O'chirishdan oldin ikkalasi bilan login **200** — teshik haqiqiy edi.
  - Zaxira `backend/local.db.bak-20260915-zzintusers` (online backup,
    integrity ok).
  - Bog'liqlik: RESTRICT 0; `audit_logs.actor_id` 19 ta qatorda → NULL (audit
    1326 → 1326, `actor_email`/`actor_role` saqlangan); ikkala `users` qatori
    o'chirildi.
  - Tasdiq: login **401/401**, seed admin 200, `users` 3 → **1** (faqat seed
    admin), 12 kontrakt joyida. `state.json` va bo'sh
    `storage/user-signatures/be8c2952-…` o'chirildi.
- **Seed admin paroli ochiq holda** `%TEMP%\swm_repro\count_probe.py` va
  `live_parallel.py` da topildi → o'chirildi. Yana o'chirilgan: `swm_bulk\state.json`,
  `swm_e2e\state.json`, `swm_override_e2e\token.txt`, `swm_sig\token.txt`,
  `swm_sig\reg.json`, `swm_bulk2\fixture.json`,
  `swm_e2e\ev_60_register_proof_user.json` (muddati o'tgan tokenlar /
  o'chirilgan hisoblar paroli). Qayta skan: `swm_repro` da `password` qolgan
  fayl 0.
- **PII artefaktlari** (foydalanuvchi ruxsati bilan):
  `c:\PTG\.playwright-mcp\page-2026-09-12T16-59-23-652Z.yml`,
  `page-2026-09-12T15-13-32-502Z.yml` (haqiqiy kontrakt matni),
  `swm_ovr\09-real-contract-readonly.png`,
  `swm_ovr\09b-real-contract-decision-panel.png` o'chirildi. Qolgan `page-*.yml`
  lar (agent hisobotiga ko'ra 137 ta; yozuv paytida katalogda **136** ta —
  farq sababi haqida ma'lumot yetarli emas) haqiqiy kontrakt belgilari
  (Envelope ID, Premier Trucking, Docusign, FCRA, CDL) bo'yicha skanerlandi →
  **0** (belgi skani — to'liq kafolat emas).
- **Tavsiya (bajarilmagan, foydalanuvchi qarori):** parol faqat shu
  mashinaning `%TEMP%` iga yozilgan va o'chirilgan, lekin gigiyena uchun seed
  admin parolini almashtirish (`.env` dagi `SEED_ADMIN_PASSWORD` bilan birga).
- **Saboq:** sinov agentlari faol hisob, parol/token fayllari va Playwright
  snapshot'larida PII qoldiradi. E2E topshirig'ida: faqat seed admin bilan
  ishlash yoki yaratilgan hisobni o'chirish; kredensialni faylga yozmaslik;
  haqiqiy sahifa snapshot/skrinshotini o'chirish; yakunda kredensial/PII
  skanini talab qilish.

### J. Ochiq / keyingi bosqich

1. **Kutubxona imzolari Docusign shtampi bilan** — foydalanuvchi faqat
   imzoning o'zini qayta yuklashi kerak (D bo'limi).
2. `app/config.py:8` `env_file=".env"` CWD'ga nisbiy — hamon tuzatilmagan,
   foydalanuvchi qarori kutilmoqda (2026-09-12 ikkinchi qism, A bo'limi).
3. PLAUSIBLE: `storage.py` `put_bytes` dagi `.partial` fayl nomi kalitdan
   olinadi — parallel finalize'da Windows sharing violation berishi mumkin
   (2026-09-12 ikkinchi qism, F-2).
4. «Replace all» atomik emas (avval DELETE, keyin bulk).
5. Yo'q sahifadagi majburiy **matn** maydonlari `missing_required` da qoladi
   (C bo'limi; `can_sign` xulqi avvalgidek).
6. `parsing` holatida qotib qolgan kontraktni o'chirib bo'lmaydi (409).
   COMMIT'dan keyingi fayl o'chirish xatosi yetim fayl qoldirishi mumkin.
7. 2 ta sana slotida chiziq topilmaydi (`p1_cdl_expiration`,
   `p14_cdl_expiration`) — bbox noto'g'ri, detektorni qayta ishlatish kerak.
8. `list_contracts` ↔ `assert_can_view_contract` nomuvofiqligi (F bo'limi).
9. Haqiqiy telefon suratlarida fon olib tashlash — PLAUSIBLE (faqat sintetik
   rasmlarda tasdiqlangan).
10. ~~`tests/test_signature_image.py` holatlar soni: hisobotda 16, statik
    sanashda 15 — keyingi pytest yugurishida aniqlashtirilsin.~~ **HAL
    BO'LDI:** `--collect-only` bo'yicha 15 edi, halo testlari bilan 19 (I.6).
11. Seed admin parolini almashtirish tavsiya etiladi (I.7) — foydalanuvchi
    qarori kutilmoqda.

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `comparison_engine.py` dagi `can_sign` ifodasi, `tests/conftest.py`.
pytest **341 passed** (I.6, agent hisobotiga ko'ra; `--collect-only` jami 341 —
yozuv paytida tekshirildi). Backend: launcher PID **35884** → xizmat
ko'rsatuvchi **PID 17676**; frontend :5173 PID **10144** (uchala jarayon yozuv
paytida ishlayapti). Zaxiralar: `backend/storage_backup_20260914/`,
`backend/local.db.bak-20260914-dupdelete` (I.4),
`backend/local.db.bak-20260915-zzintusers` (I.7) — hammasi mavjud. Jonli E2E
natijasi — I bo'limida.

---

## 2026-09-17 — Soxta «bo'sh» imzo/sana slotlari (shablondan ayirish bekor qilindi), rasmli katakchalar, qutilarni sichqoncha bilan surish, ogohlantirishlar (appsec + frontend)

Foydalanuvchi allaqachon imzolangan va sanalangan joylar «imzo/sana kerak» deb
belgilanayotganini ko'rsatdi. Ildiz sabab 2026-09-10 dagi shablon-diff qarori
bo'lib chiqdi: «shablonda bor narsa kontraktniki emas» degan taxmin noto'g'ri
edi. Ayirish olib tashlandi, geometrik himoyalar (A, B) saqlandi. Shuningdek
rasm ko'rinishidagi katakcha belgilari tanildi, slot qutilari chiziqqa
tushirildi va sichqoncha bilan surish qo'shildi. Oldindan to'ldirilgan shablon
va «kontrakt = shablon fayli» holatlari uchun ogohlantirishlar chiqarildi.
Bu jarayonda hujjatning o'zida haqiqiy nomuvofiqlik topildi (`exp_date_conflict`).
Izoh: `fayl:qator` havolalari yozuv paytida joriy kod bilan spot-check
qilingan; siljigan raqamlar joriy qiymatga tuzatilgan (H bo'limida ro'yxat).
«Eski» qator raqamlari, DB holati, o'lchov va mock natijalari **agent
hisobotlariga ko'ra** yozilgan (reporting ularni qayta o'lchamagan).

### A. Shikoyat va qarorlar

Foydalanuvchi 3 ta skrinshot yubordi:

| # | Shikoyat |
|---|---|
| 1 | Allaqachon imzolangan (Docusign «Signed by:» shtamplari) va sanalangan (08/22/2026) joylar, shuningdek to'ldirilgan Exp va Date of Birth «imzo/sana kerak» deb belgilanyapti — «bor ma'lumot ustidan yozmoqchi, detect qilishda skip qilsin» |
| 2 | Belgilangan bloklarni **sichqoncha bilan surish** imkoni kerak |
| 3 | Decision paneli: «Approval is blocked… Override is not available: FMCSA violation / cross-field inconsistency» |

Qarorlar (AskUserQuestion):

| Mavzu | Qaror |
|---|---|
| Surish doirasi | **Faqat shu kontrakt** (shablonga yozilmaydi) |
| Skip siyosati | **«Avtomatik skip + ogohlantirish»**. Rad etilgan variantlar: bitta tugma bilan tasdiqlash; «toza shablon yuklayman» |

### B. Diagnostika (appsec, faqat o'qish) — CONFIRMED

Bu bo'limdagi DB holati, o'xshashlik, hash va slot sonlari agent hisobotiga
ko'ra yozilgan.

- **Baza holati:** audit bo'yicha 2026-09-16 13:12–13:13 da 14 kontrakt va 4
  shablon (jumladan `befea049`) foydalanuvchi tomonidan o'chirish tugmasi
  bilan o'chirilgan. 2026-09-17 holati: 1 shablon `a5990eaa` «Company
  Contract - RMR.pdf» (44 bet), 3 kontrakt.
- **Skrinshotdagi kontrakt `d62c63de` (44 bet) = shablon `a5990eaa` ning
  O'ZI.** 44/44 sahifada matn o'xshashligi 1.00, rasm hash'lari va stroke
  soni bir xil, Envelope ID mos. Faqat PDF metama'lumoti
  (Creator/Producer/Title/sanalar) farq qiladi.
- `40a1a2d7` va `c1b6de5d` — 49 bet, o'zaro fayl hash'i bir xil. Shablon
  bilan: 1–34-bet 0.98–1.00, 35–44-bet 0.10–0.22.
- Shablon tahlili audit'ida `prefilled_signatures: 42` (42 dan) va
  `prefilled_dates: 50` (51 dan) bor edi. Frontend'da `prefilled` so'zi 0
  marta uchragan — foydalanuvchi bu ogohlantirishni hech qachon ko'rmagan.
- **Soxta «bo'sh» slotlar:** `d62c63de` da 93 slotdan 92 tasi.
  - 42 imzo — `has_ink` (`pdf_parser.py`, eski `:335`) shablon hash'i bilan
    bir xil rasmni tashlab yuborardi (9 kompaniya vakili, 31 kontraktor, 2
    initsial).
  - 50 sana — `_without_baseline` (eski `:250`) shablondagi bilan bir xil
    tokenni o'chirardi (`confidence 1.0`, `value None`; `p1_cdl_expiration`
    va `p1_date_of_birth` ham shu jumladan).
  - Faqat `p1_effective_date` haqiqatan bo'sh.
  - `40a1a2d7`: 25 imzo + 3 sana soxta bo'sh, 51 haqiqatan bo'sh.
- **Ildiz — 2026-09-10 qarori haddan oshgan:** «shablonda bor = kontraktniki
  emas» taxmini noto'g'ri. O'sha kuni ham `c190c353` shablon bilan bayt-bayt
  bir xil edi va uning imzolari «shablondan meros» deb xato talqin qilingan.
  Oqib kirish muammosini geometrik himoyalar allaqachon hal qiladi: A —
  chiziq ustidagi 8 pt polosa + `:` yorliq filtri; B — rasm qoplami ≥0.05 va
  joylashuvi.
- **Bloklovchilar (`d62c63de`):**

| Bloklovchi | Hukm | Izoh |
|---|---|---|
| `cdl_mismatch` p1_cdl ↔ p14_cdl | **HAQIQIY** | 9 belgidan 1 tasi farq qiladi, PDF'da shunday bosilgan → override'ni yopadi |
| `p1_w_9_status`, `p2_contract_type`, `p2_are_you_eligible…dot…` | **SOXTA** | katakcha `☐` glifi ustiga ~8×8 pt X **rasm** qo'yilgan; `is_checked` (eski `:428`) faqat belgilar/chiziqlarni ko'rardi |
| `p3_vehicle_assignment` | **HAQIQIY** bo'sh | — |
| FMCSA buzilishi | 0 ta | paneldagi «FMCSA violation» matni `ReviewPage.tsx` da qotirilgan edi (eski `:224`) |

- **Qutilar joylashuvi:** imzo bbox `field_detector.py:335`
  `top = run.y − height` (`SIGNATURE_HEIGHT = 34.0`, `:28`). `run.y` — `_`
  glifining TEPASI (651.1), chiziq siyohi esa `rule_y=662.5`. Natijada quti
  chiziqdan **11.4 pt yuqorida** turadi va yuqoridagi matn qatoriga 4.7 pt
  kiradi. `FieldHighlighter.tsx` (eski `:13`) va `SignaturePage.tsx` (eski
  `:943`/`:978`) xom bbox chizardi, joylashtirilgan imzo esa
  `signatureRect(…rule_y…)` bilan chiziqqa tushardi. Gorizontal surish yo'q
  edi (eski `lib/layout.ts:48`).

### C. Backend — gorizontal slot offseti (appsec)

- `app/schemas/contract.py`:
  - `LAYOUT_OFFSET_LIMIT_PT = 20.0` (`:255`) o'zgarmagan;
    `SLOT_OFFSET_LIMIT_PT = 200.0` (`:258`).
  - YANGI `slot_offsets_x_pt` (`:279`; musbat = o'ngga).
  - Validator ikkala map uchun (`:284-297`): har map'da ≤500 kalit
    (`MAX_LAYOUT_SLOT_OFFSETS` `:259`), finite, [-200, 200].
- `app/services/pdf_builder.py`:
  - `Layout.slot_offsets_x_pt` (`:88`), `slot_offset_x` (`:105-109`; erkin
    joylashtirishda 0).
  - Imzo chap qirrasi `bbox.x + dx` (`:275`, ishlatilishi `:284`, `:300`);
    sana x `bbox.x + dx` (`:324-325`).
- `app/api/signatures.py:704-723` `_assert_layout_slots`: ikkala map'ning
  noma'lum kalitlari bitta saralangan `details.unknown_slot_offsets`
  ro'yxatida (birinchi 50 tasi, `:722`).
- Audit `details.layout` yangi map'ni avtomatik oladi.
- **Backend dx ni sahifa chetiga clamp qilmaydi** (faqat ±200). Sahifa
  chegarasini frontend ushlab turadi (D bo'limi).
- Testlar: `tests/test_pdf_builder_layout.py`, `tests/test_api_layout_pages.py`.
  Bitta mavjud test kirishi moslandi (±20.5 → ±200.01). pytest → **376**
  (agent hisobotiga ko'ra).

### D. Frontend — surish, qutilar, bloklovchi matn, ogohlantirishlar (general-purpose; `tsc` EXIT 0, `build` OK)

- `src/lib/layout.ts`:
  - `slot_offsets_x_pt` (`:55`), `SLOT_OFFSET_LIMIT = 200` (`:20`).
  - Sanitizatsiya `sanitizeSlotMap` (`:93`, qo'llanishi `:117-119`) — x-siz
    eski layout xatosiz yuklanadi.
  - `layoutForRequest` (`:171`): finalize'ga faqat joriy, nol bo'lmagan
    slotlar ketadi; bo'sh bo'lsa kalit umuman yuborilmaydi.
  - `withoutSlotOffsets` (`:187`); **`slotBoxRect` (`:267`) — quti pastki
    qirrasi chiziqda**; `slotOffsetBounds` (`:309`); `fitLayoutToPages`
    (`:350`); `fitLayoutToPagesReport` (`:359`).
- YANGI `src/hooks/useSlotDrag.ts` (`useSlotDrag` `:71`,
  `DRAG_THRESHOLD_PX = 3` `:14`): sichqoncha/sensor bilan surish, px→pt,
  0.5 pt snap, dy ishorasi (musbat = yuqoriga), ±200 va sahifa chegarasi
  clamp, <3 px — oddiy bosish, jonli preview, qo'yib yuborganda saqlash,
  double-click → slot nolga.
- `src/hooks/usePdfViewer.ts` — barcha sahifa o'lchamlari oldindan olinadi
  (`pageSizes` `:24`, `:64-74`), ekranda bo'lmagan sahifalar uchun ham clamp.
- `src/components/FieldHighlighter.tsx:53` — imzo/sana slot qutilari
  `slotBoxRect` bilan chiziqda va suriladi; boshqa maydon qutilari xom
  bbox'da.
- `src/pages/SignaturePage.tsx`:
  - slot qutilari `slotBoxRect` (`:999`, `:1044`); joylashtirilgan imzo
    rasmlari va sana matni ham suriladi;
  - global offset/hajm slotni sahifadan chiqarsa, slot qaytariladi va qisqa
    xabar chiqadi: «1 slot was moved to stay on the page» (`:264-278`; 5 s
    yoki keyingi o'zgarishda yo'qoladi);
  - finalize oldidan qayta tekshiruv `fitLayoutToPages` (`:868`);
  - 422 `unknown_slot_offsets` ikkala map'ni tozalaydi (`:884-891`,
    `withoutSlotOffsets`);
  - «Same file as template?» badge (hisob `:484`, badge `:1177-1183`).
- `src/pages/ReviewPage.tsx`:
  - review va sign **bir xil `useContractLayout`** dan foydalanadi (`:64`);
  - qotirilgan matn o'rniga haqiqiy bloklovchilar: `describeOverrideBlockers`
    (`:28`), FMCSA uchun o'qiladigan `issue` matni (`:35`), inconsistency
    (`:41`), panelda ro'yxat (`:280-287`);
  - «This contract appears to be the template file itself (page similarity
    N%)» banneri (hisob `:149`, matn `:202`) — bloklamaydi.
- `src/components/LayoutPanel.tsx` — har slotda ◀▶ + ▲▼, x/y qiymatlari,
  sahifa chetida to'xtaydi (`:235`, `:244`); Reset ikkala map'ni tozalaydi.
- YANGI `src/lib/warnings.ts` — himoyalangan o'qish (`readPrefilledWarning`
  `:35`, `readIdenticalTemplate` `:41`).
- `src/types/template.ts:14`, `src/types/comparison.ts:78`.
- `src/pages/TemplateManager.tsx:185-195` — «Pre-filled template» belgisi +
  «Already contains 42/42 signatures and 50/51 dates…».
  `src/styles/index.css:114-115`.
- **Mock tekshiruv** (agent hisobotiga ko'ra; Playwright MCP band bo'lgani
  uchun alohida headless Chromium, `http://10.31.0.58:5173`, barcha
  `/api/*` mock):
  - surish **28/28**: quti pastki qirrasi 678.59 pt (chiziq 678.6, xom
    bbox'dan 11.41 pt pastda); `rule_y=null` → bbox pastki qirrasi;
  - `dx=+20, dy=−10` saqlandi;
  - sahifa chetlari: chap dx=−72, past dy=−113, sana o'ng dx=142, +200
    chegarasi;
  - sign sahifasida rasm dx 30 / dy −5, sana dx −30 / dy +8; finalize tanasi
    aynan saqlangandek;
  - «Cross-field inconsistency: CDL numbers differ between pages (pages 1,
    14)»;
  - ogohlantirishlar **14/14**.

  Skrinshotlar: `c:\PTG\.playwright-mcp\swm_drag\`, `swm_warn\` (ikkala
  katalog mavjud).
- **Cheklovlar:**
  - layout brauzer `localStorage` ida — boshqa kompyuterda ko'rinmaydi;
  - sahifa chetini baholash taxminiy (juda uzun sana matni biroz chiqishi
    mumkin);
  - erkin joylashtirishlar surilmaydi;
  - sensor faqat sichqoncha bilan sinalgan.

### E. Backend — aniqlash tuzatishi (appsec; pytest **400 passed**)

- `app/services/pdf_parser.py`:
  - **Olib tashlandi:** `has_ink` dagi rasm hash'ini tashlash va stroke
    ayirish (`has_ink` `:410-441` endi baseline'ga qaramaydi);
    `_without_baseline` funksiyasi o'chirilgan (faylda 0 uchrashuv).
  - **Saqlandi:** B — rasm qoplami ≥0.05 VA markaz pastki yarmda yoki ≥60%
    ichida (`:418-430`; konstantalar `:48-49`); A — chiziq polosasi + yorliq
    filtri (`:435-440`; `SIGNATURE_TEXT_BAND = 8.0` `:53`);
    `SIGNATURE_MIN_STROKES = 4` (`:41`, `:432`).
  - `template_baseline` hamon quriladi, lekin hech narsa ayirmaydi
    (docstring «informational only» `:213-217`; `prefilled` o'qilishi
    `:539-548`).
- **Sana qoidasi** (`date_in` `:443-459`, `date_starting_in` `:461-480`,
  yordamchilar `:630-768`) — slot faqat sanaga o'xshash qiymat bilan
  to'ldirilgan hisoblanadi:

| Qabul qilinadi | Qabul qilinmaydi |
|---|---|
| raqamli: `01/30/2026`, `1-5-26`, `01//30//2026`, `9 / 9 / 2026`, `2026-01-30` | `//` |
| oy nomi + kun: `Aug 22, 2026`, `22nd August, 2026` | `sideration.`, `Date:`, `PART` |
| bo'shliqli: `01 30 2026` (faqat haqiqiy kalendar sana) | yakka son |
| oy/yil: `08/2029` | `Aug 2026`, `May`, `13/2029` |
| ixcham 8/6 raqam (faqat biror o'qilishi haqiqiy sana bo'lsa) | `D12345678`, `Mayor 12, 2026` |

  **Tor sana qutilari** (27–60 pt, 6 slot): sana qator davomidan o'qiladi —
  sana boshi quti ichida bo'lishi va faqat qutining pastki yarmi hisobga
  olinadi (`:464-479`).
- **Rasmli katakcha** — `is_checked` (`:560-573`): rasm markazi option
  ichida, o'lchami har o'qda ≤1.5× (`CHECKBOX_IMAGE_MAX_SCALE` `:64`).
- **`prefilled_summary`** (`:256-282`, o'qish vaqtida hisoblanadi):
  `app/schemas/template.py:47-59`, `:73-75`; `app/api/templates.py:91`,
  `:100-109`. `warning` — biror tur >50% bo'lsa (qat'iy `>`,
  `PREFILLED_WARNING_RATIO = 0.5` `:60`; aniq 50% → false).
- **`template_match`** (`:286-327`, yordamchilar `:771-805`): sahifalar soni
  teng + eng past sahifa matn o'xshashligi (token Dice) **≥0.995** + imzo
  slotlarining ≥90% da rasm hash'i bir xil (`:71-72`).
  - **0.995 sababi** (agent hisobotiga ko'ra): bir xil fayl 44/44 sahifada
    1.000; haqiqiy to'ldirilgan kontrakt maydonli sahifalarda 0.954 va 0.979
    — 0.98 chegara soxta moslikdan 0.001 masofada bo'lardi.
  - `app/workers/tasks.py`: `_template_match` `:93-100` (xato bo'lsa `None`,
    parse'ga ta'sir yo'q), chaqiruv `:158`, saqlash `:168-170`, audit
    `template_identical` `:197`. `app/schemas/comparison.py:60-68`,
    `:80-81`.
  - Har parse'ga ~4–5 s qo'shiladi (44 bet, fon vazifa; agent hisobotiga
    ko'ra).
- **O'zgartirilgan eski testlar** (`tests/test_template_baseline.py`):

| Oldin | Keyin | Qator |
|---|---|---|
| `test_the_templates_own_signature_image_is_not_the_contractors` (False) | `test_a_signature_image_the_template_also_shows_is_still_signed` (True) | `:118-132` |
| `test_a_date_preprinted_on_the_template_is_not_filled_by_the_contractor` (None) | `test_a_date_the_template_also_shows_is_still_filled` (sana) | `:143-151` |

  Docstring va izoh (`:220-222`) A himoyasini nomlaydi. A/B himoya testlari
  o'zgarmagan holda yashil. YANGI `tests/test_visible_detection.py` — agent
  hisobotida 19 funksiya (yozuv paytidagi holat H bo'limida).
- **Quruq ishga tushirish** (nusxadan, PII'siz; agent hisobotiga ko'ra):

| | `d62c63de` oldin | `d62c63de` keyin | `40a1a2d7` oldin | `40a1a2d7` keyin |
|---|---|---|---|---|
| pending slotlar | 93 (42+51) | **1** (`p1_effective_date`) | 79 (38+41) | **58** (13+45) |
| `missing_required` | W-9, contract_type, dot_eligible, vehicle_assignment | **faqat `p3_vehicle_assignment`** | W-9, contract_type, vehicle, p35_state | o'zgarmagan |
| `inconsistencies` | `cdl_mismatch` | `cdl_mismatch`, **`exp_date_conflict`** | `cdl_mismatch` | `cdl_mismatch`, **`exp_date_conflict`** |
| `can_sign` | False | False | False | False |
| `template_match` | – | **identical true** (1.0/1.0) | – | **false** (0.0/0.571) |

  - Shablon `prefilled_summary`: imzo 42/42, sana 50/51, `warning: true`.
  - `40a1a2d7` da 58 (51 emas): 7 sana sloti (`p35_date_of_birth`,
    `p35_date`, `p37_date`, `p38_date_of_birth`, `p38_date`, `p39_date`,
    `p44_date`) avval faqat oqib kirgan nasr bilan soxta «to'ldirilgan» edi.
  - `40a1a2d7` da W-9 va contract_type option'larida rasm yo'q — haqiqatan
    bo'sh.
  - `p42_class_4` (`d62c63de`) endi rasmli belgi bilan checked (render
    tasdiqlagan).
- **Yashirin haqiqiy xato ochildi — `exp_date_conflict`:** CDL amal qilish
  muddati 1 va 2-betda farq qiladi: `d62c63de` da **1 kun**, `40a1a2d7` da
  **111 kun** (render bilan tasdiqlangan). Ilgari token ayirish uni
  yashirgan.

### F. Yakuniy integratsiya

Hozircha rejalashtirilgan: `POST /compare` da `template_match` saqlanishi
tuzatishi, pytest, backend restart, 3 haqiqiy kontraktni zaxira bilan reparse
(`local.db.bak-20260917-detect`), `d62c63de` ni faqat o'qish rejimida
brauzerda tekshirish va sintetik surish→finalize→PDF o'lchovi. Natija —
quyidagi F.1–F.5 kichik bo'limlarida.

Yozuv paytidagi holat: `backend/local.db.bak-20260917-detect` hali **yo'q**;
`app/api/contracts.py` 02:34 da o'zgargan va
`tests/test_visible_detection.py:408`
`test_rerunning_compare_keeps_the_stored_template_match` mavjud — bu
integratsiya ishi bo'lishi mumkin (tasdiqlanmagan).

#### F.1 Integratsiya — PASS

2026-09-17, `http://10.31.0.58:5173`, headless Chromium (Playwright MCP band
edi). Yuqoridagi «tasdiqlanmagan» holat shu bilan tasdiqlandi.

- **Kod tuzatishi (restartdan oldin):** `app/api/contracts.py`
  `rerun_comparison` (`:227`, `POST /api/contracts/{id}/compare`) — saqlangan
  `comparison_result` da `template_match` bo'lsa, yangi natijaga ko'chiriladi
  (`:241-245`). Ilgari keyingi reparse'gacha `null` bo'lib qolardi.
  Test: `tests/test_visible_detection.py:408`
  `test_rerunning_compare_keeps_the_stored_template_match` (reporting
  spot-check qildi: `:408` va `contracts.py:241-245` joriy kodda mos).
- pytest **401 passed** (168 s); `tsc` EXIT 0; `build` OK.
- Zaxira `backend/local.db.bak-20260917-detect` (online backup,
  `integrity_check` ok; 1 foydalanuvchi, 3 kontrakt, 1 shablon).
- Backend restart: PID 24584 (launcher 14952) → **12992** (launcher 17292),
  `/health` 200; frontend :5173 PID 4660 tegilmagan.

#### F.2 Haqiqiy kontraktlarni reparse (OLDIN / KEYIN)

Uchala so'rov 202 → `analyzed` (17–19 s):

| Kontrakt | | signature_slots | missing_required | inconsistencies | can_sign | template_match.identical | review_status |
|---|---|---|---|---|---|---|---|
| `d62c63de` | OLDIN | 93 (42+51) | `w_9_status`, `p2_are_you_eligible…`, `p2_contract_type`, `p3_vehicle_assignment` | `cdl_mismatch` | false | null | pending |
| | KEYIN | **1** (`p1_effective_date`) | faqat `p3_vehicle_assignment` | `cdl_mismatch`, `exp_date_conflict` | false | **true** | pending |
| `40a1a2d7` | OLDIN | 79 | `p1_w_9_status`, `p2_contract_type`, `p35_state`, `p3_vehicle_assignment` | `cdl_mismatch` | false | null | pending |
| | KEYIN | **58** (13+45) | o'sha 4 ta | `cdl_mismatch`, `exp_date_conflict` | false | false (0.0 / 0.5714) | pending |
| `c1b6de5d` | OLDIN / KEYIN | `40a1a2d7` bilan bir xil | bir xil | bir xil | false | false | pending |

- Barcha natijalar quruq ishga tushirish (E bo'limi) bilan aynan mos.
  `review_status` o'zgarmagan.
- Shablon `a5990eaa` `prefilled_summary`: imzo 42/42, sana 50/51,
  `warning: true`.
- `POST /compare` (`d62c63de`) → 200; `template_match` `identical: true`,
  `page_similarity 1.0`, `image_match_ratio 1.0` saqlangan (POST tanasida
  ham, keyingi GET'da ham).
- Kuzatuv: `40a1a2d7`/`c1b6de5d` da `page_similarity 0.0` — sahifa soni farq
  qilgani uchun (xato emas).

#### F.3 Haqiqiy kontrakt — faqat ko'rish (DOM, skrinshot olinmagan)

- `d62c63de` review sahifasida «template file itself» banneri bor.
- Decision panelida 2 qator — CDL «(pages 1, 14)» va CDL expiry «(pages 1,
  2)»; FMCSA qatori 0.
- «Approve for signing» yopiq, override tugmasi yo'q (inconsistency bor —
  dizayn bo'yicha).
- 1-sahifada faqat `p1_effective_date` sloti, W-9 elementi 0.
- Slot qutisi pastki qirrasi DOM'da 510.85 pt vs `rule_y` 510.861
  (**Δ −0.01**); chap qirra 190.04 = bbox.x.
- Templates sahifasida «Pre-filled template» belgisi bor.
- So'rovlar jurnali: GET'dan boshqa yagona so'rov `POST auth/login`.

#### F.4 Sintetik surish → finalize → PDF (faqat seed admin, `ZZ-DRAG-…`)

- Slot geometriyasi: imzo bbox.x 200, sana bbox.x 440, `rule_y` 593.402.
- Brauzerda surish → `localStorage`: imzo dx 20 / dy 10, sana dx −15.
- Finalize **200**; so'rovdagi `layout` saqlangan qiymatlar bilan aynan bir
  xil.

| Yakuniy PDF o'lchovi | Kutilgan | Haqiqiy | Δ |
|---|---|---|---|
| Imzo chap qirrasi | 220.000 (200 + 20) | 220.000 | **0.000** |
| Imzo pastki qirrasi | 582.402 (`rule_y − 1 − 10`) | 582.402 (render'da siyoh pasti 582.125) | **0.000** |
| Sana x | 425.000 (440 − 15) | 425.000 | **0.000** |
| Sana baseline | `rule_y − 1` | 592.402 | — |

- Ekrandagi preview PDF bilan mos (220.00 / 582.40); `/verify`
  `intact: true`.
- **Nomuvofiqlik (test skripti xatosi, ilova xatosi EMAS):** birinchi
  sintetik kontrakt nol offsetlar bilan yakunlangan — slot 1100 px
  viewport'dan pastda edi, sichqoncha surishi ekrandan tashqariga tushgan.
  Slotni ko'rinishga aylantirib va finalize oldidan offset borligini
  tekshiruvchi guard bilan qayta bajarilgan.

#### F.5 Tozalash va gigiyena

- Sintetik kontraktlar `e682df1f…`, `f171f93e…` DELETE 200; shablon
  `e045e8ab…` purge 200; bazada ZZ-DRAG qatorlari 0.
- Qolgan: 3 kontrakt + 1 shablon; foydalanuvchilar soni 1 (seed admin);
  kutubxona imzolari 2 (o'zgarmagan).
- `%TEMP%\swm_drag_e2e\` to'liq o'chirilgan.
- Yangi backend loglarida `password`/`authorization`/`bearer` yo'q; haqiqiy
  kontrakt skrinshoti olinmagan.
- `%TEMP%\swm_e2e\` dagi 2026-09-10 dan qolgan parol/token qatorli eski
  sinov fayllari direktor tomonidan alohida skanerlanib tozalandi.

### G. Ochiq / keyingi bosqich

1. **Foydalanuvchi uchun:** CDL raqami (1↔14-bet) va CDL muddati (1↔2-bet)
   hujjatning o'zida farq qiladi — manbada tuzatish kerak. Bu inconsistency
   bo'lgani uchun override mavjud emas (dizayn bo'yicha).
2. `d62c63de` — shablon faylining o'zi. Ideal holda imzolanmagan toza shablon
   yuklash tavsiya etiladi (ogohlantirishlar endi buni ko'rsatadi).
3. Layout `localStorage` da — boshqa kompyuterda ko'rinmaydi; kerak bo'lsa
   server tomonida saqlash.
4. `template_match` har parse'ga ~4–5 s qo'shadi.
5. `c1b6de5d` va `40a1a2d7` — o'zaro bayt-dublikat. Dublikat ogohlantirishi
   09-14 dan keyin qo'shilgan, ular undan oldin yuklangan bo'lishi mumkin —
   tekshirilmagan.
6. Backend slot dx/dy ni sahifa chetiga clamp qilmaydi (faqat ±200) —
   sahifadan tashqariga chiqishni faqat frontend to'xtatadi (C bo'limi).
7. 2026-09-14 J-7 (`p1_cdl_expiration`/`p14_cdl_expiration` bbox) va imzo
   bbox'ining chiziqdan 11.4 pt yuqoridaligi (`field_detector.py:335`)
   detektorda tuzatilmagan — frontend `slotBoxRect` bilan ekranda
   kompensatsiya qilingan.

### H. Spot-check (reporting, yozuv paytida)

Brif raqamlaridan farq qilib, joriy kod bo'yicha tuzatilganlari:

| Brifda | Joriy kod |
|---|---|
| `field_detector.py:335` `top = run.y − 34` | `top = run.y - height`, `SIGNATURE_HEIGHT = 34.0` (`:28`) |
| `SignaturePage.tsx` slot qutilari `:977`, `:1022` | `:999`, `:1044` |
| `SignaturePage.tsx` clamp `:257`, xabar `:260-280` | `:264-278` |
| `SignaturePage.tsx` finalize tekshiruvi `:846`, 422 tozalash `:869` | `:868`, `:884-891` |
| `SignaturePage.tsx` badge `:484` | hisob `:484`, badge `:1177-1183` |
| `ReviewPage.tsx` `useContractLayout` `:73`, bloklovchilar `:27`/`:279`, banner `:200` | `:64`, `:28`/`:280-287`, `:202` |
| `TemplateManager.tsx:185-191` | `:185-195` |
| `pdf_parser.py` `template_match` yordamchilari `:763-805`, sana yordamchilari `:628-760` | `:771-805`, `:630-768` |
| `pdf_parser.py` B `:418-431`, A `:435-438` | `:418-430`, `:435-440` |
| `tests/test_visible_detection.py` 19 funksiya | yozuv paytida **20** `def test_` / **25** yig'ilgan holat |
| pytest 400 passed | `pytest --collect-only` jami **401** — integratsiyada **401 passed** tasdiqlandi (F.1) |

Tasdiqlangan (o'zgarishsiz): `contract.py` `:255/:258/:279/:284-297`;
`pdf_builder.py` `:88/:105-109/:275/:284/:300/:324-325`;
`signatures.py:704-723`; `tasks.py` `:93-100/:158/:168-170/:197`;
`comparison.py:60-68/:80-81`; `templates.py:91/:100-109`;
`template.py:47-59/:73-75`; `test_template_baseline.py` `:118-132/:143-151/:220-222`;
`layout.ts` `:20/:55/:93/:171/:187/:267/:309/:350/:359`;
`FieldHighlighter.tsx:53`; `LayoutPanel.tsx:235/:244`; `types/template.ts:14`;
`types/comparison.ts:78`; `index.css:114-115`; konstantalar 0.995/0.9/1.5/0.5.
Tekshirib bo'lmagan: barcha «eski» qator raqamlari, DB/audit holati, hash va
o'xshashlik raqamlari, mock va quruq ishga tushirish natijalari.

### I. Reviewer override kritik nomuvofiqliklarga kengaytirildi (appsec + frontend)

#### I.1 Shikoyat

Integratsiyadan keyin foydalanuvchi Decision panel skrinshoti bilan: «hali
ham keyingi stage'ga o'tkazmayapti — tekshir, nega». Panelda: «Override is
not available; correct these first: Cross-field inconsistency: CDL numbers do
not match across pages (pages 1, 14); CDL expiration dates conflict across
the document (pages 1, 2)». Izoh maydonida «111111» (6 belgi).

#### I.2 Tekshiruv (direktor, faqat o'qish, `local.db` `mode=ro`)

- To'siq dizayn bo'yicha ishlagan: 2026-09-10 override siyosati faqat kritik
  `missing_required` uchun edi; kritik nomuvofiqlik bo'lsa override yo'q
  (G-1 bandi ham shuni qayd etgan).
- Aniq farqlar (6 kontrakt bo'yicha, qiymatlar maskalangan; direktor
  tekshiruviga ko'ra, reporting DB'ni qayta o'qimagan): `p14_cdl` `p1_cdl`
  dan **faqat 1-pozitsiyada** farq qiladi (14-betda «F», qolgan 8 belgi bir
  xil); `p2_cdl_expiration` = 02/13/2031, `p1_cdl_expiration` — `d62c63de`
  da 02/14/2031 (−1 kun), `40a1a2d7`/`c1b6de5d`/`cf0ac9a2`/`e1ff6f07` da
  6/4/2031 (−111 kun). PDF'da shunday bosilgan (E bo'limi diagnostikasi
  render bilan tasdiqlagan) — bosma xato.
- «Correct value» formasi cross-field qatorlarga chiqmaydi:
  `frontend/src/components/ComparisonSidebar.tsx:142` faqat `issue.fieldId`
  bo'lsa → ilova ichida o'tkazish yo'li yo'q edi.
- Kuzatuvlar: `cf0ac9a2` va `e1ff6f07` — `40a1a2d7` bilan bir xil qiymatlar
  (yana nusxalar bo'lishi mumkin); `76a2e7f7` (reviewing/rejected) —
  `p1_cdl` 1 belgi, `p14_cdl` 13 belgi o'qilgan → o'qish xatosi gumoni,
  tekshirilmagan.
- Override izohi kamida 10 belgi — «111111» (6) baribir yetarli emas edi.

#### I.3 Qaror (AskUserQuestion)

**«Izoh bilan override (FMCSA hamon yopiq)»** tanlandi. Rad etilgan: manba
hujjatni tuzatish (Docusign qayta imzolash og'ir), ilovada qiymatni tuzatish
(yakuniy PDF'da bosma xato qoladi).

#### I.4 Backend (appsec; pytest 401 → **412 passed**)

- `app/services/review_override.py`: `BLOCKING_CATEGORIES =
  ("fmcsa_violations",)` (`:27`); `critical_inconsistencies` (`:62`, faqat
  `{type, issue, pages}`, `values`/PII yo'q); `critical_issue_keys` (`:80`,
  missing field_id'lar + `inconsistency:<type>`, barqaror tartib,
  dublikatsiz; type'siz → `inconsistency:untyped`); `override_new_issues`
  (`:92`, `fmcsa:<rule>` + override to'plamida yo'q joriy kalitlar);
  `override_permits_signing` (`:106`).
- `app/api/review.py:63-96`: izoh <10 → 422 (`:76-79`); kritik FMCSA → 409
  `blocking_categories: ["fmcsa_violations"]` (`:80-85`);
  `review_override_fields = critical_issue_keys(...)` (`:86`);
  `override_missing` nomi moslik uchun saqlangan. `:88` bo'sh override
  (override qilinadigan kritik muammo yo'q) → 409 `blocking_categories: []`.
  Audit `review_override` (`:110-123`, `details` `:115-120`)
  `{field_ids, inconsistencies:[{type,issue,pages}], reason}`.
- `app/api/signatures.py`: `_assert_signable` (`:68`; QISM-TO'PLAM darvozasi
  `:90-99`); `place_signature` (`:253`, chaqiruv `:267`), bulk
  `place_signatures_bulk` (`:312`, `:339`), `finalize_contract` (`:484`,
  `:494`) shu funksiyadan o'tadi. Yangi muammo → `409 {"error":"conflict",
  "message":"New critical issues appeared after the reviewer override; the
  contract must be reviewed again.","details":{"reason":
  "new_issues_after_approval","new_issues":[...]}}` (FMCSA keyin paydo
  bo'lsa ham shu shakl, `fmcsa:<rule>` bilan). `_override_metadata` (`:702`):
  `Subject` = «Reviewer override: <izoh> - fields: <label> (<id>);
  inconsistencies: <issue> (pages 1, 2)», izoh ≤500 (`:698`), butun
  `Subject` ≤2000 (`:699`, yangi chegara), `Keywords` `reviewer-override`
  (`:697`).
- O'zgartirilgan eski testlar (`tests/test_api_review.py`):
  `test_an_override_cannot_cover_a_critical_inconsistency` →
  `test_a_reviewer_can_override_a_critical_inconsistency_with_a_reason`
  (`:219`, 200); `test_both_blocking_categories_are_reported_together` →
  `test_an_fmcsa_violation_still_blocks_an_inconsistency_override` (`:269`);
  missing override testi audit'iga `"inconsistencies": []`; keyin-paydo-
  bo'lgan FMCSA va inconsistency testlari yangi 409 shakliga (`new_issues`)
  moslandi — place, bulk, finalize uchalasida (yordamchi
  `_assert_signing_blocked` `:514-531`).
- Chegara (PLAUSIBLE): taqqoslash `type` bo'yicha, sahifalar bo'yicha emas —
  `cdl_mismatch` boshqa sahifalar orasida qayta chiqsa yangi muammo
  hisoblanmaydi.

#### I.5 Frontend (general-purpose; `tsc` EXIT 0, `build` OK, mock 15/15)

- YANGI `lib/override.ts` — `review_override_fields` ni o'qiladigan
  ko'rinishga (missing → label; `inconsistency:<type>` → comparison'dagi
  `issue` + sahifalar, bo'lmasa type'dan odam o'qiydigan).
- `pages/ReviewPage.tsx`: override sharti (`:128-133`, FMCSA holati `:134`) —
  `can_sign` false, kritik FMCSA 0, kritik bo'sh maydon YOKI nomuvofiqlik;
  override qutisida ikkita ro'yxat (`:242`, `:255`); izoh namunasi (`:34`);
  confirm matni nol qismni tushiradi (`:163-171`); «Override is not
  available: FMCSA violation must be corrected first: <issue> (page N)» faqat
  FMCSA uchun (`describeOverrideBlockers` `:23`, matn `:29`).
  `api/contracts.ts:35-36` 409 xabari faqat FMCSA.
- `components/OverrideBadge.tsx` — «1 required field and 2 inconsistencies
  skipped» + ro'yxat, review (`ReviewPage.tsx:350`) va sign sahifalarida;
  `components/ComplianceBadge.tsx:23` nomuvofiqliklarni sanaydi.
- `pages/SignaturePage.tsx`: override + FMCSA yo'q → bloklanmaydi
  (`:501-503`); placement/bulk/avto-to'ldirish/finalize 409 → «New issues
  appeared after approval — the contract must be reviewed again.» (`:147`,
  `:673`, `:758`, `:814`); `api/signatures.ts:101` `isNewIssuesConflict`
  (`details.reason` da `new_issues`), matn `:111`.
- Mock tekshiruvlar (agent hisobotiga ko'ra): (a) faqat 2 nomuvofiqlik →
  ikkala qator sahifalar bilan, 9 belgida tugma o'chiq / 10 da yoqiq, confirm
  «…with 2 cross-field inconsistencies…», tana `override_missing:true`;
  (b) bo'sh maydon + nomuvofiqlik → ikkala ro'yxat; (c) FMCSA + nomuvofiqlik
  → override yo'q; (d) badge `inconsistency:cdl_mismatch` ni xom emas,
  o'qiladigan ko'rsatadi; (e) sign sahifasida 409 xabari. Oldingi surish
  to'plami 28/28 (eski «CDL blocks approval» kutuvi yangilangan);
  ogohlantirishlar to'plami 13/14 — bitta test eski FMCSA matn formatini
  kutgan (ilova emas, test skripti). Skrinshotlar (5 ta, `a_`…`e_`)
  `c:\PTG\.playwright-mcp\swm_ovr2\`.
- Eslatma: override badge tasdiq paytidagi to'plamni, compliance badge joriy
  comparison'ni sanaydi — sonlar farq qilishi mumkin.

#### I.6 Integratsiya — PASS (2026-09-17)

| Qadam | Natija |
|---|---|
| pytest | **412 passed** (186.83 s) |
| frontend | `tsc` EXIT 0, `build` OK (2.44 s) |
| Zaxira | `backend/local.db.bak-20260917-override2` (online backup, `integrity_check` ok; 1 foydalanuvchi, 6 kontrakt, 1 shablon) |
| Restart | PID 12992 to'xtatildi → launcher 33732, `:8000` tinglovchi **18612**, `/health` 200; frontend PID 4660 tegilmagan |
| Haqiqiy `d62c63de` (pending, faqat ko'rish, DOM) | override qutisi 1 ta; «CDL numbers do not match across pages (pages 1, 14)» va «CDL expiration dates conflict across the document (pages 1, 2)»; bo'sh maydonlar: «Vehicle Assignment · Page 3»; «Override is not available» 0; izoh 9 belgida tugma o'chiq, 10 da yoqiq (tozalandi, BOSILMADI); 30 API so'rovdan GET'dan boshqa yagona `POST auth/login`; kontrakt hamon `pending` |
| 4A sintetik `GET /compare` | `can_sign=false`, `cdl_mismatch` + `exp_date_conflict` (sahifalar [1, 2] — sintetik shablon 2 betlik), missing 0, FMCSA 0 |
| 4B brauzer override | confirm «You are approving a contract with 2 cross-field inconsistencies…»; tana `{"decision":"approved","notes":"…","override_missing":true}` → **200** → `/sign` |
| 4C detail/audit | `review_override_fields` = `['inconsistency:cdl_mismatch','inconsistency:exp_date_conflict']`; audit `review_override` = `{field_ids:[], inconsistencies:[{type,issue,pages}], reason}`, qiymat yo'q |
| 4D imzolash | «Signing is blocked» 0; badge «Approved with override · 2 inconsistencies skipped» + ikkala element; imzo 201, finalize **200 completed**; PDF `Subject` «Reviewer override: … - inconsistencies: CDL numbers do not match across pages (pages 1, 2), CDL expiration dates conflict across the document (pages 1, 2)», `Keywords` `reviewer-override`; `/verify` `intact: true` |
| 4E yangi-muammo darvozasi | override 200 → `PATCH p1_full_name` "" 200 → `POST /compare` (`p1_full_name` missing) → imzo, bulk, finalize har biri **409** `{"reason":"new_issues_after_approval","new_issues":["p1_full_name"]}`; brauzerda imzo qo'yishda «New issues appeared after approval — the contract must be reviewed again.» |

- **UX kuzatuvi (dizaynga mos, xato emas):** yangi muammo paydo bo'lgach sign
  sahifasi ochilganda o'zi bloklanmaydi va badge hali «2 inconsistencies
  skipped» ko'rsatadi — xabar faqat imzo/finalize urinishida (server 409)
  chiqadi. Sahifa ochilganda oldindan ogohlantirish kerak bo'lsa — frontend
  yuklanishda yangi muammolarni tekshirishi kerak (I.7 bandi 5).
- **Gigiyena:** sintetik kontraktlar `a00150e5`, `bfa304c2` DELETE 200,
  shablon `c011256c` purge 200; baza 1 foydalanuvchi / 6 kontrakt / 1 shablon;
  ZZ-OVR2 qatorlari, maydonlar, imzolar, storage fayllari qolmagan; audit
  qatorlari `contract_id=NULL` bilan (dizayn bo'yicha); yangi foydalanuvchi
  yaratilmagan; avto-to'ldirish o'chirilgan — adminning saqlangan imzolari
  kontraktga qo'yilmagan; `%TEMP%\swm_ovr2_e2e\` da parol/token yo'q
  (skriptlar `.env` ni ishga tushishda o'qiydi; skanerning `b"eyJ"` qidiruv
  qatori soxta-musbat); skrinshot olinmagan.

#### I.7 Ochiq

1. `76a2e7f7` CDL o'qilishi (1 belgi) — tekshirilmagan.
2. `cf0ac9a2`, `e1ff6f07` — ehtimoliy nusxalar (G-5 dagi
   `c1b6de5d`/`40a1a2d7` dublikat savoli bilan birga ko'rilsin).
3. Nomuvofiqlik taqqoslash type darajasida (sahifa emas).
4. G-1 bandi («override mavjud emas») endi eskirgan — I.3 qarori bilan
   izoh bilan override mumkin.
5. Sign sahifasi yuklanishda tasdiqdan keyingi yangi muammolarni oldindan
   ko'rsatmaydi (faqat 409 urinishda) — ixtiyoriy UX yaxshilash.

#### I.8 Spot-check (reporting, yozuv paytida)

| Brifda | Joriy kod |
|---|---|
| `review.py:64-95`, audit `:113-120` | `:63-96`; audit chaqiruvi `:110-123` (`details` `:115-120`) |
| `signatures.py` `_assert_signable` `:90-99` | funksiya `:68`; `:90-99` — qism-to'plam darvozasi bloki |
| `place_signature` `:255`, bulk `:327`, finalize `:482` | ta'rif `:253`/`:312`/`:484`; `_assert_signable` chaqiruvi `:267`/`:339`/`:494` |
| `ReviewPage.tsx` override sharti `:128, :134` | shart `:128-133`; `:134` — FMCSA tufayli override yo'qligi |
| `ReviewPage.tsx` confirm `:169` | `window.confirm` `:168`, qismlar `:163-167` |
| `ComplianceBadge.tsx:24` | `:23` |
| `SignaturePage.tsx` 409 `:146`, `:815` | `:147`, `:814` |
| pytest 412 passed | `pytest --collect-only` jami **412** (`.venv`); passed soni integratsiyada tasdiqlanadi |

Tasdiqlangan (o'zgarishsiz): `review_override.py` `:27/:62/:80/:92/:106`;
`review.py:88`; `signatures.py:702` va 409 matni/`details` shakli (`:95-99`);
`ComparisonSidebar.tsx:142`; `ReviewPage.tsx` `:23/:34/:255/:350`;
`SignaturePage.tsx` `:501-503/:673/:758`; `contracts.ts:35`;
`signatures.ts:101`; `lib/override.ts` mavjud; ikki qayta nomlangan test
mavjud, eski nomlar yo'q; skrinshotlar papkasi mavjud.
Tekshirib bo'lmagan: DB qiymatlari (CDL farqlari, 6 kontrakt, `76a2e7f7`),
mock natijalari, `tsc`/`build`, pytest passed soni.

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `comparison_engine.py` dagi `can_sign` ifodasi,
`field_detector.py`, `tests/conftest.py`. pytest **412 passed** (I.6 da
tasdiqlandi).
Backend launcher **33732** → `:8000` tinglovchi **18612**; frontend :5173 PID
**4660**. Zaxira `backend/local.db.bak-20260917-override2` (I.6); oldingi
`backend/local.db.bak-20260917-detect` (F.1) ham qoladi.

---

## 2026-09-23 — Interfeys qayta ishlash: uzluksiz scroll, eskizlar, qidiruv, burish, dizayn tizimi, dashboard, imzo UX, o'lcham dastagi (frontend + appsec)

Foydalanuvchi so'rovi bo'yicha ilovaning **butun interfeysi** qayta ishlandi:
PDF endi uzluksiz scroll bilan ko'riladi, eskizlar paneli, matn qidiruvi va
faqat-ko'rish uchun burish qo'shildi; dizayn butun ilova uchun yagona token
tizimiga o'tkazildi; yuklash sahifasi, dashboard va imzo qo'yish ekrani qayta
qurildi; har imzo slotiga o'z o'lchami (burchak dastagi) berildi. Ish sakkiz
alohida bosqichga bo'lindi (D0–D8), har biri mustaqil yetkazildi va har birida
to'liq tekshiruv to'plami yashil bo'ldi. **Detektsiya va imzolash darvozalari
tegilmadi** — `can_sign`, `_critical()` filtri, `field_detector.py`,
`pdf_parser.py` o'zgarmadi; barcha o'zgarish yo ko'rsatish (display), yo
qo'yish geometriyasi qatlamida.

### A. Foydalanuvchi so'rovi va qarorlari

So'rov besh banddan iborat edi:

1. umumiy interfeysni yaxshilash va yangi interfeyslar qo'shish;
2. imzo/sana qo'yish joyidagi interfeysni qulaylashtirish;
3. **PDF'ni scroll qilib ko'rish** (bitta sahifadan sakrash emas);
4. yuklash sahifasi;
5. **hozirgi imkoniyatlar o'zgarmasin** + Sejda uslubidagi imkoniyatlar.

`AskUserQuestion` bo'yicha qarorlar:

- Sejda ro'yxatidan **hammasi tanlandi**: uzluksiz scroll + zoom + eskizlar;
  istalgan joyga qo'yish (drag + resize); asboblar paneli + undo/redo; sahifa
  amallari.
- Keyin dalil ko'rsatilgach **sahifa o'chirish/tartibini o'zgartirish RAD
  ETILDI** → faqat **ko'rish uchun burish** qoldirildi. Sabab: yakuniy PDF hash
  bilan muhrlanadi, sahifa to'plamini o'zgartirish audit zanjirini va
  shablon↔kontrakt sahifa solishtiruvini buzadi, imzo joylari siljiydi.
- Dizayn: **butun ilova uchun yagona tizim** (bitta sahifa emas).
- Yangi sahifa: **dashboard**.

### B. Boshlang'ich tahlil (fayl:qator bilan)

| Topilma | Dalil |
|---|---|
| Dizayn tizimi yo'q | `index.css` 326 qator, 13 token, **0 ta** `@media` / `:hover` / `:focus-visible` / `transition` |
| TSX da ishlatilib CSS da yozilmagan **8 sinf** | `.pdf-viewer`, `.sidebar`, `.review__decision`, `.issue__correct*`, `.field__input`, `.blocker-line` |
| Sarlavha balandligi xato | `.review{height:calc(100vh-57px)}` — o'lchangan sarlavha **64px** |
| PDF ko'ruvchi bitta canvas | faqat joriy sahifa chiziladi (eski `usePdfViewer.ts:93-137`); `pageSizes` allaqachon bor (eski `:65-76`) |
| pt↔px konversiyasi **7 joyda tarqoq** | yagona modul yo'q |
| Matn qatlami yo'q | qidiruv imkonsiz |
| `rotation` state ishlatilmaydi | o'qilmaydigan prop |

**Unumdorlik chegarasi.** Haqiqiy hujjatlar 44/49/66 betli, sahifa o'lchamlari
**aralash** (612×792 va 612×817) → uniform-height virtualizator ishlamaydi.
Hamma sahifani bir vaqtda chizish ≈ **300–437 MB** canvas bitmap degani.
`pdfjs-dist@4.8.69` ichida tayyor `web/pdf_viewer.mjs` bor, lekin u loyihaning
DOM shartnomasini (overlay/slot qutilari) buzardi — **ishlatilmadi**; yangi npm
bog'liqlik ham qo'shilmadi.

**🚩 Saboq:** 2026-09-15 xavfsizlik tozalashida brauzer tekshiruv skriptlari
(`%TEMP%\swm_*_e2e`) o'chirib yuborilgan — **57 ta tekshiruv yo'qolgan**.
Shuning uchun birinchi bosqich ularni tiklash bo'ldi va endi ular vaqtinchalik
papkada emas, **repo ichida** (`tests/ui/`).

### C. Bosqichlar (har biri alohida yetkazildi, har birida to'liq to'plam yashil)

| Bosqich | Nima | Natija |
|---|---|---|
| **D0** | `tests/ui/` tiklandi (`_harness.py` MockApi, `test_layout_drag.py` 28, `test_warnings.py` 14, `test_override.py` 15, `run_all.py`, `README.md` + **DOM shartnomasi**) | 57/57, 3 marta ketma-ket toza |
| **D1** | `lib/viewport.ts` — `ptToPx`/`pxToPt`/`pxPointToPt`/`ptRectToPxBox`, 7 tarqoq joy almashtirildi; `placementSize` o'lik prop olib tashlandi, `rotation` qoldirildi | atribut dump **SHA-256 oldin=keyin** (`054e6dce…`), 57/57 |
| **D2** | uzluksiz scroll + virtualizatsiya (`usePdfViewer.ts` 172→688 q., `IntersectionObserver` ±2, `MAX_RENDERED_PAGES=6`, `MAX_ACTIVE_RENDERS=2`, `canvas.width=0` bo'shatish, `currentPage` hosila, `scrollToPage`, zoom anchor, `ResizeObserver` fit, miltillash nuqsoni tuzatildi), `PdfViewer.tsx` 94→155, `renderPageOverlay(pageNumber)` prop (`children` olib tashlandi), `viewer.css` yangi, `data-page-number` | 44/66 betda heap **7.1/7.2 MB barqaror**, ≤5 canvas (naive ≈437 MB), 57/57 |
| **D3** | eskizlar `PdfThumbnails.tsx` (317 q., `renderPageTo` — hujjat qayta yuklanmaydi, `MAX_DRAWN=14`, roving tabindex, `swm.thumbs`) + **`tests/ui/test_viewer.py` 16 piksel tekshiruvi** | 73/73; **teshik yopildi** — `zoom_repaints` bo'sh canvas'ni tutdi; strip 66 betda +0.55 MB heap / 0.99 MB bitmap |
| **D4a** | dizayn tizimi: `index.css` 326→840 q., 77 token, 21 `:hover` / 8 `:active` / 7 `:focus-visible` / 13 `transition`, `prefers-reduced-motion`, **8 yozilmagan sinf yozildi**, 3 `@media` (1024/760), `--header-h: 64px` (57px xato edi), `.review__panel: clamp(320px,26vw,380px)` | 57/57, ranglar o'zgarmadi (12 asl qiymat saqlandi) |
| **D5** | yuklash sahifasi 198→~470 q. + `PdfDropZone.tsx`, `lib/contractUpload.ts`, `lib/pdfThumbnail.ts`, `upload.css`; bosqichli oqim, drag&drop, bet soni+eskiz, shablon kartochkalari + pre-filled ogohlantirish, dublikat modalida **Cancel asosiy** | 57/57 + o'z 27/27; dublikat mantig'i (SHA-256, 8 s timeout, xatoda jim davom etish) **bayt-ba-bayt** saqlangan |
| **D6a** | dashboard 127→~470 q. + `dashboard.css`: 6 guruh (sonlar jamlanadi), qidiruv/saralash, `swm.dash.*`, tez amallar, bo'sh holatlar, 760px kartochka | 57/57 + o'z 7/7; `Dashboard.before.tsx` zaxirasi |
| **backend** | `ContractListOut` (**`ContractOut` EMAS** — aks holda detail javobiga ziddiyatli `null` maydonlar tushardi): `template_name` (LEFT JOIN), `critical_*_count`, `can_sign`, `review_override`, **`stored_signature_slot_count`** (filtrlanmagan — `signing_comparison` sahifa filtri qo'llanmagan); `critical_counts()` **review/signing darvozalari bilan bir xil** `_critical()` filtridan | pytest 423; **N+1 yo'qligi mutatsiya bilan isbotlangan** (5→32 so'rov bilan test yiqildi); 6 kontraktda ~4 ms (o'zgarmadi); PII javobda yo'qligi test bilan qulflangan |
| **D6b** | yagona tabli `SignatureModal.tsx` (421 q., uchala tab **mount bo'lib turadi** → yarim chizilgan imzo va tanlangan fayl saqlanadi, fokus tuzog'i, Escape, roving tab), `SlotToolbar.tsx` (155 q.) mini-panel, `SlotCardList.tsx` (299 q.) sahifa bo'yicha guruh, `signing.css`; `padMode` olib tashlandi | 73/73 + o'z 11/11; **surish ajratilgani `elementFromPoint` bilan isbotlangan** (panel slot markazini qoplamaydi), 220 ms hover grace, surish paytida yashiriladi |
| **dashboard+** | boyitilgan maydonlarga ulanish: shablon **nomi** bo'yicha qidiruv, kritik belgilar (FMCSA alohida to'ldirilgan qizil), override belgisi, «needs attention» filtri; `stored_signature_slot_count` **ataylab ko'rsatilmadi** (filtrlanmagan son imzo ekraniga zid bo'lardi); «Not ready to sign» override qoplasa yashiriladi (`SignaturePage.tsx` `overrideCovers` qoidasining aynan o'zi) | 73/73 + o'z 10/10; `null`/`"3"`/`-1` kabi buzuq qiymatlar belgi bermaydi |
| **D7 backend** | `slot_scales` [0.3, 3.0] (global `signature_scale` [0.6,1.5] ni **almashtiradi**, ko'paytirmaydi — `pdf_builder.py` `signature_scale_for`), faqat **imzo** slotlari (sana → 422), erkin joylashtirishga tegmaydi, noma'lum kalit mavjud `details.unknown_slot_offsets` ga qo'shiladi | pytest 423→**462** (+39); **pastki-chap burchak qotgani** 0.3–3.0 da test bilan qulflangan; sahifa chetiga clamp **yo'q** (frontend qiladi) — belgi kesiladi, jimgina yo'qolmaydi |
| **D7 frontend** | `useSlotResize.ts` (267 q.), `SlotResizeHandle.tsx` (81 q.), `lib/layout.ts` sanitizatsiya/chegaralar (`slotScaleBounds`, `fitLayoutToPagesReport` — avval o'lcham, keyin joylashuv), `LayoutPanel` ScaleStepper + `own-size-note`, `SlotToolbar` «Auto size» | 94/94 + o'z 27/27; sudrashda `data-left-pt`/`data-bottom-pt` **bayt-ba-bayt o'zgarmaydi**, aspect saqlanadi; dastak `pointerdown` ni to'xtatadi + o'z pointer-capture; global slayder o'z o'lchamli slotga tegmaydi |
| **D8** | qidiruv: `usePdfSearch.ts` (180 ms debounce, sahifama-sahifa, `MAX_HITS=500`), `PdfSearchBox.tsx`, `PdfSearchMarks.tsx` (`.pdf-search-layer` `.pdf-overlay` dan **oldin**, `pointer-events:none`), matn keshi `usePdfViewer` da (`getPageText`, `MAX_TEXT_CHARS=1.5M`, `MAX_TEXT_PIECES=150k`, eng eski chiqariladi); burish: pdf.js viewport bilan (CSS cho'zish emas), overlay `rotationTransform` bilan **nuqta darajasida** to'g'ri (`(x,y,w,h)→(H−y−h,x,h,w)`, ±1 pt), **burilganda joylashtirish o'chadi** — `pdf-overlay__shield` (z-index 30) + izoh | 94/94 (yangi `test_search.py` 11 + `test_rotation.py` 10); 66 betli zich hujjatda qidiruv +2.73 MB heap, sovuq 2.21 s → issiq 0.91 s |

**Burish qarori (dalil bilan):** burilgan sahifada joylashtirish o'chiriladi,
chunki `useSlotDrag.ts` kursor siljishini to'g'ridan-to'g'ri o'qlarga qo'llaydi
(burilganda o'qlar almashadi) va `tests/ui/README.md` masshtabni
`rect.width / 612` deb o'qiydi (burilgan sahifa 792 pt kenglikda → 1.294×
xato).

**🚩 Test uslubi topilmasi:** `locator.screenshot()` bu loyihada ishonchsiz —
`.pdf-page` ichki scroll qutisida joylashgan, Playwright clip'ni hujjatga
nisbatan hisoblaydi va 1- va 2-sahifa uchun **bir xil** piksel natijasini
qaytardi. Clip `.pdf-stage` ga nisbatan qo'lda hisoblanadi (usul
`tests/ui/README.md` da yozilgan).

### D. Yakuniy integratsiya — PASS (2026-09-23)

- **Statik:** pytest **462 passed** (217.95 s), `typecheck` exit 0, `build`
  exit 0 (96 modul), `tests/ui/run_all.py` **94/94** (147 s).
- **Zaxira** `backend/local.db.bak-20260925-ui` (fayl nomidagi sana noto'g'ri —
  zaxira 2026-09-23 da olingan; nom diskda shunday qolgan) (online backup API,
  `integrity_check` ok; users 1, contracts 9, templates 1). Restart:
  PID 2376 → **8480** (launcher 35588), `/health` 200; frontend PID 15536
  tegilmadi.
- **Jonli API:** `GET /api/contracts` da yettita yangi maydon keladi,
  `comparison_result`/`extracted_data` **yo'q**; detail javobi **o'zgarmagan**.
- **Haqiqiy 49 betli kontrakt (`e1ff6f07…`), faqat ko'rish:** 49 ta
  `.pdf-page`, hisoblagich `1/49`→`49/49`; heap 21.2 → 37.8 → **23.2 MB**
  (oqma yo'q), bitmap ushlab turgan canvas **3** (chegara 6); eskizlar 49 ta,
  **14 chizilgan**, 7-ni bosish o'sha betga olib bordi; qidiruv «Contractor»
  **1/133, 0.87 s**, kesh bilan **0.33 s**, «Signature» 1/63 0.33 s; burish:
  `data-rotation=90`, quti 990×1281→990×765, overlay
  `matrix(0,1,-1,0,990,0)`, slot qutisi 434×20→16×335, shield + izoh, to'liq
  aylanadan keyin **aynan tiklandi**; dashboard 9/9 qatorda shablon nomi,
  override belgisi 3 ta kontraktda. **So'rovlar: 22 GET + 1 POST
  `auth/login`** — boshqa yozuv yo'q, skrinshot olinmadi.
- **Sintetik uchdan-uchgacha:** drop-zone'ga haqiqiy `drop` → analyzed →
  approve → Draw bilan imzo (tab aylanasidan keyin **2439 ink piksel**
  saqlandi) → mini-paneldan sanalar → burchak dastagidan **1.400×**
  (`left_pt` 200.00→200.00, `bottom_pt` 592.40→592.40) → bitta slot surildi →
  **finalize 200**. So'rov tanasi: `slot_scales:{"p1_contractor_signature":1.4}`
  (faqat o'zgartirilgani), `slot_offsets_pt:{"p3…":11}`,
  `slot_offsets_x_pt:{"p3…":17}`.

| Yakuniy PDF | Eni | Chap | Siyoh pasti |
|---|---|---|---|
| p1 (1.4× o'lchamli) | **214.06** (=152.9×1.4, Δ0.000) | 200.00 | 592.40 |
| p2 | 152.90 | 200.00 | 592.40 |
| p3 (surilgan) | 152.90 | **217.00** | **581.40** |

  Sanalar baseline 592.402 (Δ0.000); `/verify` **intact: true**.
- **Gigiyena:** sintetik kontrakt/shablon DELETE+purge 200; baza zaxira bilan
  solishtirildi — farq faqat `audit_logs +19` (ataylab saqlanadigan iz);
  skriptlarda parol/token qiymati **yo'q**; `tests/ui/README.md` ga D7
  hook'lari qo'shildi (yagona kod o'zgarishi).
- **Kuzatuv (xato emas):** «Needs attention only» filtri jonli ma'lumotda hech
  narsani kesmaydi, chunki 9 kontraktning hammasida muammo belgisi bor;
  sintetik toza qator bilan 10→9 ishlashi tasdiqlandi.

### E. Ochiq / keyingi bosqich

1. `SignaturePage.tsx` hali **~1600 qator** — hook'larga bo'lish (D9)
   qilinmadi.
2. Undo/redo (Sejda ro'yxatidan) — faqat «Undo auto-fill» va dbl-click reset
   bor; umumiy undo yo'q.
3. Istalgan joyga **matn/belgi** qo'yish — hozir faqat imzo (erkin
   joylashtirish) va sana slotlari; erkin imzolar **surilmaydi**.
4. Zoom paytida sahifa ~50 ms oq ko'rinadi (canvas o'lcham o'zgarishi
   bitmapni tozalaydi) — ikki-buferli yechim xotira evaziga.
5. `.pdf-stage` ga `tabIndex` qo'yilmagan — klaviatura bilan scroll Chrome
   127+ xususiyatiga tayanadi.
6. Backend so'rov tanasi hajmiga middleware cheklovi yo'q
   (`slot_offsets*`/`slot_scales` kalit **uzunligi** cheklanmagan) — mavjud
   holat, yangi yuza emas, PLAUSIBLE.
7. Layout `localStorage` da (boshqa kompyuterda ko'rinmaydi) — server tomonida
   saqlash qilinmadi (2026-09-17 G-3 bandi hamon ochiq).

### F. Spot-check (reporting, yozuv paytida)

Brif raqamlari joriy kod bilan solishtirildi. Qator sonlari bosqich
yetkazilgan paytdagi holat — keyingi bosqichlar o'sha fayllarni o'stirgan,
shuning uchun C jadvalidagi sonlar tarixiy, quyidagilar **yozuv paytidagi**:

| Brifda (bosqich paytida) | Joriy kod (yozuv paytida) |
|---|---|
| `usePdfViewer.ts` 172→688 (D2) | **1062** (D8 matn keshi qo'shdi) |
| `PdfViewer.tsx` 94→155 (D2) | **296** (qidiruv qatlami + shield) |
| `PdfThumbnails.tsx` 317 | **329** |
| `index.css` 326→840 | **839** |
| yuklash sahifasi 198→~470 | fayl nomi `pages/ContractUpload.tsx`, **569** |
| dashboard 127→~470 | `pages/Dashboard.tsx` **884** (dashboard+ bosqichi o'stirgan) |
| `SlotToolbar.tsx` 155 | **182** |
| `SignaturePage.tsx` ~1600 | **1693** |
| burish qarori dalili `useSlotDrag.ts:133-137` | o'qlarga qo'llash aslida **`:141-142`** (`movedX`→dx, `movedY`→dy); `:133-137` — sozlash va izoh bloki |
| `Dashboard.before.tsx` zaxirasi | zaxira nusxa yaratilgan deb hisobot berilgan, lekin diskda topilmadi (`frontend/src/pages/` da yo'q, repo bo'yicha `*.before.*` 0 ta) — **revert nusxasi YO'Q** |

Tasdiqlangan (o'zgarishsiz, joriy kodda mos): `viewport.ts` `ptToPx :26` /
`pxToPt :31` / `pxPointToPt :46` / `ptRectToPxBox :70` (+6 burish yordamchisi,
`rotationTransform :128`); `usePdfViewer.ts` `MAX_RENDERED_PAGES=6 :46`,
`MAX_ACTIVE_RENDERS=2 :48`, `MAX_TEXT_CHARS=1_500_000 :79`,
`MAX_TEXT_PIECES=150_000 :81`, `getPageText :477`, `scrollToPage :625`;
`PdfThumbnails.tsx` `MAX_DRAWN=14 :28`; `usePdfSearch.ts` `DEBOUNCE_MS=180 :34`,
`MAX_HITS=500 :36`; `PdfViewer.tsx` `renderPageOverlay :50`,
`data-page-number :249`, `PdfSearchMarks :265` **`.pdf-overlay :272` dan
oldin**, `pdf-overlay__shield :281`; `viewer.css` shield `z-index: 30`
(`:229-235`); `index.css` 77 token / 3 `@media` / 21 `:hover` / 8 `:active` /
7 `:focus-visible` / 13 `transition` / `prefers-reduced-motion` /
`--header-h: 64px` / `clamp(320px, 26vw, 380px)` — **hammasi aynan**;
`schemas/contract.py` `ContractListOut :29`, `template_name :44`,
`critical_missing_count :47`, `critical_inconsistency_count :48`,
`critical_fmcsa_count :49`, `can_sign :53`, `review_override :54`,
`stored_signature_slot_count :62` (**yettita maydon**, izohda
«unfiltered count» sababi yozilgan), `SLOT_SCALE_MIN=0.3` / `MAX=3.0`
(`:306-307`), validator `:355-366`; `pdf_builder.py` `signature_scale_for :120`
va «REPLACES the global scale» docstring `:32`, qo'llash `:302`;
`api/signatures.py:754-776` (faqat imzo slotlari, `unknown_slot_offsets[:50]`);
`review_override.py` `_critical :38` / `critical_counts :46`;
`layout.ts` `slotScaleBounds :483`; `LayoutPanel.tsx` `ScaleStepper :140`,
`own-size-note :293`; `SlotToolbar.tsx` «Auto size» `:88`;
`tests/ui/README.md` `rect.width / 612` (`:189`, `:206`), screenshot ogohligi
(`:100-103`), `data-left-pt`/`data-bottom-pt` (`:138-139`), `.slot-resize`
qoidasi (`:199`).

O'lchov bilan tasdiqlangan: `pytest --collect-only` → **462**;
`tests/ui` bo'yicha **94** tekshiruv (layout 28, override 15, viewer 16,
warnings 14, search 11, rotation 10 — brif taqsimoti bilan aynan mos);
`package.json` da **yangi bog'liqlik yo'q** (`pdfjs-dist 4.8.69`, react 18.3.1,
react-dom 18.3.1, react-router-dom 6.28.0); `backend/local.db.bak-20260925-ui`
**mavjud**; `netstat` — `:8000` LISTENING **8480**, `:5173` LISTENING
**15536** (ikkala PID jonli).

Tekshirib bo'lmagan (**agent hisobotiga ko'ra**): barcha «oldin» qator
raqamlari va bosqich paytidagi sonlar; heap/bitmap o'lchovlari va
taymingar (7.1/7.2 MB, 21.2→37.8→23.2 MB, 0.87/0.33 s, 2.21→0.91 s, 147 s,
217.95 s); SHA-256 `054e6dce…`; brauzer DOM o'lchovlari va burish matritsasi;
yakuniy PDF o'lchovlari va `/verify`; N+1 mutatsiya testi va ~4 ms;
so'rovlar soni (22 GET + 1 POST); `audit_logs +19`; `typecheck`/`build` exit
kodlari va 96 modul; sintetik uchdan-uchgacha oqim (2439 ink piksel, 1.400×).

**Kod holati:** commit/push/deploy QILINMADI (signwithme git-repo emas).
Tegilmagan: `comparison_engine.py` `can_sign`, `pdf_parser.py`,
`field_detector.py`, `backend/tests/conftest.py`. Yangi npm bog'liqlik
**qo'shilmagan**. pytest **462**, `tests/ui` **94**. Backend PID **8480**,
frontend PID **15536** (ikkalasi ham yozuv paytida tinglab turibdi). Zaxira
`backend/local.db.bak-20260925-ui`; oldingi zaxiralar
(`…-20260917-detect`, `…-20260917-override2`) ham qoladi.

---

## 2026-09-24 — Hujjat muharriri (`/contracts/:id/edit`), annotations v4, 422 javobida so'rov tanasi oqishi tuzatildi (frontend + appsec)

Uch yo'nalishda ish bajarildi: (1) hujjatga matn/belgi/sana qo'yish uchun
**alohida to'liq ekranli muharrir**; (2) uni qabul qiladigan **backend
annotations v4** kontrakti va yakuniy PDF'ga chizish; (3) yo'l-yo'lakay
topilgan **422 javobida so'rov tanasining qaytishi** (HIGH) tuzatildi.

### A. Hujjat muharriri (yangi) — `/contracts/:id/edit`

Alohida to'liq ekranli sahifa. Yangi fayllar:
`frontend/src/pages/DocumentEditor.tsx`, `src/lib/annotations.ts` (459 qator),
`src/hooks/useAnnotations.ts`, `src/hooks/useAnnotationEdit.ts`,
`src/components/AnnotationLayer.tsx`, `src/components/EditorToolbar.tsx`,
`src/api/editor.ts`, `src/styles/editor.css`. Marshrut `src/App.tsx:40`.

| Asbob | Tafsilot |
|---|---|
| Matn | 8–48 pt, Helvetica / Times / Courier |
| ✔ va ✖ | 6–72 pt, bosilgan nuqta = **markaz** |
| Sana | paneldan tanlanadi, `MM/DD/YYYY` |
| Erkin imzo | quyidagi cheklovga qarang |

Tahrirlash: tanlash / sudrash / burchakdan o'lcham / Delete / Undo-Redo
(**100 qadam**, Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y). **Bitta sudrash = bitta qadam.**

Saqlash: `localStorage` kaliti `swm.annotations.<contractId>` — shakli backend
so'rov tanasi bilan **aynan bir xil**; buzuq yozuv jimgina tashlanadi.

Kirish havolalari («Open editor»): `Dashboard.tsx:874`, `ReviewPage.tsx:236`,
`SignaturePage.tsx:1470`.

**🚩 Cheklov — erkin imzo Undo bilan qaytmaydi va sudralmaydi.** Erkin imzo
annotation emas, **server yozuvi** (`POST /api/contracts/{id}/signatures`,
`field_id: null`), shuning uchun uni muharrirning Undo/sudrash mexanizmi
boshqarmaydi; o'rniga «Remove and place again». Sabab: klaviatura yorlig'i
audit yozuvini o'chirmasligi kerak.

### B. Backend — annotations v4

O'zgargan fayllar: `app/services/pdf_builder.py` (**461→732** qator),
`app/schemas/contract.py` (`AnnotationIn`), `app/api/signatures.py`.

Kontrakt: `{id, kind, page, x_pt, y_pt, text?, font?, size_pt}`,
`kind` ∈ `text|date|check|cross`, **≤200 element**, **yuqori-chap origin**,
matn = baseline-chap, belgi = markaz. Annotationlar imzolardan **keyin**
chiziladi (z-tartib piksel bilan isbotlangan).

**🚩 ✔/✖ shrift emas, vektor chiziq.** ZapfDingbats standart-14 shriftini
ko'ruvchi dastur ta'minlashi kerak, Windows'da esa u yo'q → pypdfium2
render'da **to'liq qora kvadrat** chiqdi. Shrift fayli qo'shilmadi, belgilar
vektor chiziq bilan chiziladi.

**🚩 Chizib bo'lmaydigan belgi endi 422.** Ildiz sabab: `reportlab` ning
`unicode2T1` kodlanmaydigan harfni **ogohlantirmasdan** ZapfDingbats `n`
baytiga (qora kvadrat) almashtirar edi — «Привет» yakuniy PDF'da olti qora
blok bo'lib chiqar, `/verify` esa `intact: true` derdi (ya'ni buzilish
jimgina, tekshiruvdan o'tib ketardi). `pdf_builder.py` dagi
`undrawable_character()` buni to'xtatadi va 422 qaytaradi.
**Ta'siri:** standart PDF shriftlari faqat WinAnsi (G'arbiy Yevropa) —
kirill, emoji va o'zbekcha `oʻ/gʻ` dagi maxsus apostrof **o'tmaydi**. Kerak
bo'lsa PDF shrift joylashtirish **alohida ish** sifatida ochiq qoladi.

Audit izi: faqat sanoq — `annotations: {total, by_kind}`; **matn va element
id saqlanmaydi**.

### C. Xavfsizlik tuzatmasi — 422 javobida so'rov tanasi qaytishi (CONFIRMED, HIGH)

- **Ildiz sabab:** `app/main.py` da `RequestValidationError` ishlovchisi yo'q
  edi → FastAPI standart javobi `detail[].input` ichida **rad etilgan
  qiymatning o'zini** qaytarardi; `frontend/src/api/client.ts:83` esa uni
  butunlay xato matniga aylantirib ekranga chiqarardi.
- **Ta'sir:** finalize, imzo qo'yish, ommaviy imzo va saqlangan imzo
  oqimlarining hammasida foydalanuvchi yozgan matn (**SSN bo'lishi mumkin**)
  va imzo PNG'ining base64 tanasi javobda qaytardi.
- **Tuzatma:** `app/main.py:7, 51-64, 67-92, 95-107` — `_scrub_validation_error()`.
  `loc`/`msg`/`type` qoladi, `input`/`ctx`/`url` tushadi, `msg` 500 belgida
  kesiladi. **Status kodi va `detail` shakli o'zgarmadi** (mijoz mosligi).
- **O'lchangan:** javob **8 353 → 202 bayt**; yuborilgan SSN javobda **yo'q**.

### D. Tekshiruv (dalillar)

- **Backend `pytest`: 594 passed** (bazaviy 462 → +122 annotations →
  +10 validatsiya).
- **Frontend:** `npm run typecheck` EXIT 0, `npm run build` EXIT 0,
  `python tests\ui\run_all.py` **123/123** (layout 28, warnings 14,
  override 15, viewer 16, search 11, rotation 10, **editor 29**).
- **Jonli integratsiya (2026-09-24 02:04):** zaxira
  `backend\local.db.bak-20260924-0204-annotv4` (`integrity_check` ok) va
  `backend\storage_backup_20260924-0204-annotv4` (59 fayl). Backend qayta
  ko'tarildi (eski PID **8480** → yangi launcher **29312** / uvicorn
  **28688**), `/health` va `/ready` **200**.
- Jonli `openapi.json` da `AnnotationIn` va `finalize` tanasida `annotations`
  bor (restartdan **oldin yo'q edi**).
- **Uchdan-uchgacha sintetik hujjat:** 4 xil annotation yakuniy PDF'ga tushdi
  va pypdfium2 bilan 8 px/pt da o'lchandi:

| Element | O'lcham | Natija |
|---|---|---|
| matn | 14 pt | chap **+0.25 pt**, baseline **+0.125 pt** |
| sana | 12 pt | chap **+0.5 pt**, baseline **+0.125 pt** |
| ✔ va ✖ | 24 pt | siyoh qutisi aniq **24.0×24.0 pt**, markaz xatosi **0.000 pt** |

  `/verify` → **`intact: true`**. Sintetik shartnoma/shablon keyin o'chirildi;
  baza baytma-bayt tiklandi, farq faqat **`audit_logs` +17 qator** (dizayn
  bo'yicha o'chmaydi).
- **Brauzer:** muharrir ochildi, 6 asbob faol, belgi va matn qo'yildi, Undo
  ishladi. Konsolda tutilmagan JS xatosi **yo'q**; yagona 404 —
  `favicon.ico`.

### E1. Jimgina yo'qotish yopildi — `extra="forbid"` (CONFIRMED, D dan keyin)

D bo'limida ochiq qolgan band **tuzatildi**, va tekshirish jarayonida
ikkinchi, og'irroq holat topildi.

- `app/schemas/contract.py:475` — `ContractFinalization` ga
  `model_config = ConfigDict(extra="forbid")` (izoh `:463-474`).
  **Oldin o'lchangan buzilish:** tana `{"contract_date": …, "annotation": […]}`
  → **HTTP 200**, `status = completed`, imzolangan PDF yozildi (3664 bayt),
  annotatsiya **chizilmadi**, hech qanday xato yo'q. Endi → **422**,
  `loc = ["body","annotation"]`, finalize umuman bajarilmaydi.
- 🚩 `app/schemas/contract.py:147` — `FieldCorrection` ham ochiq edi va
  **og'irroq**: `valu` deb xato yozilsa **200** qaytardi, `raw_value`
  o'zgarmasdi, lekin maydon `filled → verified` ga o'tar, `confidence = 1.0`
  bo'lar va `validation_error` tozalanar edi. Ya'ni parser xatosi «odam
  tekshirgan» maqomini olib imzo darvozasidan o'tardi. Jonli tasdiqlandi,
  endi 422.
- Frontend yuboradigan **har bir kalit** sxema bilan solishtirildi (11 kalit;
  `SignaturePage.tsx:1037-1041`, `DocumentEditor.tsx:549-562`,
  `lib/layout.ts:250-259`, `lib/annotations.ts:437-458`) — nomos kalit yo'q.
  Qurilgan `dist` bundle ham tekshirildi: eski build ham buzilmaydi.
- Qolgan so'rov sxemalari audit qilindi. **Ataylab tegilmadi:** `FieldUpdate`,
  `SignaturePlacementIn`, `BulkSignaturePlacementIn`, `UserSignatureCreate`,
  `DatePlacementIn`, `ReviewSubmission`, `UserCreate`, `CheckboxOption` —
  ularda xato kalit yo javobda ko'rinadi, yo majburiy maydon tufayli 422
  beradi, ya'ni **jim emas**; frontendni buzmaslik muhimroq deb hisoblandi.
  Eslatma: `UserCreate` da `role` maydoni e'lon qilingan, lekin `auth.py:43`
  uni **o'qimaydi** (qat'iy `contractor`) — huquq oshirish yo'q, sxemadan
  olib tashlash tavsiya etiladi.
- Testlar: `backend/tests/test_api_unknown_keys.py` — **17 ta**
  (7 xil xato yozilgan finalize kaliti, rad etilgan qiymat javobda
  qaytmasligi, frontendning to'liq tanasi hali ham 200, `FieldCorrection`
  uchun 5 xato kalit + 4 to'g'ri kalit). **pytest 594 → 611 passed**,
  yiqilgan yo'q.
- **Jonli:** backend qayta ko'tarildi (yangi uvicorn PID **33536**,
  02:36:04); `openapi.json` da `ContractFinalization.additionalProperties` va
  `FieldCorrection.additionalProperties` = **false**, `AnnotationIn` bor,
  `finalize` maydonlari `contract_date, dates, apply_paired_dates, layout,
  annotations`.

### E. Ochiq qolgan

1. `frontend/index.html` da favicon yo'q (kosmetik).
2. Backend grasiozli to'xtamaydi — `taskkill /F` kerak; band paytda restart
   xatari bor.
3. Kirill / o'zbekcha maxsus harflar uchun **PDF shrift joylashtirish** —
   qaror kutilmoqda (B bo'limidagi 422 cheklovi shundan).
4. `SignaturePage.tsx` hali **~1693 qator** (hook'larga bo'lish bajarilmagan);
   layout va annotationlar faqat `localStorage` da.

**Kod holati:** pytest **611 passed**, `tests/ui` **123/123**, `typecheck` va
`build` EXIT 0. Backend ikki marta qayta ko'tarildi: annotations v4 uchun
(launcher **29312** / uvicorn **28688**, eski **8480** o'rniga), so'ng E1
tuzatmasi uchun (joriy uvicorn **33536**, 02:36:04) — `/health` va `/ready`
200, jonli sxema tekshirilgan. Zaxiralar:
`backend\local.db.bak-20260924-0204-annotv4` va
`backend\storage_backup_20260924-0204-annotv4` (59 fayl); oldingi zaxiralar
ham qoladi.


## 2026-09-25 — Imzo/sanalarni to'rt tomonga «hammasini birdaniga» surish (frontend)

**Shikoyat:** imzo qo'yilgandan keyin belgilarni «birdaniga» faqat bir o'q bo'yicha surish mumkin, tepa-pastga emas.

**Reproduksiya (haqiqiy kontrakt `62f43665`, 44 bet, izolyatsiyalangan headless profil, faqat o'qish — GET + login'dan boshqa har qanday `/api` so'rovi bloklangan; bloklangan so'rov 0, skrinshot olinmagan):**
- Bitta slotni sichqoncha bilan surish to'rt tomonga ham ishlaydi (imzo rasmi / slot qutisi / sana matni): 40 px yuqoriga = dy +24.5 pt, pastga = −24.5, o'ngga = dx +24.5. Touch (CDP) bilan ham xuddi shunday, `pointercancel` yo'q.
- 52 ta slot qatorining hammasida ◀▶▲▼ yoqiq; `slotOffsetBounds` dy diapazoni hech bir slotda toraymagan (eng tori p37 [−200, 187.5]); sahifalar 792 va 817 pt.
- Demak (a) bounds, (b) o'q qulfi/belgi, (d) pointer-events gipotezalari **tasdiqlanmadi**. Mos kelgan yagona narsa (c): «hammasini birdaniga» boshqaruvi faqat global `All signatures` / `All dates` edi (`LayoutPanel.tsx`, eski 255-274-qatorlar) — faqat ▲▼, ±20 pt, 0.5 pt qadam, gorizontal guruhli surish umuman yo'q edi.

**Tuzatish (backendsiz, finalize kontrakti o'zgarmagan):**
- `lib/layout.ts` — `withSlotsShifted(layout, targets, {dx, dy})`: guruhdagi har bir slotning o'zining `slot_offsets_x_pt` / `slot_offsets_pt` qiymatiga bir xil qadam qo'shiladi, har biri ±200 pt va O'Z sahifasi chetiga clamp qilinadi; hech narsa o'zgarmasa o'sha obyektni qaytaradi.
- `components/LayoutPanel.tsx` — yangi «Move together» bloki: Signatures / Dates / Everything, har biri uchun ◀ ▲ ▼ ▶ (0.5 pt, Shift 2 pt); hech bir slot u tomonga siljiy olmasa tugma o'chadi. Global ±20 pt qatorlari va ularning aria-label'lari o'zgarmagan.
- `styles/signing.css` — `.layout-group`, `.shift-pad`.

**Tekshiruv:** `npm run typecheck` 0, `npm run build` 0; `python tests\ui\run_all.py` **134/134** (oldingi 123 + yangi `tests/ui/test_move_all.py` 11 ta: vertikal sudrash, rasm va sana matnini sudrash, sana ▲▼, guruhli surish 4 tomonga ×3 guruh, sahifa-cheti clamp, 792/817 aralash, finalize tanasi).
**Keyin (haqiqiy kontrakt, faqat localStorage):** «Everything» ▲×5 ▶×5 (Shift) → 52 slotning hammasida dy=10, dx=10, global ofsetlar 0/0; p1 imzo belgisi (221.29, 661.52) → (231.29, 651.52); «Signatures» ▼▶ bilan qaytarilgan → imzolar 0 ga, sanalar 10 da qoldi.
**Tozalash:** `%TEMP%\swm_vdrag` (repro skripti) o'chirildi; skrinshot/`page-*.yml` yaratilmagan; parol/token faylga yozilmagan.

## 2026-09-25 — Annotations v5 (backend): Sejda uslubidagi asboblar

**Doira:** faqat `backend` (frontendga tegilmadi, server qayta ko'tarilmadi — integratsiyada).

**Yangi turlar** (`kind` bo'yicha discriminated union, har biri `extra="forbid"`): `highlight`/`underline`/`strikeout` (`rects` 1..50), `ink` (1..20 path, 2..2000 nuqta, element <=4000, so'rov bo'yicha <=20 000), `rect`/`ellipse` (`fill` = faqat alfa 0.2), `line`/`arrow`, `note` (haqiqiy PDF `/Text` annotatsiya, `/F` 28 = Print|NoZoom|NoRotate), `image` (asset), `whiteout`. v4 turlariga ixtiyoriy `color`, hammasiga ixtiyoriy `group_id`. Palitra: black #000000, blue #1D4ED8, red #DC2626, green #16A34A, yellow #FACC15, orange #EA580C, purple #7E22CE — `white` YO'Q. Jami <=500 element.
**Z-tartib:** asl sahifa -> whiteout -> imzo/sana -> qolganlari (so'rov tartibida).
**Whiteout = redaksiya EMAS:** asl matn content stream'da qoladi (test bilan isbotlangan); audit `whiteouts` ro'yxatida joyi yoziladi.
**Yangi endpoint:** `POST /api/contracts/{id}/annotation-assets` (JSON `image_data` yoki multipart `file`; imzo gate'lari, fon olib tashlanmaydi, har doim RGBA PNG) va `GET .../annotation-assets/{asset_id}`. Kalit `contracts/{id}/asset-<hex>.png` — kontrakt o'chirilsa `delete_prefix` bilan ketadi. Kontrakt boshiga <=100 yuklash.
**Fayllar:** `app/schemas/contract.py`, `app/services/pdf_builder.py`, `app/api/signatures.py`, `app/api/deps.py`, `app/services/storage.py`, `app/services/audit.py`; test: `tests/test_api_annotations_v5.py` (yangi), `tests/test_api_annotations.py` (200->500 cheklov testi).
**Tekshiruv:** pytest **772 passed, 2 xfailed** (611 bazaviy + 161 yangi). 500 element + 20 000 ink nuqta finalize ~0.77 s.
**Ochiq xatar (OLDINDAN BOR, v5 emas):** `/Rotate` 90/270 sahifalarda umumiy overlay xaritalash barcha belgilarni (imzo, sana, v4 matn) noto'g'ri joyga qo'yadi — strict xfail bilan qayd etilgan.

## 2026-09-25 — W0: `SignaturePage` ichidan bo'lindi (frontend, UI o'zgarmagan)

**Reja:** `docs/WORKSPACE-PLAN.md` §5 W0 (1→9). Foydalanuvchi ko'radigan hech narsa o'zgarmadi (DOM, `data-testid`, klass, `aria-label`, matn, `localStorage` kalitlari); yagona yangi xulq — `swm.dates.<id>` (§7-8).

**Qator soni:** `SignaturePage.tsx` 1702 → **418**; `ReviewPage.tsx` 379 → 205; `DocumentEditor.tsx` 737 → 626.

**Yangi modullar (`frontend/src`):**
- sof: `lib/signing.ts` (turlar, konstantalar, `readLayoutRejection`, `signingErrorText`, `buildLayoutRows`), `lib/gate.ts` (`computeSigningGate`), `lib/reviewRules.ts` (`computeOverrideState`);
- I/O: `hooks/useContractDocument.ts`, `useSignatureLibrary.ts`, `useContractSignatures.ts` (uch sahifadagi dublikat fetch'lar shunga o'tdi);
- holat: `useSignatureSlots.ts`, `useDateValues.ts` (**persist `swm.dates.<id>`**, buzuq qiymat jimgina tashlanadi, hech narsa o'zi to'ldirilmaydi), `useSigningState.ts`, `useSlotFocus.ts`, `useSlotLayout.ts`;
- tanlovchi: `useSignatureChooser.ts` + `components/SignatureChooserHost.tsx` (Sign va Editor ikkalasi);
- `useBulkSigning.ts`, `useAutoFill.ts` — so'zma-so'z ko'chirildi (diff: faqat oxirgi bo'sh qator), `useCallback`/`useEffect` bog'liqliklari aynan;
- `useFinalize.ts` — bitta body quruvchi (`buildFinalizeBody`), `annotations` bo'sh bo'lsa kalit yo'q, `isAnnotationsUnsupported` shu yerda; tugma darvozasi va xato matnlari har sahifada avvalgidek;
- `overlay/SlotLayer.tsx`, `overlay/IssueLayer.tsx`, `panels/SignSection.tsx`, `panels/FinalizeBar.tsx`, `panels/FinalizeErrors.tsx`, `panels/ReviewSection.tsx`.

**Tekshiruv:** har 9 qadamdan keyin `npm run typecheck` 0, `npm run build` 0, `python tests\ui\run_all.py` yashil (1–2-qadam 134/134, 3-qadamdan 136/136). Yangi 2 test `test_layout_drag.py` da: `dates_survive_a_reload`, `a_broken_stored_date_is_dropped`; mavjud testlar o'zgartirilmagan.
**Autofill bir marta:** alohida probe (repo'dan tashqarida, mock `MockApi`): bulk POST = 1, kutubxona rasmi GET = 1, Undo → DELETE = 2, o'chiq bo'lsa POST = 0 — refaktordan oldin ham, keyin ham bir xil; nazorat mutatsiyasi (guard olib tashlanib, beqaror dep qo'shilganda) → 4 POST, probe qizardi → probe ikki marta ishlashni ushlaydi.

## 2026-09-25 — W1: yagona ish maydoni (review + imzo + muharrir bitta sahifada) (frontend)

**Reja:** `docs/WORKSPACE-PLAN.md` §5 W1. `/contracts/:id/review|sign|edit` endi **bitta** route elementi (`pages/ContractWorkspace.tsx` → `workspace/Workspace.tsx`); panel almashishi `navigate(replace)` — qayta mount yo'q, PDF qayta olinmaydi (test bilan isbotlangan), sanalar/tanlash/tarix saqlanadi. `/contracts/:id` → pending+reviewer `/review`, approved `/sign`, aks holda `/review`; noto'g'ri segment → `/review`.

**O'chirildi:** `pages/ReviewPage.tsx`, `pages/SignaturePage.tsx`, `pages/DocumentEditor.tsx` (zaxira: `%TEMP%\swm_w1_backup`).
**Yangi:** `workspace/{Workspace.tsx, useWorkspaceRoute.ts, useAnnotationTools.ts, PanelSections.tsx, useStableCallback.ts, state/useSelection.ts}`, `overlay/{PageOverlay.tsx (memo), FreeSignatureLayer.tsx}`, `panels/ObjectsSection.tsx`, `styles/workspace.css`, `pages/ContractWorkspace.tsx`.
**O'zgardi:** `App.tsx`, `AnnotationLayer.tsx` (erkin imzo chiqdi), `FieldHighlighter.tsx` (slot tarmog'i chiqdi), `EditorToolbar.tsx` (finalize tugmasi chiqdi, `disabledTools` + sabab), `SlotLayer.tsx` (`pressOpensChooser`), `SignSection`/`ReviewSection` («Open editor» chiqdi, «Go to review»/«Continue to signing» → `setPanel`), `FinalizeBar` (`editor-finalize`, sticky, «Still missing»), `FinalizeErrors` (`editor-error`), `useSlotFocus` (tanlash tashqaridan), `useSignatureChooser` (`saveStoredToLibrary` funksiya bo'lishi mumkin), `useSignatureImages` (har o'zgarishda yangi Map — memo uchun).

**Qoidalar:** slot faqat `.slot`; slot imzosi faqat `.placed-signature`, erkin imzo faqat `.swm-sig`; bitta finalize (sign darvozasi, body = dates + sahifaga moslangan layout + annotations); boshlang'ich asbob URL'dan (`/sign` → signature, boshqasi → select); text/date/check/cross yoqilganda slot qatlami `pointer-events:none`; tasdiqlanmagan kontraktda Signature asbobi va finalize o'chiq + «Contract not approved yet», lokal asboblar ishlaydi; approve → `/sign` qayta yuklanmasdan; Delete/strelkalar faqat tanlangan annotation'ga; avto-to'ldirish faqat `/sign` panelida ishga tushadi (review'ni ochish kontraktni imzolamaydi). O'ng panel akkordeon (Review / Signature & date / Position & appearance («Move together» bilan) / Objects), `swm.panel.sections`.

**Testlar:** `npm run typecheck` 0, `npm run build` 0, `python tests\ui\run_all.py` **148/148** (136 → +12 yangi `test_workspace.py`). `test_editor.py` da 6 tekshiruv ko'chirildi (zaiflashtirilmagan — `tests/ui/README.md` «Moved in W1»). Harness: `open_section`, `apply_dates_to_all` avval «sign» bo'limini ochadi. Skrinshotlar: `c:\PTG\.playwright-mcp\swm_workspace\` (faqat MockApi).
## 2026-09-25 — Integratsiya: backend v5 jonli + W1 ish maydoni jonli dalil (devops)

**Zaxira:** `backend/local.db.bak-20260925-0309-w1v5` (online backup API, `integrity_check` ok) + `backend/storage_backup_20260925-0309-w1v5` (83 fayl / 70 262 215 bayt, asl bilan teng).
**Restart:** eski launcher 37784 / uvicorn 33536 (09-24 02:36, faol ulanish 0) to'xtatildi → yangi launcher **31124** / uvicorn **14044** (09-25 03:09:53), `--reload`SIZ; log `%TEMP%\swm_w1v5\backend.log(.err)`. `/health`, `/ready` = 200 (127.0.0.1 va 10.31.0.58). Vite 13420 tegilmagan, :5173 = 200.
**openapi:** oldin — faqat `text|date|check|cross`, `annotation-assets` YO'Q; keyin — `Markup/Ink/Shape/Line/Note/Image/WhiteoutAnnotationIn` (hammasi `additionalProperties:false`) + `POST/GET .../annotation-assets`.
**Sintetik API E2E (ZZSYNTH):** 12 turdagi element bilan finalize 200; pypdfium2 8 px/pt o'lchovi — 10 geometrik element Δ≤0.065 pt, matn baseline 280.000 / chap 72.000 (pdfplumber), rang va alfa (highlight 0.35, rect fill 0.2) mos; whiteout oynasi 49 089 → 0 px, `pypdf` sarlavhani hamon ajratadi; note = `/Annot /Text`, `/F 28`, `/Rect [480 312 500 332]`; `/verify intact:true`; audit — faqat sanoq + `whiteouts` joyi, matn yo'q.
**Brauzer (izolyatsiyalangan headless Chromium, LAN IP):** 22/22 PASS — bitta workspace, panel almashishda `/file` 0 marta, tasdiqlanmaganda imzo/finalize o'chiq + sabab, matn ishlaydi, approve → `/sign` qayta yuklanmasdan, slot imzo + sana + finalize → completed. Konsol: faqat React Router future-flag ogohlantirishlari; 4xx/5xx 0.
**Topilgan (tuzatilmagan):** `frontend/src/lib/signing.ts:125` va `components/SlotToolbar.tsx:55` — `today()` = `toISOString()` (UTC) → 00:00–05:00 mahalliy vaqtda standart sana KECHAGI kun (jonli: 09-25 03:14 da 09/24/2026 yozildi). `pages/FinalContract.tsx:106` — audit vaqti SQLite'da naive UTC, `new Date()` uni mahalliy deb o'qiydi → 5 soat erta ko'rsatiladi.
**Tozalash:** 2 kontrakt DELETE, shablon `?purge=true`; jadvallar oldingi bilan teng (contracts 11, templates 1, signatures 53, contract_fields 1815, users 1, user_signatures 2), audit_logs +23 (audit o'chirilmaydi); storage 83 / 70 262 215 bayt. Skrinshotlar (faqat sintetik): `c:\PTG\.playwright-mcp\swm_w1_live\`.

**Direktor (2026-09-25):**
- Sana topilmasi foydalanuvchiga qo'yildi — **qaror: «Har safar so'rasin»** (avtomatik sana YO'Q, imzolashdan oldin sana qo'lda tanlanadi). Tuzatish alohida ishda.
- `c:\PTG\.playwright-mcp\` ildizidagi `page-*.yml` snapshot'laridan signwithme'ga tegishli **81 tasi o'chirildi** (2026-09-15 qarori: haqiqiy kontrakt matni bo'lishi mumkin bo'lgan sinov artefaktlari o'chiriladi; tekshiruvda ularning birortasida `Docusign`/`RMR TRANSPORTATION`/`Envelope ID` belgisi topilmadi). Qolgan 119 tasi boshqa loyihalarniki — tegilmadi.
- Saqlangan 17 PDF / 800 sahifa skani: `/Rotate` sahifa **0** — backend v5 hisobotidagi burilgan-sahifa xaritalash xatosi hozirgi hujjatlarga ta'sir qilmaydi (ochiq qoladi).

## 2026-09-25 — W2: asbob registri + suzuvchi asboblar paneli + yagona undo tarixi (frontend)

**Reja:** `docs/WORKSPACE-PLAN.md` §2.3, §2.5, §2.6, §5 W2+W2b. Alohida nusxada (`_staging\w2`, Vite :5183) qurildi va sinaldi, keyin direktor sinxronladi.

**Asbob registri:** `workspace/tools/{types,registry,shared}.ts` + `tools/{select,text,date,check,cross,signature}/index.ts`; `import.meta.glob('./*/index.ts')`, guruh+order bo'yicha. `handleCanvasClick` dagi `if` zanjiri asboblarning `onPageClick` iga bo'lindi. Yangi asbob = bitta papka. `ToolId` §2.5 dagi 19 ta asbobni qamraydi, ro'yxatda 6 tasi.

**Suzuvchi panel:** `workspace/toolbar/{FloatingToolbar,PropertiesBar}.tsx`, `useFloatingPanel.ts`. Default `docked-top` (1600 px da bitta qator, 55.5 px, sahifa y=194, avvalgidek). Grip bilan sudrash → `floating`. Yuqori chetga (≤24 px) → docked, chap/o'ng chetga → vertikal. Kamida 48 px ustunda qoladi (ResizeObserver clamp). Yig'ish. Holat `swm.toolbar` da saqlanadi (buzuq qiymat e'tiborsiz qoldiriladi). Klaviatura: roving tabindex, Alt+T, Esc, grip strelkalari 10/50 px, Enter; V T D K X S (input ichida va Ctrl/Alt bilan o'chiq). ≤640 px da pastki bar (44 px, surish yo'q, xususiyatlar pop-over'da). Joy yetmasa avval asbob yorliqlari yashiriladi (1600 px da faqat glif, ~1920 px da yorliq bilan), keyin oxirgi guruhlar «More ▾» ga o'tadi. Tasdiqlanmagan holatdagi sabab — bir qatorli badge. `EditorToolbar.tsx` o'chirildi, barcha testid'lar saqlangan.

**Rang (v5):** `lib/annotations.ts` — palitra (black #000000, blue #1D4ED8, red #DC2626, green #16A34A, yellow #FACC15, orange #EA580C, purple #7E22CE; oq yo'q), `color?` text/date/check/cross uchun. Qora = kalit yo'q (v4 tanasi bayt-bir xil). Faqat palitra nomi qabul qilinadi. Preview aynan shu hex'lar bilan chiziladi. Har asbobning o'z rangi (`swm.toolProps`). Tanlangan annotation rangini o'zgartirish = 1 undo qadami.

**Yagona tarix:** `workspace/state/useDocumentHistory.ts` (kanallar `annotations`, `layout`; 100 qadam; slider yurishi 1.2 s ichida bitta qadamga birlashadi). `useAnnotations` tarixni tashqaridan oladi. W2b: `useContractLayout`/`useSlotLayout` → `commit` (surish, resize, stepper, slider, «Move together», Reset) va `replace` (sahifa-cheti clamp, finalize oldi fit, noma'lum slot tozalash — qadam emas). Ctrl+Z/Ctrl+Y slot surishni ham qaytaradi. Server yozuvlari tarixda yo'q.

**Memo:** 3 betli hujjatda 20 qadamli surish — oldin har harakatda 3/3 sahifa qayta render bo'lardi, endi faqat surilayotgan sahifa (qolganlari 0; slot qo'yib yuborilganda layout o'zgargani uchun bittadan). Hisoblagich: `window.__swmOverlayRenders` (faqat dev).

**Sinxronlash (direktor):** nusxa yaratilgandan (03:09:39) keyin jonli `frontend/src` va `tests/ui` da o'zgarish **yo'q** edi (ziddiyat yo'q). 15 yangi + 12 o'zgargan fayl ko'chirildi, `EditorToolbar.tsx` o'chirildi; hash bo'yicha nusxa bilan bir xil (farq 0). Ustiga yozilgan 13 faylning zaxirasi: `_staging\w2_prelive_backup\`.

**Tekshiruv (jonli papkada, sinxronlashdan keyin):** `npm run typecheck` 0, `npm run build` 0, `python tests\ui\run_all.py` **167/167** (148 + yangi `test_toolbar.py` 19; layout 30, move 11, warnings 14, override 15, viewer 16, search 11, rotation 10, editor 29, workspace 12, toolbar 19). Eski testlar o'zgartirilmagan. Skrinshotlar: `c:\PTG\.playwright-mcp\swm_w2\` (MockApi).

## 2026-09-26 — Avtomatik sana olib tashlandi + audit vaqti UTC
- Qaror: «Har safar so'rasin» — hech qayerda avtomatik sana yo'q. today() (UTC, 00:00–05:00 Toshkentda kechagi sana) o'chirildi: signing.ts, SlotToolbar («Today» tugmasi), useDateValues, useAutoFill, useToolState, tools/date.
- Barcha sana kiritishlar bo'sh boshlanadi; sanasiz urinish qo'yilmaydi → fokus + «Choose the date first». Date asbobi ham.
- Avto-to'ldirish endi faqat imzo qo'yadi; «Choose the date for N date fields»; Undo faqat imzolarni qaytaradi.
- Backend: sanani o'zi qo'ymaydi (contract_date=None, apply_paired_dates frontda false; `validators.py:171` dagi `date.today()` faqat guvohnoma muddati tekshiruvi). Vaqtlar: `models/types.py` `UTCDateTime` — SQLite naive UTC endi +00:00/Z bilan qaytadi (oldin `2026-09-24T16:51:10` → brauzer mahalliy deb o'qib 5 soat xato). Frontda `lib/time.ts` (naive = UTC) zaxira; FinalContract, Dashboard, ReviewQueue, ContractUpload, MySignatures, useSignatureLibrary shu yordamchida.
- Testlar: tests/ui 176/176 (+9 test_dates, 02:30 Toshkent soat qotirilgan), pytest 775 passed / 2 xfailed (+3).
- **Sinxronlash (direktor):** `_staging\wdate` → jonli, ziddiyat yo'q (nusxa 09-25 17:34 dan keyin jonli o'zgarmagan), 24 fayl, hash farqi 0; zaxira `_staging\wdate_prelive_backup\` (22 fayl). Jonli papkada typecheck 0, build 0, `tests/ui` **176/176**.
- 🚩 **Uzilish:** mashina 2026-09-25 18:02 da qayta yongan, `:5173` va `:8000` avtomatik ko'tarilmagan → ilova ~29 soat ishlamagan. 2026-09-26 22:53 da qo'lda ko'tarildi: Vite PID 652, uvicorn PID 37172 (loglar `%TEMP%\swm_servers\`), `/ready` 200, LAN 10.31.0.58 orqali 200; jonli openapi'da v5 bor, yangi `UTCDateTime` yuklangan. Avto-ishga tushirish yo'q — ochiq.

## 2026-09-26 — Backend: embedded shriftlar (Liberation) + qalin/kursiv (contract v6) — TUGADI
- Liberation 2.1.5 (SIL OFL 1.1) 12 TTF: backend/app/assets/fonts/liberation/ + LICENSE; manba/SHA-256: app/assets/fonts/SOURCES.md.
- Yangi app/services/fonts.py: 6 oila (Helvetica, Times-Roman, Courier + LiberationSans/Serif/Mono), bold/italic → yuz; TTFont subset embedding (/FontFile2, AAAAAA+), bir marta lock bilan ro'yxat.
- Sxema (additiv): font/date_font enum +3; text/date `bold`/`italic`, layout `date_bold`/`date_italic` (StrictBool, default false); check/cross'da bold/italic 422. Audit layout'da date_bold/italic faqat true bo'lsa.
- Belgi tekshiruvi: Liberation — TTF cmap (glif yo'q → 422), + control/format, birikuvchi belgi, RTL → 422; base-14 — WinAnsi, xabar o'zgarmagan. Kirill, o'zbek oʻ/gʻ (U+02BB), ş ç € — 12 yuzda bor; emoji/✓/CJK yo'q.
- Topilma: Liberation Sans Italic/BoldItalic va barcha Serif'da .notdef BO'SH — yo'q glif jim yo'qolardi; cmap tekshiruvi buni to'sadi.
- Metrika: Liberation vs Helvetica/Times/Courier ≤0,017 % (istisno: ¯ ± µ · ÷ Sans/Serif'da farq qiladi).
- Hajm: +17–20 KB/yuz (3 oila ≈ +58 KB); finalize builder 4→7–14 ms; birinchi ro'yxat ≈157 ms.
- bold/italic yo'q yoki false → PDF bayt-ba-bayt v5 (testlar bilan). tests/test_api_fonts.py 136 test; pytest 911 passed, 2 xfailed.
- **Jonli (direktor):** backend 22:53 da (shrift ishi tugamasdan) ko'tarilgan edi → 23:01:12 da qayta ko'tarildi (faol ulanish 0), uvicorn PID 38800; `/ready` 200, LAN 200; jonli openapi'da `LiberationSerif` va `bold` bor.

## 2026-09-27 — WE: PDF'dagi mavjud matnni tahrirlash + shrift tanlash (frontend)
**Reja:** WORKSPACE-PLAN §3, §7-6, §7.1. Nusxada (`_staging\we`, Vite :5186) qurildi va sinaldi.
- Matn qatlami: pdf.js 4.8.69 TextLayer (`--scale-factor`), faqat edit-text asbobida, faqat oynadagi sahifalarda (≤6), burilgan sahifada yo'q; qidiruv va overlay orasida. Geometriya bitta manba (`lib/textRuns.ts runGeometry`) — qidiruv o'zgarmadi.
- pdf.js bo'laklari qatorga birlashtiriladi (baseline, uslub, bo'shliq); ikki ustun alohida qator. Hover ramka.
- `edit-text` (E): qatorga bosish → asl matn bilan tahrirchi; shrift avtomatik (serif/sans/mono → Liberation, bold/italic nomdan, o'lcham run'dan). Commit = whiteout (qator ±0.5 pt) + text (baseline) bitta `group_id`, BITTA undo qadami; o'zgarmagan qator → hech narsa; qayta bosish shu guruhni ochadi; whiteout qulf, matn suriladi; o'chirish/«Revert» butun guruhni o'chiradi; Obyektlar'da «Edited text» qatori.
- Shriftlar: Text/Date/edit-text uchun 6 oila + B/I; `bold`/`italic` faqat true bo'lsa yuboriladi (v5 tanasi bayt-bir xil); Liberation TTF `public/fonts/liberation` + `@font-face`, preview metrik-mos.
- Belgi tekshiruvi oldindan (backend qoidalaridan generatsiya: `scripts/gen_font_coverage.py` → `lib/fontCoverage.ts`), NFC; Helvetica'da kirill/oʻ, istalgan shriftda emoji → aniq xabar va yechim.
- Toolbar: 7-asbob va B/I bilan 1600 px'da sig'ishi uchun compact rejimda maydon yorliqlari yashiriladi; FloatingToolbar'dagi eskirgan kenglik xatosi va full/compact tebranishi tuzatildi.
- Tekshiruv: typecheck 0, build 0, tests/ui 196/196 (+20 test_edit_text). 49 bet: heap 19.8 → 22.8 MB, ≤5 qatlam / 4200 span, Select'ga qaytganda tozalanadi.
- **Sinxronlash (direktor):** nusxa (09-26 23:02:47) dan keyin jonli o'zgarmagan; hash bo'yicha 49 fayl (23 yangi, jumladan 12 TTF + LICENSE/AUTHORS), agent ro'yxati bilan to'liq mos; zaxira `_staging\we_prelive_backup\` (26 fayl); hash farqi 0. Jonli papkada typecheck 0, build 0, `tests/ui` **196/196**; `/fonts/liberation/*.ttf` LAN orqali 200.
- Ochiq: frontend `MAX_ANNOTATIONS` hali 200 (backend 500; har tahrir 2 element); Layout paneli slot sanasi uchun hali 3 shrift; faqat-o'chirish (bo'sh tahrir) yo'q — W5 whiteout asbobi; qatorlarni klaviatura bilan tahrirlash yo'q.

## 2026-09-27 — WE jonli uchdan-uchgacha: edit-text + shriftlar haqiqiy backend bilan (devops)
- **Zaxira:** `backend/local.db.bak-20260927-we-e2e` (online backup API, `integrity_check` ok). Serverlarga tegilmadi (Vite 652, uvicorn 38800), kod o'zgarmagan.
- **Sintetik (ZZSYNTH):** reportlab PDF — Times-Roman 12 «Contractor Name: John Sample», Helvetica-Bold 11 «Truck Number: 1234», Courier 10 «Ref: ABC-001» + imzo/sana qatori; shablon = kontrakt = shu PDF; API approve (override kerak bo'lmadi, can_sign true).
- **Brauzer (izolyatsiyalangan headless Chromium, LAN IP):** 26/26 PASS — E asbobni yoqadi; Times qatori → asl matn, `LiberationSerif`, 12; «Jane Oʻzbek» qabul qilindi; Helvetica-Bold → `LiberationSans` + B, ko'k rang tahrirchi ochiqligida tanlandi; Ctrl+Z/Ctrl+Y bitta qadamda butun guruh (whiteout+text); Courier qatori o'zgartirmasdan Enter → 0 element; Text asbobi LiberationMono italic; imzo chizildi, sana bo'sh boshlandi va qo'lda tanlandi; finalize → `/final`.
- **Finalize tanasi:** 2 juft `whiteout`+`text` (umumiy `group_id`), `font` LiberationSerif / LiberationSans(`bold:true`,`color:blue`) / LiberationMono(`italic:true`); `bold:false`/`italic:false` hech qayerda yuborilmagan.
- **Yakuniy PDF (pypdfium2 8 px/pt, pypdf, pdfplumber):** whiteout oynalarida asl siyoh 21 140 → 0 va 20 926 → 0 px (yangi Liberation matn obyektlari olib tashlanib o'lchandi); baseline Δ 0.000 pt, chap chet Δ 0.000 pt (piksel chetida ≤0.125 pt); `AAAAAA+LiberationSerif`, `AAAAAA+LiberationSans-Bold`, `AAAAAA+LiberationMono-Italic` — `/FontFile2` + `/ToUnicode`; ekstraksiyada yangi matn va U+02BB bor, asl matn ham hamon bor (whiteout vizual — kutilgan); ko'k piksellar o'rtachasi (29.3, 78.3, 216.0), neytral-qora 0; Courier qatori piksel-farq 0; `/verify intact:true`; audit `contract_finalized` → `whiteouts` 2 joy, matn yo'q.
- **Konsol:** faqat React Router future-flag ogohlantirishlari; pageerror 0; 4xx/5xx 0.
- **Tozalash:** kontrakt DELETE, shablon `?purge=true` → 200; jadvallar oldingi bilan teng (contracts 13, templates 1, signatures 63, contract_fields 2145, users 1, user_signatures 2), audit_logs 2071 → 2083 (+12, o'chirilmaydi); storage 96 fayl / 82 025 868 bayt — teng. Skrinshotlar (faqat sintetik): `c:\PTG\.playwright-mcp\swm_we_live\`. Skriptlar: `%TEMP%\swm_we_e2e\` (parol/token yozilmagan).
- Topilgan muammo: yo'q.

## 2026-09-27 — W3: Highlight / Underline / Strikeout + «Highlight all matches» + 500 chegarasi + slot sanasi shrifti (frontend)
**Reja:** WORKSPACE-PLAN §2.4, §2.5, §3, §5 W3, §8.1. Nusxada (`_staging\w3`, Vite :5187) qurildi va sinaldi.
- Asboblar: highlight (H), underline (U), strikeout (C — S imzoda, X/K belgilarda; "cross out"); umumiy `tools/markup.ts`, har biri o'z papkasida. Matn qatlami `select` rejimida (faqat spanlar, qator tugmalari yo'q), `workspace--text-select`; burilgan sahifada qatlam yo'q → asbob o'chiq.
- `text/useTextSelection.ts`: tanlangan belgilar → span → `TextRun.item` → run; x/w run kengligi Liberation metrikasi bilan bo'linadi (kerningsiz), y/h qator ink qutisi; bir qator birlashadi (ustunlar alohida); clamp, ≥1 pt; >50 quti → bir nechta annotation. Bir necha sahifali tanlash = har sahifaga bittadan, BITTA undo qadami. Tanlov tozalanadi.
- Preview = PDF: highlight palitra hex'i, alfa 0.35; underline/strikeout chizig'i max(0.75, h·0.08) pastda/o'rtada; tartib whiteout → markup → matn/belgi. Butunligicha surish (sahifa ichida), rang, Delete, undo, Obyektlar ro'yxati.
- Finalize: `{id, kind, page, rects[{x_pt,y_pt,w_pt,h_pt}], color}` — rang doim yuboriladi (standart yellow/red).
- «Highlight all matches» (`pdf-search-highlight-all`): qidiruv qutilaridan, bitta qadam, takror yo'q, 500 da hammasi-yoki-hech-narsa. Qidiruv qutilari endi harf soni emas, glif kengligi bilan (Liberation) bo'linadi (eski usulda 3.7 pt xato bor edi).
- `MAX_ANNOTATIONS` 200 → 500 (test satrlari yangilandi). Slot sanasi: 6 oila + B/I; `date_bold`/`date_italic` faqat true — eski tana bayt-bir xil.
- Toolbar: 10 asbob bilan 1600 px'da sig'ishi uchun compact rejimda asbob chetlari toraytirildi, Undo/Redo so'zi yashirildi (aria-label/title'da bor); overflow 0.
- Topilma tuzatildi: `measureTextPt` kerning qo'shardi (PDF'da yo'q, 16 pt'da ~0.5 pt).
- Ochiq: `lib/annotationKinds/*` bo'linmasi qilinmagan (annotations.ts monolit); annotation hit qutilari markup/matn asbobi yoqilganda bosilmaydi (Select orqali); `usePdfViewer.ts`/`Dashboard.tsx` izohlarida avvaldan kodlash buzilishi (`Â§`) — tegilmadi.
- **Sinxronlash (direktor):** staging Vite 34232 to'xtatildi; nusxadan keyin jonli o'zgarmagan; 31 fayl (5 yangi) hash bo'yicha, agent ro'yxatiga mos; zaxira `_staging\w3_prelive_backup\` (25). Frontend yuboradigan markup tanasi haqiqiy backend `ContractFinalization` sxemasidan o'tdi (ortiqcha `x_pt` → rad, kutilgandek). Jonli papkada typecheck 0, build 0, `tests/ui` **215/215**.

## 2026-09-27 — W4: Qalam / to'rtburchak / oval / chiziq / strelka / note / rasm + toolbar ochiluvchi guruhlari (frontend)
**Reja:** WORKSPACE-PLAN §2.4, §2.5, §5 W4, §7-1, §8.1. Nusxada (`_staging\w4`, Vite :5188) qurildi va sinaldi.
- `overlay/ToolCaptureLayer.tsx` (`.ws-capture`, overlay'ning birinchi bolasi, faqat drag asbobida): pointer capture, sahifaga clamp, Esc bekor, preview faqat o'z sahifasida (boshqa PageOverlay 0 render), bitta gesture = bitta undo.
- Asboblar: ink (P), rect (R), ellipse (O), line (L), arrow (A), note (N), image (M). Shift: kvadrat/doira, 45°. Ink RDP 0.5 pt; server chegaralaridan oshsa bir necha elementga bo'linadi (bitta qadam); 20 000 nuqta — aniq rad.
- Preview = PDF: markazlangan stroke, fill alfa 0.2, butt uchlar, strelka boshi 6·3·stroke; note ikonka + tanlanganda matn; rasm qutini aynan to'ldiradi.
- Tahrir: rect/ellipse/image 8 tutqich (rasm nisbatni saqlaydi, Shift erkin), line/arrow 2 uch, ink/note butunligicha; PropertiesBar `stroke`/`fill`; Delete, undo/redo, Objects.
- Note: ko'p qatorli, WinAnsi oldindan tekshiriladi. Rasm: 2 MB / PNG-JPEG / 4000 px oldindan, bir marta JSON `image_data`, preview GET (bitta), 409/413/422 so'z bilan; tasdiqlanmaganda o'chiq.
- Toolbar: «Markup ▾» / «Shapes ▾» / «Insert ▾» — qatorda 10 tugma, 1600 px'da overflow 0; `tool-<id>` menyu ichida saqlangan. Yo'lda: «More ▾» ga tushgan guruh qolib ketish xatosi, menyu fokusining note oynasini yopishi, note popup'ning o'ng chetdan chiqishi, rasm preview'ning StrictMode'da ikki marta yuklanishi tuzatildi.
- `lib/annotationKinds/*` — faqat yangi turlar (eskilari `annotations.ts` da, barrel dispatch; eski turlar ~200 test va v4/v5 bayt-bir xillik bilan bog'langani uchun ko'chirilmadi).
- Tekshiruv: typecheck 0, build 0, tests/ui 238/238 (+23 test_draw). Finalize tanasi backend `ContractFinalization` dan o'tdi, 7 negativ variant rad etildi.
- Ochiq: saqlangan annotation sahifa o'lchami ma'lum bo'lgach qayta clamp qilinmaydi (`clampAnnotation` hech qayerda chaqirilmaydi — oldindan bor, barcha turlarga tegishli) → finalize'da 422 xatari.
- **Sinxronlash (direktor):** nusxadan keyin jonli o'zgarmagan; 43 fayl (21 yangi, 22 o'zgargan) hash bo'yicha, agent ro'yxatiga mos; zaxira `_staging\w4_prelive_backup\`. Jonli papkada typecheck 0, build 0, `tests/ui` **238/238**.

## 2026-09-27 — W5: Whiteout + Initsial + saqlangan annotation clamp'i (frontend)
**Reja:** WORKSPACE-PLAN §5 W5, §7-1/6/7, §8.1. Nusxada (`_staging\w5`, Vite :5189) qurildi va sinaldi.
- **Whiteout (W, `redact`, Edit text'dan keyin):** sudrab quti (Shift kvadrat), bitta undo qadami; oq noshaffof, ekranda ingichka kulrang punktir chegara (faqat CSS — tanada yo'q, PDF'da oddiy oq quti). Erkin whiteout 8 tutqich, surish, Delete, undo; Objects'da «Whiteout · p.N». Asbob tavsifida va birinchi whiteout'da bir martalik eslatma (`swm.whiteout.noticed`): faqat yashiradi, asl matn faylda qoladi. Edit-text'ning guruhli whiteout'i qulf, tutqichsiz, chegarasiz (o'zgarmadi). `lib/annotationKinds/whiteout.ts`.
- **Z-tartib tuzatildi (preview):** whiteout endi `overlay/WhiteoutLayer.tsx` da — slot va imzolardan PASTDA (oldin annotation SVG tagida, ya'ni ekranda imzolar USTIDA edi; PDF'da server tartibi to'g'ri edi). Preview = chop tartibi; piksel testi bilan isbotlangan.
- **Initsial (I, `fill`, Signature'dan keyin):** erkin imzo endpointi (`field_id: null`), `serverWrite` (tasdiqlanmaganda o'chiq + sabab), undo'da yo'q. SignatureModal («Apply initials»); quti 60×24 pt ichida rasm nisbati bilan. Objects'da «Every page»: har boshqa sahifaga bitta POST, pastki-o'ng burchakka nisbatan, `runPool` 3 ta parallel, progress, qisman xato hisoboti, qayta bosganda faqat yetishmagan sahifalar. «Remove all initials». Ajratish: quti ≤ 60×24 YOKI `swm.initials.<id>`; cheklov — faqat UI yorlig'i, server/audit/PDF uchun oddiy imzo. Kutubxonada `label: "Initials"`.
- **Clamp tuzatmasi:** `clampAnnotationsToPages` + `store.replace` — saqlangan annotation sahifa o'lchami kelgach sahifaga tortiladi (undo qadami emas, xabar bir marta) → 422 `annotation_outside_page` xatari yopildi.
- **Toolbar:** 19 asbob / 12 tugma; 1600 px'da (12 imzo hisobi bilan ham) overflow 0.
- **Tekshiruv:** typecheck 0, build 0, tests/ui 251/251 (+13 test_redact). Finalize tanasi backend `ContractFinalization` dan o'tdi (guruhli variant ham), 6 negativ variant rad etildi.
- **Ochiq:** `page > pageCount` bo'lgan saqlangan annotation clamp qilinmaydi; `sig-count` initsiallarni imzo sifatida sanaydi.
- **Sinxronlash (direktor):** nusxadan keyin jonli o'zgarmagan; 32 fayl (7 yangi, 25 o'zgargan) hash bo'yicha; zaxira `_staging\w5_prelive_backup\`. Jonli papkada typecheck 0, build 0, `tests/ui` **251/251**. Rejadagi 19 asbobning hammasi jonli.

## 2026-09-27 — W3/W4/W5 jonli uchdan-uchgacha: barcha asboblar haqiqiy backend bilan (devops)
- **Zaxira:** `backend/local.db.bak-20260927-w345-e2e` (online backup API, `integrity_check` ok). Serverlarga tegilmadi (Vite 652, uvicorn 38800), kod o'zgarmagan.
- **Sintetik:** ZZSYNTH 3 sahifali reportlab PDF (p.1: Helvetica 12 qatorlar «Alpha Bravo Charlie», «Delta Echo Hotel», «Foxtrot Golf», «SECRET-LINE-12345» + imzo/sana bloki; p.2–3 matn). 1-kontrakt — asosiy sinov; 2-kontrakt (o'sha shablon) — imzo+whiteout kesishuvi va initsial preview↔PDF o'lchovi.
- **Approve'dan oldin (12/12):** Image va Initials o'chiq, sabab «Contract not approved yet», M/I tugmalari yoqmaydi; H (sichqoncha bilan tanlash), P, W lokal ishlaydi; uchalasi Ctrl+Z bilan qaytarildi.
- **Approve'dan keyin (37/39 + 2-kontrakt 5/6):** highlight (sichqoncha sudrash), underline (ikki marta bosish), strikeout, ink, rect (fill, 3 pt), ellipse, line, arrow (Shift → 45.000°), ko'p qatorli note, rasm (M → fayl dialogi → JSON `image_data` bitta POST → 90×45 → burchakdan 130×65, 2:1 saqlandi), 2 whiteout, initsial → «Every page» (p.2, p.3) → p.3 tasviri GET 200 → «Remove all initials» (confirm, 3 DELETE) → qayta qo'yildi va Every page; slot imzosi, sana qo'lda (bo'sh boshlandi) → finalize → `/final`. 2 ta FAIL — sinov skriptining o'zi: Ctrl+Z oxirgi whiteout'ni qaytardi (to'g'ri xatti-harakat, initsial qoldi) va sanoq 13 emas 12 bo'lishi kerak edi.
- **Yakuniy PDF (pypdfium2 8 px/pt, pypdf):** 11 element brauzer pt geometriyasidan max Δ 0.00–0.11 pt; highlight alfa 0.350, ostidagi matn qora pikselining 94% saqlangan, ekstraksiyada bor; rect fill alfa 0.204, stroke 3.0 pt; ellipse ichi oq; rasm burchaklari to'g'ri (TL qizil, TR yashil, BL ko'k, BR qora), box Δ 0; whiteout: asl 36 755 px → 0, matn ekstraksiyada bor (kutilgan); note `/Annots /Text`, `/Rect` Δ 0, `/Contents` 2 qator, `/F 28`; 3 sahifada initsial rasmi; `/verify intact:true`; audit `annotation_asset_added` (sha256, 120×60) + `contract_finalized.annotations.whiteouts` joy bilan, matn/note yo'q. 2-kontrakt: slot imzosi whiteout ustida ko'rinadi (rasmlarsiz render whiteout ichida 0 px), slot imzosi preview↔PDF Δ 0.01 pt.
- **Konsol:** faqat React Router future-flag; pageerror 0; 4xx/5xx 0.
- **Tozalash:** 2 kontrakt DELETE, shablon `?purge=true` → 200; jadvallar teng (contracts 13, templates 1, signatures 63, contract_fields 2145, users 1, user_signatures 2), audit_logs 2083 → 2115 (+32); storage 96 fayl / 82 025 868 bayt — teng, `asset-*.png` qoldig'i 0. Skrinshotlar: `c:\PTG\.playwright-mcp\swm_w345_live\`. Skriptlar: `%TEMP%\swm_w345_e2e\`.
- **Topilgan muammolar (tuzatilmagan):**
  1. Drag boshida editor-note o'chadi → hujjat sudrash paytida 74 px (178% da 41.5 pt) yuqoriga sakraydi, box noto'g'ri tushadi (whiteout 19 pt o'rniga 60.5 pt, ikki marta takrorlandi). `frontend/src/workspace/tools/drawing.tsx:64-68` (`startGesture` → `ctx.notify(null)`) + `frontend/src/workspace/Workspace.tsx:996-1000` (note hujjat ustida oqimda). Barcha drag asboblariga tegishli.
  2. Initsial (har qanday erkin imzo) preview'dan PDF'da 6.32 pt pastda: preview `object-fit: contain` bilan markazlaydi (`frontend/src/styles/editor.css:526-531`), PDF siyoh pastini box pastidan `SIGNATURE_INK_GAP` yuqoriga qo'yadi (`backend/app/services/pdf_builder.py:471-472, 655-667, 1092-1097`). Qo'shimcha: box xom pad nisbati (3:1) bilan hisoblanadi (`frontend/src/hooks/useInitials.ts:119-123`), server esa kesilgan 426×38 (≈11:1) saqlaydi → rasm 60×20 box'ning faqat 5.35 pt balandligini egallaydi.

## 2026-09-28 — Tuzatish: sudrashda sahifa sakrashi + erkin imzo/initsial preview↔PDF (frontend)
Nusxa `_staging\wfix` (Vite :5190). Jonli W3/W4/W5 E2E'da topilgan 2 xato.
- Xato 1: note hujjat ustida oqimda edi; drag boshida `ctx.notify(null)` uni o'chirardi → sahifa 74 px (178% da 41.5 pt) sakrardi, pointOf joriy rect'ni o'qigani uchun box shuncha katta saqlanardi (4 drag asbobi). Tuzatish: note endi sahifalar ustida suzadi (`EditorNote`, `PdfViewer floating`, × bilan yopiladi); finalize xatolari / comparison xatosi / template ogohlantirishi bitta blokda, sahifada bosish ushlab turilganda balandligi muzlatiladi (`useGestureLayoutLock`) — asinxron banner faqat qo'yib yuborilgach chiqadi.
- Xato 2: erkin imzo preview'i `object-fit: contain` bilan markazlanardi; PDF chapga tekislaydi, global scale beradi, siyoh pastini bbox pasti − 1 − signature_offset_pt ga qo'yadi. Tuzatish: `freeSignatureRect` (signatureRect ustida, `_ink_metrics` brauzer nusxasi `lib/signatureInk.ts`), global offset/scale qo'llanadi; slot'larga tegilmadi. Initsial box endi server saqlaydigan qirqilgan rasm nisbatida (`prepare_signature_image` qoidasi joylashdan oldin; shaffof bo'lmagan fotoda rasmning o'z o'lchami).
- Tekshiruv: typecheck 0, build 0, tests/ui 255/255 (+4 test_geometry — eski kodda 0/4; test_redact'da 3:1 → server qirqish nisbati, zaiflashtirilmagan).
- Jonli (ZZSYNTH, :8000, pypdfium2 8 px/pt), oldin → keyin: whiteout 19 pt Δ 41.5 → 0.000 pt (sakrash 73.8 → 0 px); rect Δ 41.5 → 0.000; erkin imzo (offset 2, scale 1.1) preview↔PDF 18.18 → 0.375 pt; initsial p.1–3 6.75 → 0.401 pt. Zaxira `backend/local.db.bak-20260928-wfix`; tozalash: 2 kontrakt DELETE, shablon ?purge=true; sanoqlar teng, audit_logs +29; storage 96 / 82 025 868 — teng.
- Ochiq: note endi sahifa yuqorisini qisman yopadi (× bilan yopiladi); slot preview siyoh ostidagi bo'sh qatorlarni hisobga olmaydi (server qirqqan rasmda ular yo'q — tegilmadi).
- **Sinxronlash (direktor, 2026-09-28):** nusxadan keyin jonli o'zgarmagan; 19 fayl (4 yangi) hash bo'yicha; zaxira `_staging\wfix_prelive_backup\`. Jonli papkada typecheck 0, build 0, `tests/ui` **255/255**. Serverlar 09-26 dan beri uzluksiz (Vite 652, uvicorn 38800).

## 2026-09-29 — F1a: Sejda uslubidagi toolbar + muharrir yuqori paneli (qobiq, frontend)
Nusxa `_staging\f1a` (Vite :5191). EDITOR-V2-PLAN §7, §9 F1a. Yangi backend funksiyasi yo'q.
- Toolbar spetsifikatsiya tartibida: Select | Text ▼ (Add text T, Edit text E, Date D) | Forms ▼ (Check K, Cross X) | Images ▼ (Image M) | Sign ▼ (Signature S, Initials I) | Whiteout W | Annotate ▼ (Highlight H, Underline U, Strikeout C, Draw P, Note N) | Shapes ▼ (Rect R, Ellipse O, Line L, Arrow A) | Undo | Redo. 19 asbob, barcha harf va `tool-<id>` saqlandi. Hali yo'q asboblar (Links, Filled square, forma maydonlari, Detect, Stamp, Redact…) ko'rsatilmaydi — soxta tugma yo'q.
- `styles/sejda.css` faqat `.workspace` ichida: #0099FF, #E9E9E9 kanvas + oq sahifa, 1px #D0D0D0, 4px, 34px, 13px; inline SVG ikonlar (`toolbar/icons.tsx`, aria-label + title). Boshqa sahifalar o'zgarmadi (test bilan).
- Menyular `position: fixed`, viewport ichida, scroll/resize/zoom'da qayta joylashadi; klaviatura saqlandi. Undo/Redo qatorda.
- `EditorTopBar.tsx`: kontrakt nomi, «Download PDF» (final bo'lsa `/api/download/{id}/final`, aks holda `/file`), «Generate final contract» (`editor-finalize` shu yerga, gate o'sha). Edit PDF / Save / Cancel — B1/F1b ga, `session` slot tayyor, UI'da yo'q.
- Top bar + toolbar 40+40 px bitta blok, viewer strip 30 px (yarim piksel 0.5 pt snap'ni buzardi).
- Eski testlar zaiflashtirilmadi — README «Changed in F1a».
- Ochiq: 1600 px da qator compact (faqat ikon); Sejda skrinshotlari kelsa vizual moslik qayta tekshiriladi.
- 🚩 Jarayon: agent monitor bilan ~18 soat «Waiting» holatida qotdi; direktor 09-29 18:14 da aniqlab majburan yakunlatdi.
- **Sinxronlash (direktor):** nusxadan keyin jonli o'zgarmagan; 39 fayl (4 yangi) hash bo'yicha; zaxira `_staging\f1a_prelive_backup\`. Jonli papkada typecheck 0, build 0, `tests/ui` **269/269**.

## 2026-09-29 — Editor v2 B1 (backend fondamenti) — JONLI
- **Qurilish** (`_staging\b1`): yangi jadvallar `documents`, `document_versions`, `document_pages`, `document_drafts`; ustunlar `signatures.page_uid`, `user_signatures.source`; alembic 0005 + `scripts\migrate_local_editor_v2.py`. PyMuPDF dvigateli `app/services/pdf_engine/` (`import pymupdf` faqat `adapter.py`). `text.replace`/`text.delete`: yadro-redaksiya qo'shni gliflar bbox'idan tashqariga suriladi (aks holda `____` ham o'chardi; iloji bo'lmasa qayta chiziladi + `neighbor_glyphs_redrawn`), oq quti yo'q, Liberation almashtirish (`substituted_from`), wrap/shrink/overflow va to'qnashuv → 422. MuPDF `0 0 Td` xatosi: `0.000123` → redaksiya → 0 ga qaytarish (spike'dagi `1e-4` haqiqiy paketda 3 betda 36 px qoldirgan; endi 44/44 bet 0 px). `TextWriter` o'rniga `insert_text` (CropBox≠MediaBox'da TextWriter 8 pt siljidi). API `/api/contracts/{id}/document` (versiyalar, sahifalar, matn modeli, markup GET/PUT, draft + lease, preview, atomik commit, `commit_request_id`). Review: content commit → pending, override tozalanadi, tahrirlangan hududdagi tuzatishlar tashlanadi, reparse; imzolar bo'lsa 409 `signatures_present` yoki `discard_signatures`. Q1 markup-content qoidasi markup PUT'da. Final joriy versiyadan; legacy yo'l bayt-bir xil. Faqat markup commit yangi versiya yaratmaydi. B2 ga: `/SWMPageId`, finalize `_resolve_dates` sahifa xaritasi.
- Haqiqiy shablon nusxasida (44 bet): 3 ekstraktor ✔, Δ 0.000, qator tashqarisi 0 px, boshqa betlar 0 px, 350–500 ms, 2.97→2.54 MB, preview 103 ms (kesh 1.8 ms).
- pytest nusxada **1004 passed / 2 xfailed** (911 + 93).
- **Deploy (direktor, 2026-09-29 21:00):** faol ulanish 0 → backend to'xtatildi → 41 fayl hash bo'yicha ko'chirildi (zaxira `_staging\b1_prelive_backup\backend\`; staging `.env` ko'chirilmadi) + `frontend/src/api/document.ts` → migratsiya: `--dry-run` (2 ustun + 4 jadval kutilmoqda) → haqiqiy: integrity ok/ok, sanoqlar oldin=keyin (contracts 14, signatures 75, contract_fields 2310, audit_logs 2167, …), zaxira `backend\local.db.bak-20260929-210100-editorv2` → backend qayta ko'tarildi 21:01:10 (uvicorn 32980), `/ready` 200, LAN 200, openapi'da 10 ta `/document*` yo'li. To'xtab turish ~1 daqiqa.

## 2026-09-29 — Editor v2 B1 jonli uchdan-uchgacha (API, ZZSYNTH) (devops)
- **Zaxira:** `backend/local.db.bak-20260929-b1-e2e` (online backup API, `integrity_check` ok). Kod o'zgarmagan, serverga tegilmagan (uvicorn 32980, `/ready` 200). Skriptlar: `%TEMP%\swm_b1_e2e\` (parol/token yozilmagan).
- **Sintetik PDF:** 1 bet — Chrome-uslubidagi 2 qator (`(AAAA) Tj 0 0 Td (BBBB) Tj 0 0 Td 30 0 Td (CC) Tj`, `(ZZ-LEFT) Tj 0 0 Td 90 0 Td (ZZ-RIGHT) Tj`), rangli katakda «Carrier: ABC Transportation LLC», o'chiriladigan qator, `____` ustidagi qiymat, imzo+sana bloki. Slotlar `p1_contractor_signature`, `p1_date` topildi.
- **Natija 60/63 PASS** (3 FAIL: 2 = topilma (1), 1 = topilma (3)). Dangasa yaratish v1 + capabilities, versions/pages/text (7 blok), 404'lar; draft 201 → PUT 3 op (replace×2, delete) 260 ms → eski revision 409 `revision_conflict` → bir xil payload idempotent 200 → `preview?hide=b3` 1 bet 79 ms; imzo → commit 409 `signatures_present{count:1}` → `discard_signatures` 201 v2 175 ms, `review_reset`, pending, imzo o'chdi; replay → o'sha v2 (`replayed`), versiyalar [1,2]. Audit `signature_removed{superseded_by_version}`, `signatures_superseded`, `review_reset_by_edit`, `document_version_committed` — matn yo'q.
- **v2 o'lchovi (pypdfium2 8 px/pt):** 3 ekstraktorda eski matn yo'q, yangisi bor; Δ chap/dum/baseline 0.0000 pt (pdfplumber + PyMuPDF); qator tashqarisi 0 px (thr 48); Chrome qatorlari 1e-4 pt (tuzatma), thr 0 da 533 px ≤2 kulrang daraja; rangli katakda oq piksel 0, fon (217,237,255) saqlandi; `____` 30/30, chiziq polosasi 0 px.
- **Final:** qayta approve → imzo (`page_uid` qatorda) + sana → finalize 200, v2 dan (yangi matn bor, eski yo'q), rasm XObject 1 = imzo 1, `/verify intact:true`, `source_version_no: 2`. Keyin draft 409 `contract_completed`. Tokensiz 401 (6), noma'lum kontrakt 404 (3), yozuv yaratilmadi. Eski kontrakt faqat o'qildi: detail/file 200, `document: null`, qator yaratilmadi.
- **Topilgan (tuzatilmagan):** (1) qisman dum qayta yozish (`services/pdf_engine/text_edit.py:420-466` + `adapter.py:368` `insert_text`) qatorni ikkiga bo'ladi: v2 matn modelida `b3 'Carrier: '` + `b6 'Premier Trucking Group Inc.'` (blok ID'lari qayta raqamlanadi), pypdf/PyMuPDF ikki qator deb o'qiydi (pdfplumber bir qator) → ikkinchi tahrir va qidiruvga ta'sir. (2) `compare` slotlarida `page_uid` yo'q (`schemas/comparison.py:39-53`); `date-fields` da bor. (3) pdfplumber finalda sanani `__/__/____` bilan aralashtiradi — legacy, v2 emas.
- **Tozalash:** kontrakt DELETE, shablon `?purge=true` → 200; sanoqlar teng (contracts 14, signatures 75, contract_fields 2310, …), audit_logs 2167 → 2188 (+21); `documents`/`document_versions`/`document_pages`/`document_drafts` 0/0/0/0; storage 109 fayl / 86 975 112 bayt — teng, `version-*.pdf` 0.

## 2026-09-29 — B1.1: qisman tahrirdan keyin qator bo'linishi + 2-tahrirda ko'rinmas matn — JONLI
- Ildiz 1: matn modeli MuPDF rawdict'ni oqim tartibida olardi; `_partial` dumni oqim oxiriga yozadi → 'Carrier: ' + 'Premier…' ikki blok, keyingi ID'lar qayta raqamlanardi.
- Tuzatma: `pdf_engine/lines.py` — bir baseline'dagi bo'laklar mantiqiy qatorga (Δbaseline≤0.25·size, o'lcham nisbati≥0.85, bo'shliq≤0.6·size — 1.0 da haqiqiy paketning 4-betidagi jadval kataklari qo'shilardi; vertikal chiziq/probel-faqat bo'lak to'sadi); ID'lar geometrik (baseline, chap chet); `TextSpanOut.synthetic`.
- 🚩 Ildiz 2 (topildi+tuzatildi): 2-tahrirda `SWM<hash>` shrift aliasi subsetlangan eski shriftni qayta ishlatardi → yangi gliflar GID0 (ko'rinmas, U+0000; haqiqiy shablonda "LLC" → `\x00\x00\x00`). Endi har yozuvga yangi alias. Har qanday ikkinchi tahrirga ta'sir qilardi.
- `_partial` uslubni o'zgargan belgidagi span bilan solishtiradi; eski draft op'lari aniq bbox chetlari bilan yechiladi; preview hide eski ID'larni ham yashiradi; `compare` `signature_slots[].page_uid`.
- O'lchov: haqiqiy paket (44/44/49 bet) blok/qator soni o'zgarmadi (asl PDF'da 0 birlashtirish); 46/46 qisman tahrir → bitta qator, ID barqaror, qayta tahrir Δ0. pypdf/pdfium dumni hali alohida qator o'qiydi (B2: oqimga splice). Cheklov (B2): paragrafdagi bitta qatorni TO'LIQ almashtirish blokni bo'ladi.
- pytest: agentda 1021 + 1 beqaror; direktor qayta yurgizdi → **1022 passed / 2 xfailed, 0 fail**.
- **Deploy (direktor, 22:08):** faol ulanish 0 → to'xtatildi → 10 fayl (ro'yxatdan tashqari farq 0), zaxira `_staging\b11_prelive_backup\backend\`, hash farqi 0 → qayta ko'tarildi 22:08:50 (uvicorn 40396), `/ready` 200, LAN 200, openapi'da `synthetic` bor. Migratsiya kerak emas edi.

## 2026-09-29 — F1b: tahrirlash sessiyasi + mavjud matnni haqiqiy tahrirlash (frontend) — JONLI
Nusxa `_staging\f1b` (Vite :5192, proksi → jonli :8000). EDITOR-V2-PLAN §3, §6, §7, §9 F1b; backend B1 + B1.1.
- Sessiya (`workspace/session/`): useEditSession + useDraftAutosave (debounce 1500 ms / maxWait 10 s, bitta PUT navbat, backoff 1/2/5/10 s, pagehide keepalive ≤64 KB, `swm.draft.<cid>` zaxira), lease har 45 s. Top bar: [Edit PDF] / [Save changes] [Cancel editing] + editor-save-status; edit rejimida Sign ▾ va «Generate final contract» o'chiq («Save or cancel your edits first»); capabilities bo'yicha tugmalar.
- Save = commit (commit_request_id); 409 signatures_present → «N signature(s) will be removed and the contract must be signed again» → discard_signatures; «Saved as version N» + «Review reopened». Cancel = DELETE draft. Recovery (Resume / Restore from this browser / Discard), lock + Take over, konflikt modal (Reload / Download my changes JSON).
- Edit text v2: server matn modeli (`useServerTextModel` — yagona adapter, versiya+hash; B1.1 `synthetic` span uslub namunasi emas), TextBlockLayer, TextBlockEditor (server shriftida, birinchi baseline PDF'nikida), preview?hide → setPageOverride + mediana-rang maska; Enter/blur → text.replace, bo'sh → text.delete; toshish zonasi (Shrink / Allow overlap / Keep editing). TextFloatingToolbar (Link yo'q — F2), FontPicker (3 Liberation, substitute chip). WE whiteout+text yo'li olib tashlandi (foydalanuvchi: oq quti TAQIQ); `test_edit_text.py` dan 11 eski tekshiruv almashtirildi (README «Replaced in F1b»); Objects'da «Convert to real edit».
- Markup serverda: markupWire (page → page_uid), useMarkupSync (server bo'sh → PUT + kalit `.migrated`; farqli → Import / Keep; 404 → eski localStorage). History 'ops' kanali.
- Jonli E2E (B1.1 bilan): asosiy 15/15 + qayta tahrir 8/8 — v2/v3, bitta blok/ramka, 3 ekstraktorda ikkinchi tahrir, U+0000 yo'q; Δ baseline/chap 0.000, qator tashqarisi 0 px, katakda oq piksel 0; muharrir↔eksport o'ng chet 0.096 pt; preview↔eksport ≤1 px. Zaxiralar `local.db.bak-20260929-f1b-e2e`, `-f1b-e2e-b11`; tozalash: sanoqlar teng.
- Ochiq: textbox va More fonts (B2), Link (F2); lease/pagehide UI testi yo'q; B/I blok bo'yicha (tanlangan qismga emas); Duplicate = legacy text markup.
- **Sinxronlash (direktor):** nusxadan keyin jonli o'zgarmagan; 30 fayl (16 yangi) hash bo'yicha, zaxira `_staging\f1b_prelive_backup\`. (Birinchi urinishni ruxsat tekshiruvi to'xtatdi — skriptdagi yo'l naqshi o'chirish buyrug'i deb o'qildi; hech narsa bajarilmagani tekshirildi, qayta — o'chirish buyruqlarisiz.) Jonli papkada typecheck 0, build 0, `tests/ui` **288/288**.

## 2026-09-29 23:07 — Qayta yonish (ikkinchi marta)
- Mashina 23:07:26 da qayta yondi → Claude sessiyasi uzildi, B2 agenti to'xtadi, `:5173` va `:8000` o'chdi. 23:22 da direktor ikkala serverni qo'lda ko'tardi (loglar `%TEMP%\swm_servers\`), LAN 200. B2 ishi yo'qolmagan (`_staging\b2`, 92 fayl, oxirgisi 22:58) — agent SendMessage bilan davom ettirildi.
- Foydalanuvchi qarori: avto-start qo'yilmaydi — ilova keyinroq alohida serverga ko'chiriladi. Shungacha reboot'dan keyin qo'lda ko'tariladi.

## 2026-09-29 — Editor v2 B2 (backend: ops, sahifalar, v7 markup, final pipeline, galereya, eksport, typed imzo, shriftlar) — JONLI
- Nusxa `_staging\b2` (reboot'dan keyin davom ettirildi). Ops: redact (haqiqiy: 3 ekstraktor + stream baytlarida yo'q, `0 0 Td` himoyasi), image.remove, link.add/update/remove (http(s)/mailto, ichki havola page_uid bo'yicha, maqsad o'chsa olib tashlanadi + warning; javascript:/file:/data: rad), page.insert/delete/move/rotate; /SWMPageId.
- Sahifa xaritasi to'liq: extract/template_match page_map, `page_removed_with_slots` (critical, override mumkin, FMCSA emas), /compare `page`=pozitsiya + page_uid, date-fields, finalize sanalari; slotli sahifani o'chirish faqat reviewer/admin (403 page_has_slots). page.rotate/link.* = structure.
- pdf_builder `/Rotate` 90/270 tuzatildi — v5 strict xfail'lar PASS. Markup v7 (textbox, stamp, image2, shape2, ink2, square_mark), final_pipeline, `annotations_in_two_places` 422. assets (galereya, soft delete, kontraktga nusxa), export_jobs, typed imzo (Great Vibes/Allura/Sacramento, OFL, matn saqlanmaydi), shriftlar Arimo/Carlito/Caladea/DejaVu Sans+Serif/Amiri + GET /api/fonts; SOURCES.md.
- Haqiqiy 44 betlik nusxa: insert+delete(slotli)+move+rotate → saqlangan betlarda 152/152 maydon bir xil, o'chgan betning 13 maydoni missing, slotlar to'g'ri pozitsiyada, commit ~200 ms.
- pytest nusxada **1187 passed, 0 xfailed, 0 fail**.
- **Deploy (direktor, 23:35):** faol ulanish 0 → to'xtatildi → 92 fayl (89 backend, 3 frontend; 27 TTF) hash bo'yicha, zaxira `_staging\b2_prelive_backup\` → migratsiya dry-run (`assets`, `export_jobs`) → haqiqiy: integrity ok/ok, sanoqlar teng, zaxira `backend\local.db.bak-20260929-233523-editorv2` → qayta ko'tarildi 23:35:23 (uvicorn 27072), LAN 200; openapi: assets 3, fonts 1, exports 3 yo'l.
- Keyingi: jonli regressiya (eski imzolash oqimi + /Rotate) + PII temp tozalash; B3 (formalar) ∥ F2 (qolgan UI) boshlandi.

## 2026-09-29 — B2 jonli regressiya + B2 API uchdan-uchgacha (ZZSYNTH, API) (devops)
- **PII:** `%TEMP%\swm_b2_real` (5 fayl, 5.0 MB), `swm_b2_mig` (3 baza nusxasi, 9.5 MB), `swm_b2_fonts` (97), `swm_b2_tsc` (202) o'chirildi, `Test-Path` = False. Mazmuni ochilmadi.
- **Zaxira:** `backend/local.db.bak-20260929-b2-e2e` (online backup API, integrity ok). Kod o'zgarmagan, server qayta ko'tarilmagan (uvicorn 27072, `/ready` 200). Skriptlar `%TEMP%\swm_b2_e2e\`.
- **Eski oqim (legacy, hujjatsiz):** A (rot 0) 10/10 — siyoh pasti chiziqdan 1.000 pt yuqorida (= `SIGNATURE_INK_GAP`), sana maydonida, v4 matn chap 0.25 / baseline 0.125 pt, XObject 1 = imzo 1, `/verify intact`, `documents` qatori yaratilmadi. R (/Rotate 90 + 270) 17/19: imzo tik turadi, x to'g'ri, v4 matn 0.25/0.125 pt, sana maydonida, XObject 2 = 2, intact; **2 FAIL** — imzo chiziqdan **11.1 pt** yuqorida (quyida 1).
- **B2 API (C, 3 bet) 59/59** (39 + 20; oradagi to'xtash sinov skriptidagi ustun nomi xatosi, server emas): redact (3 ekstraktor + barcha stream'lar + xom baytlarda yo'q, qora quti), insert/delete/move/rotate → v2 [p3,p1,yangi], page_uid barqaror, yangi bet origin null + /Rotate 90; link URI + ichki (page_uid) API'da va PDF'da; /images; /compare pozitsiya + page_uid (p1→2, keyin 1); slotli betni o'chirish (admin; uploader-sinov qilinmadi — boshqa foydalanuvchi yo'q) → `page_removed_with_slots` critical, override bilan approve; galereya → nusxa → galereyadan DELETE → image2 baribir chizildi; markup PUT → review_reset; eksport va finalda textbox/stamp/shape2 (≤1.5 pt)/image2 (0.0 pt), imzo siyohi chiziqdan 1.000 pt, XObject final 3 = eksport 2 + imzo 1; server markup + body → 422 `annotations_in_two_places`; `/api/fonts` + `?category=signature` (3); typed imzo PNG, `source=typed`, matn DB/auditda yo'q; audit `markup_source: server`, `source_version_no: 3`.
- **Tozalash:** typed imzo, 3 kontrakt, 3 shablon (purge) → 200; soft-delete bo'lgan galereya qatori (API hard-delete yo'q) id bo'yicha qo'lda o'chirildi. Sanoqlar teng (assets 0, export_jobs 0, documents/versions/pages/drafts 0), audit_logs 2221 → 2278 (+57); storage 109 fayl / 86 975 112 bayt — teng, `version-*.pdf` 0, asset fayli 0.
- **Topilma (tuzatilmagan, B2 dan OLDIN ham shunday):** 1) Asl /Rotate 90/270 betda chiziq aniqlanmaydi → `rule_y=None` → imzo va sana bbox pastiga tushadi (chiziqdan 11.1 pt / ~3 pt yuqorida): `app/services/rule_locator.py:110-115` (rotated → faqat vektor chiziq; `____` glif chiziq emas) + `app/services/pdf_builder.py:1169` (`bbox.y1` fallback). 2) Galereya soft-delete qatori abadiy qoladi (`app/api/assets.py:212-213`) — test tozalashda sanoq teng bo'lmaydi.

## 2026-09-30 — B2 jonli regressiya natijasi (direktor)
- Eski imzolash oqimi (hujjatsiz kontrakt, `/Rotate 0`) **10/10** — imzo siyohi chiziqdan aynan 1.000 pt yuqorida, sana/v4 matn joyida, XObject = imzo soni, `intact:true`. B2 API **59/59** (redact 3 ekstraktor + xom baytlarda yo'q; page ops bitta commit 103 ms, page_uid barqaror; link URI+ichki; /compare page+page_uid; slotli sahifa o'chirish → critical; galereyadan o'chirilgan rasm finalda chiziladi; v7 markup eksport+finalda; imzo bir marta; `annotations_in_two_places` 422; typed imzo matni DB/auditda yo'q). Zaxira `local.db.bak-20260929-b2-e2e`. PII temp papkalari (`swm_b2_real`, `swm_b2_mig`, `swm_b2_fonts`, `swm_b2_tsc`) o'chirildi.
- Ochiq (B2 dan OLDIN ham bor): asl `/Rotate 90/270` sahifada `____` gliflari chiziq sifatida topilmaydi → `rule_y=None` → imzo chiziqdan 11.1 pt, sana ~3 pt yuqorida (`rule_locator.py:110-115`, `pdf_builder.py:1169`). Hozirgi hujjatlarda burilgan sahifa 0 (09-25 skani).
- Ochiq: galereyadan soft-delete qilingan `assets` qatorlari abadiy qoladi (`api/assets.py:212-213`) — API orqali to'liq o'chirib bo'lmaydi.
- Ochiq (beqaror test): `test_api_fonts.py::test_a_v5_body_builds_the_v5_bytes` to'liq to'plamda yiqildi (1186/1187), alohida 3/3 va fayl bilan 172/172 o'tdi → tartibga bog'liq (ehtimol jarayondagi shrift ro'yxati holati PDF baytlariga ta'sir qiladi). B1.1 dagi `test_a_v4_body…` beqarorligi bilan bir sinf — tekshirilishi kerak.
- Jonli typecheck 0, build 0.

## 2026-09-30 — Editor v2 B3: interaktiv PDF formalari (backend) — JONLI
- Nusxa `_staging\b3`. `pdf_engine/forms.py`: text, multiline, dropdown, checkbox (export_value), radio (ota-maydon + kid'lar, `/V` va `/AS` PDF nomi — spike [S5] `bad xref` hal), signature (bo'sh). Ops `field.add/update/remove` (structure), `field.value` (content → review qayta ochiladi); 409 `field_name_taken`/`radio_value_taken`; tab tartibi `/Annots` + `/Tabs /S`; o'chirilgan bet bilan maydon ham ketadi.
- `GET …/versions/{no}/fields` (original AcroForm ham; Docusign yashirin imzo maydoni `hidden`), `POST …/detect-fields` (field_detector.analyze_pages, saqlanmaydi).
- Flatten = `bake`: final (faqat ko'rinadigan vidjet bo'lsa — jonli 21 PDF dan 20 tasida Docusign yashirin maydoni bor, legacy final bayt-bir xil) va eksport `flatten_forms`.
- §11 Q2: email kontraktori faqat `field.value` draft (403 `fill_only`), `capabilities.can_fill`. Audit faqat sanoqlar.
- O'lchov (44 betli haqiqiy paket nusxasi): 20 maydon + 18 qiymat apply 0.46 s, flatten 0.05 s, maydondan tashqarida 0 px (44/44), pdf.js 4.8.69 21 maydonni o'qiydi, detect 44 bet ~6 s.
- pytest nusxada 1239 passed + 1 skipped, 0 fail. Migratsiya yo'q.
- Cheklov: vidjet shrifti standart-14 (WinAnsi, kirill 422); burilgan betda vidjet ko'rinishi burilmaydi; reparse vidjet qiymatini o'qimaydi; bir betlik preview field op'siz.
- **Deploy (direktor, 00:14):** faol ulanish 0 → 20 backend fayl hash bo'yicha, zaxira `_staging\b3_prelive_backup\` → qayta ko'tarildi 00:14:53 (uvicorn 9712), LAN 200, openapi'da 8 ta `fields` yo'li. `frontend/src/api/document.ts` dagi B3 bloki jonli papkaga KO'CHIRILMADI — F2 nusxasiga birlashtirish uchun F2 agentiga berildi (to'qnashuvni oldini olish).

## 2026-09-30 — B3 dan keyin jonli pytest: 1238/1240
- Yiqilganlar: `test_api_annotations.py::test_without_annotations_the_document_is_byte_for_byte_what_it_was[annotations1]`, `test_api_annotations_v5.py::test_a_v4_body_without_colour_builds_the_v4_bytes`. Bu uchinchi holat — har safar boshqa **bayt-bir xillik** testi, faqat to'liq to'plamda, alohida o'tadi → tartibga bog'liq qonuniyat (ehtimol jarayon global holati: shrift ro'yxatga olish tartibi va h.k.). Hujjat mazmuniga/joylashuvga ta'siri kuzatilmagan (jonli E2E'lar PASS). Ildiz sababni topish va ishlab chiqarish kodini deterministik qilish uchun alohida agent (`_staging\bytes`) ishga tushirildi.

## 2026-09-30 — Editor v2 F3 backend: «to'ldirish uchun ulashish» havolalari + submissions — JONLI
- Nusxa `_staging\f3b` (18 fayl: 9 yangi, 9 o'zgargan). `share_links` (token_hash sha256, token saqlanmaydi, versiya qotirilgan, 72 soat / maks 14 kun, max_submissions) + `form_submissions` (qiymatlar `pii` bilan shifrlangan). Alembic 0007 (0006 B2 da band edi), `migrate_local_editor_v2.py` kengaytirildi.
- Ommaviy `/api/share/{session,file,submissions}`: peer-IP LAN (XFF e'tiborsiz), barcha yomon token → bir xil 404, IP boshiga 10 daq/20 xato → 429, `no-store` + `no-referrer` + `nosniff`, kontrakt ID chiqmaydi; tekshiruv = B3 `validate_ops` + required; 422 da qiymat/ruxsatsiz maydon nomi qaytmaydi.
- Ichki: create/list/revoke (token va url faqat create javobida), submissions list (sezgir maydonlar maskalangan), accept → `field.value` draft (boshqa draft → 409 `draft_locked`) → Save → review pending, reject. Audit tokensiz, hashsiz, qiymatsiz.
- pytest nusxada 1261 passed + 1 skipped, 0 fail.
- 🚩 Topilma: Vite `:5173` `/api` ni `localhost:8000` ga proksilaydi → backend peer'ni 127.0.0.1 ko'radi → **LAN cheklovi proksi orqali chetlab o'tiladi**; hozir amalda `:5173`/`:8000` faqat LAN'da ekanligiga tayanadi. Boshqa serverga ko'chirishda ishonchli-proksi (trusted proxy + real IP) mantig'i MAJBURIY.
- **Deploy (direktor, 00:47):** faol ulanish 0 → 18 fayl + `frontend/src/api/share.ts` (yangi, alohida) → migratsiya dry (`share_links`, `form_submissions`) → haqiqiy: integrity ok/ok, sanoqlar teng, zaxira `backend\local.db.bak-20260930-004702-editorv2` → qayta ko'tarildi 00:47:03 (uvicorn 2428), LAN 200, openapi'da 8 share/submissions yo'li, noma'lum token → 404.

## 2026-09-30 — PDF bayt-moslik testlari: ildiz sabab va tuzatma (faqat test)
- Belgi: bayt-bir xillik testlari to'liq to'plamda goh-goh yiqilgan, alohida o'tgan. Ildiz: tartib EMAS, devor soati — pypdf `:` ni oktal `\072` qilib yozadi (`pypdf/generic/_base.py:756-761`) → `/CreationDate (D\072…Z)`; testdagi neytrallovchi `rb"\(D:\d{14}Z\)"` (`tests/test_api_annotations.py:715`) uni HECH QACHON topmagan → ikki build sekund chegarasini kesib o'tsa 1 bayt farq.
- Ishlab chiqarish deterministik: 600 build (std-14 + Liberation, GC aralashtirish, ~10 s real soat) → neytrallovchidan keyin 0 farq. `/CreationDate` = imzolash vaqti (hash ichida) — ataylab qoldirildi, `app/` o'zgarmadi.
- Tuzatma: pattern `rb"/CreationDate \(D(:|\\072)\d{14}Z\)"` + `count == 1` sharti. Nusxada to'liq pytest oddiy va teskari tartibda — ikkalasida 0 fail.
- Sinxron (direktor): `backend/tests/test_api_annotations.py` (zaxira `_staging\bytes_prelive_backup\`); jonli papkada ta'sirlangan 4 fayl: **462 passed**.
- 2026-09-30 01:2x: foydalanuvchi talabi «agentlarni ko'paytir, tugat» → parallel: F2 (davom), F2b formalar UI (`_staging\f2b`), F3-UI ulashish (`_staging\f3ui`), backend polish (`_staging\bpol`: burilgan sahifa rule_y, reparse vidjet qiymatlari, soft-delete asset purge), QA qabul to'plami (`tests\acceptance\`, 18 band). Foydalanuvchi: «localda ko'tar, keyin oxirda» — hozircha shu kompyuterda ishlaydi, boshqa serverga ko'chirish oxirida.

## 2026-09-30 — F2: muharrirning qolgan interfeysi (frontend) — JONLI
Nusxa `_staging\f2` (Vite :5193, proksi → jonli :8000). EDITOR-V2-PLAN §7.2–7.4, §9 F2; backend B2 (+B3 tiplari birlashtirildi).
- Hammasi FAQAT hujjat muharriri bor serverda (`data-editor-v2`); eski serverda F1a/F1b aynan.
- Toolbar: Select | Text ▼ | Links | Forms ▼ | Images ▼ | Sign ▼ | Whiteout ▾ Redact | Annotate ▼ | Shapes ▼ | Undo | Redo; 1600 px da overflow 0 (14 asbob).
- Add text = v7 textbox (ko'p qator, paste NFC, wrap = dvigatel layout'i, align, line spacing, surish, o'lcham, dublikat); Links (URL/sahifa, tahrir/surish/o'lcham/o'chirish, navigatsiya yo'q, matn toolbar'ida Link); Filled square Q; Stamp (sana bo'sh); Delete original image; Redact (haqiqiy); Freehand highlight F; Show/hide (faqat ekran); Style ▾; galereya; Sign ▼ eskizlar + Type tab; More fonts… (35 shrift fayli `public/fonts/` ga litsenziyalari bilan); sahifa paneli (insert/delete dialogi/drag/rotate, undo); eksport dialogi «Include annotations (N)»; finalize server markup bo'lsa annotations yubormaydi.
- Topilib tuzatildi: sahifa ko'chirilgandan keyin markup boshqa page_uid'ga tushishi mumkin edi; jonli markup sync «loading»da qotishi.
- Jonli E2E (ZZSYNTH) 7/7: 2 link /URI + GoTo; insert+delete+rotate → slotlar to'g'ri betda; redaksiya 3 ekstraktor + stream'da yo'q; textbox baseline Δ −0.002 pt; typed imzo slotda; galereya rasmi o'chirilgandan keyin ham final'da; eksportda markup bor. Zaxira `local.db.bak-20260930-f2-e2e`; tozalash: sanoqlar teng.
- Ochiq: Links tanlangan matndan (faqat matn toolbar'i orqali); kutilayotgan real burishda annotatsiyalar qayta proyeksiyalanmaydi; justify ragged-right. Q1 eslatma: tasdiqdan keyin textbox/rasm review'ni qayta ochadi (foydalanuvchi qarori).
- **Sinxronlash (direktor):** nusxadan keyin jonlida faqat `frontend/src/api/share.ts` qo'shilgan (F3 backend; nusxada yo'q — tegilmadi). 108 fayl (64 yangi, 44 o'zgargan) hash bo'yicha, zaxira `_staging\f2_prelive_backup\`. Jonli papkada typecheck 0, build 0, `tests/ui` **329/329**. F2b va F3-UI agentlariga yangi asosga rebase buyurildi.

## 2026-09-30 — F2b: interaktiv PDF formalari (frontend) — JONLI
Nusxa `_staging\f2b_rebased` (F2 jonlidan keyin olingan, Vite :5194 → jonli :8000). EDITOR-V2-PLAN §3 field.*, §7.4, §9 F2, §11 Q2; backend B3.
- Forms ▾ «Add new form fields»: Text field, Multiline, Dropdown, Radio, Checkbox, Signature box (sudrash = field.add, noyob nom, klik = odatiy o'lcham) + Detect form fields; faqat editor bor serverda.
- Tahrir: 8 tutqich, surish/o'lcham, Delete/strelkalar, Duplicate, bitta 'ops' undo; FieldProperties; band nom → «Rename to X_2», radio guruhga qo'shiladi; op'lar kanonik tartibda (remove → update → add → value, `fieldModel.ts`).
- To'ldirish (Select): HTML inputlar → field.value; kirill/emoji oldindan rad; kontraktor (can_fill) to'ldirib Save qiladi.
- Tab tartibi (drag / ↑↓); Detect faqat ekrandagi betlar, Accept / Reject / Accept all (bitta qadam). Asl AcroForm maydonlari ko'rinadi/to'ldiriladi; Docusign yashirin maydoni chizilmaydi.
- Nusxada typecheck 0, build 0, tests/ui 342/342. Jonli E2E (ZZSYNTH): UI 19/19 + final 14/14 (widget 0, AcroForm 0, qiymatlar 3 ekstraktorda o'z qutisida). Zaxira `local.db.bak-20260930-f2b-e2e`.
- Ochiq: kontraktor + imzolar → 409 → 403 fill_only (UI alohida aytmaydi); properties dock o'ng panel ustida (<1024 px sahifani yopishi mumkin); sahifa op'lari bor draft ko'rinishida widget ikki marta chiqishi sinalmagan.
- **Sinxronlash (direktor):** nusxadan keyin jonli o'zgarmagan; 24 fayl (17 yangi) hash bo'yicha, zaxira `_staging\f2b_prelive_backup\`. Eski `_staging\f2b` (F2'dan oldingi) sinxronlanmadi.
- (Tuzatish: 09-30 dagi F2 sinxron yozuvidagi «~02:3x» vaqti noto'g'ri — F2 01:23 dan oldin sinxronlangan; `f2b_rebased` ziddiyatsiz o'tdi.)
- Backend polish (`_staging\bpol`) ham 01:49 da jonli: 11 fayl, migratsiyasiz; burilgan sahifa rule_y 11.25→1.000 pt, reparse AcroForm vidjet qiymatlari, `asset_purge.py`; uvicorn 27992. Lokal smoke (faqat o'qish) 13/13.

## 2026-09-30 — QA: spetsifikatsiyaning 18 qabul testi — avtomatik jonli to'plam (`tests\acceptance\`)
- Skriptlar: `run_acceptance.py` (backend venv) + `acc_ui.py` (tizim Python, Playwright); har band `api_tNN` / `ui_tNN`; UI asbobi yo'q bo'lsa PENDING. Dalil: pypdf/pdfplumber/PyMuPDF + pdfium 8 px/pt + pdf.js 4.8.69; 100 % va 209 % zoomda ±0.5 pt. Kod o'zgarmagan.
- Yakuniy yurish `runs\20260930-021733`: **350/370**; API 12/18 PASS, UI 16/18 PASS, PENDING 0. Joylashuv: ramkalar 0.016/0.010 pt, obyektlar ≤0.394 pt, imzo siyohi +1.000 pt (UI'da chizilgan imzo +1.5 pt), guruh +3 / slot +2 aniq.
- Topilgan 3 nuqson (→ tuzatish agenti `_staging\qafix`): (1) qisman tahrirda yangi qism oqim oxiriga yoziladi → pypdf/PyMuPDF o'qish tartibi buziladi (`text_edit.py:476-529`, `adapter.py:405-414`); (2) ✥ Move jimgina yo'qoladi — commit 201, matn joyida (`text_edit.py:505-506`, `:546-553`); (3) DejaVu (8) va Amiri (2) ToUnicode yaroqsiz (PyMuPDF 1.28.2 `insert_font`, 5 xonali hex) → pypdf `extract_text` yiqiladi (`adapter.py:405`, `:594`).
- Kuzatuv: haqiqiy burilgan sahifada ekrandagi annotatsiyalar burilmaydi va imzo slotini to'sadi (F2 ochiq bandi; dalil `runs\20260930-013904\shots\ERROR_sign.png`); UI'da tanlangan sanalar final'gacha faqat brauzerda.
- Zaxira `local.db.bak-20260930-021733-acceptance` (+ oldingi yurishlar `…-013904/020115/020840/021633-acceptance`, hammasi tozalangan); sanoqlar teng (audit_logs 3038→3266), v2 jadvallar 0, storage teng.

## 2026-09-30 — F3-UI: «Share for others to fill» + /s forma + Submissions (frontend) — JONLI
- Nusxa `_staging\f3ui_rebased` (F2 dan keyin, F2b dan OLDIN olingan). 7 yangi fayl: `pages/SharedForm.tsx`, `workspace/share/{ShareDialog.tsx,useShare.ts}`, `panels/SubmissionsSection.tsx`, `styles/share.css`, `tests/ui/{_sharemock.py,test_share.py}`; umumiy: `App.tsx` (auth'siz `/s`), `api/share.ts`, `EditorTopBar.tsx` (share slot), `Workspace.tsx`, `_harness.py`, `run_all.py`, `README.md`.
- Share tugmasi faqat can_edit + ulashsa bo'ladigan maydon bo'lsa; LAN ogohlantirishi + «I understand» + Create; URL bir marta; muddat 72 soat / maks 14 kun; Revoke. `/s`: fragment darhol o'chiriladi, token faqat `X-Share-Token`; kirill oldindan rad; yaroqsiz havolaga bitta neytral xabar. Submissions: maskalangan qiymatlar; Accept → draft → Save; `draft_locked` so'z bilan; Reject sabab bilan. Nusxada 341/341, jonli E2E 26/26.
- **Birlashtirish (direktor):** F2b bilan 3 tomonlama `git merge-file` (asos = `_staging\f2b_prelive_backup`): `Workspace.tsx` toza; `run_all.py` (2) va `README.md` (1) ziddiyatlari — ikkala tomon qo'shimchasi saqlandi (`forms | share`). `_sharemock.py`: share mock yo'q bo'lganda `…/versions/{n}/fields` ga `[]` qaytarish olib tashlandi (F2b `_formmock` haqiqiy maydonlarini to'sardi). Sinxron paytida Vite to'xtatildi (EBUSY xavfi), keyin qayta ko'tarildi; `/s` 200. Zaxira `_staging\f3ui_prelive_backup\`.
- Jonli to'liq tekshiruv: typecheck 0, build 0, `tests/ui` 353/354 → `test_share.py:67` birlashtirish kutilmasi: tahrir huquqisiz foydalanuvchida umuman `/fields` bo'lmasligini kutgan, lekin F2b formalar qatlami `can_fill` uchun maydonlarni o'qiydi (to'g'ri xulq). Tekshiruv asl maqsadiga toraytirildi — share-links/submissions so'rovlari yo'q (zaiflashtirilmagan). share 12/12 → jami **354/354**.

## 2026-09-30 — QA nuqsonlari (qafix) + burilgan sahifa (rotfix) — JONLI
- **qafix (backend, 03:29):** (1) qisman tahrir yangi qismi kontent oqimiga SPLICE (`content_splice.py`, render-diff >48 px yoki noaniqlik → eski append) — sintetik: 3 ekstraktor + pdfium + pdf.js bitta qatorda, Δ0, tashqarida 0 px; haqiqiy shablon uzluksiz o'qish 8/32 → 30/32; (2) ✥ Move: box top-left siljisa butun blok ko'chadi (matn o'zgarmasa ham), jim yo'qotish yo'q (422 `op_outside_page`/`text_overflow`/`text_move_not_applied`); (3) `tounicode.py` — PyMuPDF 5 xonali hex ToUnicode → UTF-16 surrogat (40 yuz valid, DejaVu/Amiri final'da pypdf yiqilmaydi). Nusxada pytest 1358 passed + 1 skipped + 1 xfailed (MuPDF `'`/TL kanareykasi). 6 fayl, zaxira `_staging\qafix_prelive_backup\`, uvicorn 31760.
- 🚩 Yangi topilmalar (→ `_staging\zw` agenti): Docusign qatorlarining ko'pchiligida U+200B → `undrawable_character` rad → haqiqiy shablon asosiy matnining ko'pi tahrirlanmasligi mumkin; haqiqiy shablon 2-bet bir qatorda eski oxirgi so'z redaksiyadan keyin QOLADI (jonli kodda ham); MuPDF `'`+TL filtr xatosi.
- **rotfix (frontend):** `/Rotate` sahifada ikki fazo — dvigatel (matn bloki, havola, rasm, vidjet, redaksiya, v7 markup) burilmagan, legacy builder (imzo, slot, sana, v1–v6) ko'rsatilgan; dvigatel qatlamlari xom chizilgan, kiritish noto'g'ri fazoda, v7 sim konversiyasiz, draft burishda slot clamp offsetlarni qayta yozardi. Tuzatma: bitta xarita `lib/viewport.ts` (frameLayerStyle, mapRectPt, clientToLayerPt), `lib/markupFrames.ts`. Nusxada 360/360, `test_rotated_pages` 6/6; jonli E2E 21/21 (imzo Δ 0.01 pt, matn Δ 0.000 pt, slot to'silmadi). 22 fayl (`test_share.py` ataylab olinmadi — jonlisi yangiroq), Vite to'xtatilib sinxronlandi, zaxira `_staging\rotfix_prelive_backup\`. Ochiq: backend `element_points` burilgan elementni burilmagan qutisi bilan tekshiradi (→ zw agenti); burilgan slotda imzo tik va 34 pt ga siqiladi (backend dizayni); legacy text/date sahifa bilan burilmaydi.

- 2026-09-30 ~03:4x jonli to'liq tekshiruv (qafix + rotfix + F3-UI dan keyin): typecheck 0, build 0, `tests/ui` **360/360**, pytest **1359 passed + 1 xfailed** (MuPDF `'`/TL kanareykasi), 0 fail.


## 2026-09-30 — ZW: Docusign U+200B, eski so'z qolishi, `'`/TL filtri, burilgan element_points — JONLI
- (1) U+200B: haqiqiy shablon nusxasida 1915 qatordan 979 tasida (51,1 %) 4975 ta U+200B — hammasi `undrawable_character` bilan rad edi. `model.IGNORABLE` = U+00AD/200B/200C/200D/2060/FEFF (RLO/LRM/RLM/izolyat/teg hamon rad); `Line.view`/`display_text`; `expect_text` NFC + ZW'siz; yangi matndan ZW olib tashlanadi. Tahrirlanadigan qatorlar **933 → 1900/1915** (qolgan 15 = ☐ U+2610, to'g'ri rad).
- (2) 🚩 Eski so'z qolishi — ildiz: MuPDF glifni rawdict qutisining ~80 % ga qisqartirilgan qutisi bo'yicha oladi; band rawdict chetiga qo'yilgani uchun glif jimgina qolardi. Bazaviy kodda haqiqiy shablonning 18 betidagi 20 qatorda **145 ta eski glif** ko'rinib qolardi. Tuzatma: `text_edit.hit_box` (0.8) + `apply.execute_plan` redaksiyadan keyin MAJBURIY tekshiruv → 2-o'tish → 422 `old_text_not_removed`. Natija: 145 → **0**; 2-bet tashqarida 60 px → 0.
- (3) `Doc.fix_quote_ops` (`'` → `T* Tj`, `"` → `Tw Tc T* Tj`) — xfail → pass. (4) `markup.element_points` burilgan burchaklar. (5) Frontend: `text_move_not_applied`/`old_text_not_removed` xabarlari, `stripIgnorable`, `useEditSession.ts` mojibake. Qo'shimcha: `content_splice._num` 6 → 10 xona.
- Nusxada pytest 1388 passed, 0 fail; UI 364/364 (+4 `test_zero_width.py`). Ochiq: p3 b8l0 / p22 b25l0 splice guard → append (o'qish tartibi); p41–44 header (bazada ham).
- **Deploy (direktor, 03:4x):** 20 fayl (11 backend, 6 frontend, 3 test), ziddiyat va ro'yxatdan tashqari farq yo'q, zaxira `_staging\zw_prelive_backup\`; backend + Vite birga to'xtatilib qayta ko'tarildi (~15 s), LAN 200. Yakuniy QA qabul to'plami ishga tushirildi.

## 2026-09-30 04:41 — YAKUNIY QA qabul to'plami: 18/18 (API + UI) — BARCHASI PASS
- `tests\acceptance\runs\20260930-044126` (6.4 daq), zaxira `backend\local.db.bak-20260930-044126-acceptance`. 18 band × (API | UI) hammasi PASS: API 205/205, UI 163/163 (jami 368). Oldingi yurish (021733) 350/370 edi — 3 nuqson (qafix) va haqiqiy shablon muammolari (zw) tuzatilgach.
- Joylashuv: ✔/✖ 100 % va 209 % zoomda ±0.5 pt (dev 0.13–0.23 pt), slot imzosi rule_y = o'lchangan chiziq (±0.5 pt), guruh +3 / slot +2 offset aniq, sana maydon ichida, `/verify intact`.
- NOTE (eski, B1): pdfplumber sanani `__/__/____` bilan qo'shib o'qiydi; pypdf/PyMuPDF to'g'ri.
- Jonli: typecheck 0, build 0.

- 2026-09-30 04:52 jonli backend pytest (ZW dan keyin): **1388 passed, 0 fail, 0 xfail**. Editor v2 spetsifikatsiyasi: 18/18 qabul testi PASS, `tests/ui` 364, pytest 1388. Qolgani: boshqa serverga ko'chirish (foydalanuvchi qarori — oxirida; trusted-proxy/real IP majburiy).

