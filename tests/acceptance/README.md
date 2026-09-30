# Jonli qabul to'plami — PDF muharriri spetsifikatsiyasining 18 testi

Jonli SignWithMe'ga (UI `http://10.31.0.58:5173`, API `:8000`) qarshi avtomatik qabul testlari.
Har test ikki yo'l bilan: **API** (`api_tNN`) va **UI** (`ui_tNN`, izolyatsiyalangan headless Chromium).
Kod (frontend/backend) o'zgartirilmaydi — faqat sintetik `ZZSYNTH` ma'lumot yaratiladi va oxirida o'chiriladi.

## Yurgizish

```powershell
cd C:\PTG\signwithme\tests\acceptance
C:\PTG\signwithme\backend\.venv\Scripts\python.exe run_acceptance.py            # 18 × (API + UI), ~20–30 daq
C:\PTG\signwithme\backend\.venv\Scripts\python.exe run_acceptance.py --only 1,9 --path ui
C:\PTG\signwithme\backend\.venv\Scripts\python.exe run_acceptance.py --keep     # tozalamasdan (tuzatish uchun)
C:\PTG\signwithme\backend\.venv\Scripts\python.exe run_acceptance.py --resume runs\<stamp> --only 5 --path api --keep
C:\PTG\signwithme\backend\.venv\Scripts\python.exe run_acceptance.py --cleanup-only runs\<stamp>
```

**Interpretatorlar (ikkalasi kerak):**
- Orkestrator va PDF dalillari — **backend venv** `C:\PTG\signwithme\backend\.venv\Scripts\python.exe`
  (pypdf, pdfplumber, PyMuPDF, pypdfium2, reportlab, Pillow).
- UI qadamlari — **tizim Python'i** `C:\Program Files\Python312\python.exe` (Playwright + Chromium);
  orkestrator uni o'zi `subprocess` bilan chaqiradi (`acc_ui.py <scenario> <args.json> <out.json>`).
  Boshqa yo'l kerak bo'lsa: `SWM_UI_PYTHON`. Nishonni almashtirish: `SWM_API`, `SWM_UI`.

Login `backend\.env` (`SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`) dan yurish paytida o'qiladi; parol/token
hech qaysi faylga yozilmaydi (token faqat brauzer konteksti localStorage'ida).

## Har yurish nima qiladi

1. **Zaxira:** jonli `backend\local.db` → `backend\local.db.bak-<stamp>-acceptance` (SQLite online backup API,
   `integrity_check` ok bo'lmasa to'xtaydi). Jadval sanoqlari + storage fayl soni/hajmi yoziladi.
2. **Tayyorlov:** 1 shablon + 3 kontrakt (`ZZSYNTH-ACC-{API,UI,LEGACY}-<stamp>`), 3 betlik sintetik PDF
   (`acc_pdf.make_contract`: rangli katak ustida qator, o'chiriladigan qator, «sir» qator, `____`, rastr rasm,
   imzo+sana bloki 1- va 3-betda). API yo'li A kontraktida, UI yo'li U kontraktida, 18/API tegilmagan L
   kontraktida (hujjat muharriri qatori yaratilmaydi), 18/UI esa UI orqali yuklangan yangi kontraktda.
3. **Testlar** (tartib: kontent → tuzilma → sessiya → imzo → chiqish; 11 ning final tekshiruvlari 17 ichida).
4. **Tozalash:** kontraktlar `DELETE`, shablon `?purge=true`, yurishda paydo bo'lgan saqlangan imzolar va
   galereya rasmlari API orqali o'chiriladi; API faqat soft-delete qiladigan galereya qatorlari (`label LIKE
   'zzacc%'`, `deleted_at` bor, fayli yo'q) aniq shu shart bilan o'chiriladi. Keyin: sanoqlar oldin = keyin
   (faqat `audit_logs` o'sadi — bu dalil), editor-v2 jadvallarida ZZ qoldiq 0, storage fayl soni teng,
   ID nomli fayl 0 (`cleanup.json`).
5. **Hisobot:** `runs\<stamp>\results.json` (har tekshiruv + dalil), `report.md` (18 × API|UI jadval),
   PDF'lar, kesma PNG'lar (oldin/keyin), `shots\` (UI skrinshotlari), `ui_*.log`.

## Tekshiruv usuli

- **Matn:** har kontent o'zgarishi uchta mustaqil o'quvchida (pypdf, pdfplumber, PyMuPDF): yangi matn bor,
  eski yo'q. O'quvchi yiqilsa (`<ERROR …>`) — tekshiruv FAIL, test to'xtamaydi.
- **Vizual:** pypdfium2, 8 px/pt. Tahrirlangan hudud tashqarisida 0 px farq (>48), hudud ichida farq bor;
  chap chet/baseline Δ ≤ 0.5 pt (PyMuPDF + pdfplumber); rangli katakda sof oq piksel 0 («oq quti yo'q»).
- **Joylashuv (UI):** 100 % va ~209 % zoomda (viewport o'lchami + «Fit» bilan aniq shu foizga keltiriladi):
  serverdagi matn ramkalari DOM'da bbox ±0.5 pt; chizilgan/qo'yilgan obyekt serverda sichqoncha turgan joyda
  ±0.5 pt (kutilgan nuqta yaxlitlangan pikseldan qayta hisoblanadi).
- **Imzo:** chiziq ustki cheti manba renderida o'lchanadi (va server `rule_y` bilan solishtiriladi);
  siyoh pasti = chiziq − 1 pt (+ guruh/slot siljishi) ±0.5 pt; sana matni sana maydoni ichida.
- **Boshqa ko'ruvchilar:** pypdf, pdfplumber, PyMuPDF (repair'siz), pdfium render va **pdf.js 4.8.69**
  (frontend'ning o'z `pdfjs-dist`i, Chromium ichida) — sahifa soni, qaysi betda siyoh bor (pdfium bilan bir xil), matn.

## UI hali yo'q qismlar → PENDING

UI funksiyasi mavjudligi yurish boshida jonli toolbar'dan aniqlanadi (`probe`, `ctx.state["ui_tools"]`).
Asbob yo'q bo'lsa o'sha UI yo'li (yoki uning qismi) `PENDING` bo'ladi, API yo'li baribir sinaydi.
2026-09-30 02:17 holatida F2 + F2b jonli — 18 bandning hammasida UI yo'li bor, PENDING yo'q.
Yangi UI qismi kelganda: `acc_ui.py` ga ssenariy (`SCENARIOS`) qo'shing va `acc_cases.ui_tNN` ni unga
ulang — API yo'li va PDF dalillari o'zgarmaydi. F3 ulashish UI (`share-links`) 18 bandga kirmaydi.

Eslatma (yurish paytida): jonli Vite/uvicorn qayta ishga tushsa UI qadamlari `ERR_CONNECTION_REFUSED`
yoki 500 bilan yiqiladi — bu muhit uzilishi; to'plamni qayta yurgizing (~7 daq).

## Fayllar

| Fayl | Vazifa |
|---|---|
| `run_acceptance.py` | orkestrator: zaxira, tayyorlov, testlar, tozalash, hisobot |
| `acc_cases.py` | 18 test: `api_tNN` / `ui_tNN`, PDF dalillari |
| `acc_ui.py` | UI ssenariylari (tizim Python + Playwright) |
| `acc_api.py` | HTTP yordamchi (faqat stdlib), login `.env` dan |
| `acc_pdf.py` | sintetik PDF, 3 o'quvchi, render, piksel o'lchovlari |
| `acc_db.py` | zaxira, sanoqlar, qoldiq, galereya soft-delete tozalash |
| `acc_core.py` | natija yozuvchisi, UI subprocess |

Eslatma: server bir yozuvchili SQLite; parallel agentlar ham jonli backend'ga yozsa, umumiy sanoqlar
farqi ular tufayli bo'lishi mumkin — shuning uchun qoldiq ushbu yurish ID'lari bo'yicha ham alohida tekshiriladi.
