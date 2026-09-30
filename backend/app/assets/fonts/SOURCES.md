# Font sources

Fonts shipped with the backend and embedded (as subsets) into final PDFs by
`app/services/fonts.py`. The frontend preview uses the same files.

## Liberation Fonts 2.1.5 — `liberation/`

- Project: https://github.com/liberationfonts/liberation-fonts
- Release: 2.1.5 (published 2021-09-30), https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5
- Archive: https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz
  - SHA-256 `7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0`, 2 385 008 bytes
- Downloaded: 2026-09-26. Files copied from the archive unchanged.
- License: SIL Open Font License 1.1 — full text in `liberation/LICENSE`
  (copyright Google 2010 / Red Hat 2012, Reserved Font Names "Liberation",
  "Arimo", "Tinos", "Cousine"). `liberation/AUTHORS` from the same archive.
  The OFL allows bundling and embedding; the fonts are not sold on their own
  and are not renamed or modified.

| File | Bytes | SHA-256 |
|------|------:|---------|
| LiberationSans-Regular.ttf | 410712 | `76d04c18ea243f426b7de1f3ad208e927008f961dc5945e5aad352d0dfde8ee8` |
| LiberationSans-Bold.ttf | 414456 | `788abee4c806d660e8aee46689dd8540cd4bb98da03dcc9d171ce3efd99a9173` |
| LiberationSans-Italic.ttf | 415816 | `e5bae5c4cde31f22142753855f4f8fb86da6ff39955ed3c0a11248b0d16948b0` |
| LiberationSans-BoldItalic.ttf | 408996 | `698da70fc191cc5f33ad4d6d3fe830fe4624b898ea2e3169955928b7c491f1ee` |
| LiberationSerif-Regular.ttf | 393576 | `058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74` |
| LiberationSerif-Bold.ttf | 370096 | `d754ba427cfe0bca54ae052384baa8f842da5bd6550ad4da024ac441e7a7d5ce` |
| LiberationSerif-Italic.ttf | 375632 | `0e3dea9f8d613e006ccfa62201f33e265d19167bd0907725c3e145368b04fc2e` |
| LiberationSerif-BoldItalic.ttf | 376772 | `f17db8af71e24d2066b587546021d4f0b296be389512b658dec3c09affeb11a7` |
| LiberationMono-Regular.ttf | 319508 | `f2b83c763e8afd21709333370bed4774337fae82267937e2b5aea7e2fbd922c1` |
| LiberationMono-Bold.ttf | 307996 | `bd62a0672d0b9b6710b01df434c80ad54fa5f0835207eb7b17b7a761463067bb` |
| LiberationMono-Italic.ttf | 281536 | `605c01c711b44480a7508d349dfbf3264e81fa43d69e61cfa7d10b86e764c4d1` |
| LiberationMono-BoldItalic.ttf | 284068 | `79451f3c09fe25116098853b7a2ca6e2436220ccc11af022979adbcf195be130` |
| LICENSE (OFL 1.1) | 4414 | `93fed46019c38bbe566b479d22148e2e8a1e85ada614accb0211c37b2c61c19b` |

Verify: `sha256sum app/assets/fonts/liberation/*` from `backend/`.

Do NOT add fonts copied from `C:\Windows\Fonts` (Arial, Times New Roman, ...):
their licenses do not allow redistribution.

---

## Editor v2 (B2) families — downloaded 2026-09-29

All files are copied UNCHANGED from the archives / pinned commits below (not
renamed, not modified, not sold on their own). Every license here allows
bundling and embedding a subset into a PDF: SIL OFL 1.1 (§ "Permission ...
to use, study, copy, merge, embed, modify, redistribute") and the Bitstream
Vera / Arev license (DejaVu). The license text travels next to the files.

Tinos / Cousine were NOT added: Liberation 2.x Serif/Mono are the same
designs (metric-compatible with Times New Roman / Courier New) and are
already shipped.

### Arimo 1.33 — `arimo/` (metric-compatible with Arial / Helvetica)
- Project: https://github.com/googlefonts/Arimo, commit
  `4a6255f269916ae7ad3fc2706b0935e7621396b8`, files `fonts/ttf/Arimo-*.ttf`, `OFL.txt`
  (https://raw.githubusercontent.com/googlefonts/Arimo/4a6255f269916ae7ad3fc2706b0935e7621396b8/fonts/ttf/Arimo-Regular.ttf ...)
- License: SIL OFL 1.1 (`arimo/OFL.txt`).

### Carlito 1.104 — `carlito/` (metric-compatible with Calibri)
- Source: https://github.com/google/fonts, commit `23e54b51ddffbc7713c583748e3bd86f62b1fa4a`, `ofl/carlito/`.
- License: SIL OFL 1.1 (`carlito/OFL.txt`).

### Caladea 1.001 — `caladea/` (metric-compatible with Cambria)
- Source: https://github.com/google/fonts, commit `23e54b51ddffbc7713c583748e3bd86f62b1fa4a`, `ofl/caladea/`.
- License: SIL OFL 1.1 (`caladea/OFL.txt`). Latin only (no Cyrillic, no U+02BB).

### DejaVu 2.37 — `dejavu/` (Sans + Serif, 4 styles each)
- Release: https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37
- Archive: https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2
  - SHA-256 `fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7`, 5 429 777 bytes
- License: Bitstream Vera Fonts license + Arev Fonts license, DejaVu changes public domain (`dejavu/LICENSE`).

### Amiri 1.000 — `amiri/` (Latin part only; right-to-left text stays REFUSED)
- Release: https://github.com/aliftype/amiri/releases/tag/1.000
- Archive: https://github.com/aliftype/amiri/releases/download/1.000/Amiri-1.000.zip
  - SHA-256 `926fe1bd7dfde8e55178281f645258bfced6420c951c6f2fd532fd21691bca30`, 1 039 502 bytes
- License: SIL OFL 1.1 (`amiri/OFL.txt`). `fonts.embedded_refusal` refuses bidi R/AL
  characters in every family, so Amiri prints its Latin glyphs only.

### Signature scripts — `script/` (typed signatures only, never document text)
- Source: https://github.com/google/fonts, commit `23e54b51ddffbc7713c583748e3bd86f62b1fa4a`
  - Great Vibes 1.103 — `ofl/greatvibes/GreatVibes-Regular.ttf`, `OFL.txt` → `script/OFL-greatvibes.txt`
  - Allura 1.110 — `ofl/allura/Allura-Regular.ttf`, `OFL.txt` → `script/OFL-allura.txt`
  - Sacramento 1.000 — `ofl/sacramento/Sacramento-Regular.ttf`, `OFL.txt` → `script/OFL-sacramento.txt`
- License: SIL OFL 1.1 each.

| File | Bytes | SHA-256 |
|------|------:|---------|
| arimo/Arimo-Regular.ttf | 478712 | `41b22bc8f0b51f932825d37bc55b5eb6ba67dfe599a626e4aff2b43b624f9f8c` |
| arimo/Arimo-Bold.ttf | 485872 | `d7a8b187cf8444d4cfee102e8eae9e3043682fd5106d5d33ed677fe268a0e2ba` |
| arimo/Arimo-Italic.ttf | 502332 | `ffdf690e4357a392e4edcc3df0b3adb0302920f75e54f2e6eec355afbe7edcba` |
| arimo/Arimo-BoldItalic.ttf | 508508 | `77c967d49d4ef4a63c39fbcd1e27892102e503e35a20983cb058430c4f268b34` |
| arimo/OFL.txt | 4383 | `c46fda6859efb80c0a3c1faa868908a3d2fcb26eb7fd9602d73bebeec21bca3a` |
| carlito/Carlito-Regular.ttf | 628032 | `f6418f708baede9789daef5d458c0f53d2a888af9820e8062934e504fedc6595` |
| carlito/Carlito-Bold.ttf | 682468 | `bb5d20f79b82599ec72983597437373a80f2d2085fa91fc144fd74e876a594db` |
| carlito/Carlito-Italic.ttf | 615236 | `0b019225e58d702bfedcbd35c21696769f8ee115cb6343f84c2f240312450d1c` |
| carlito/Carlito-BoldItalic.ttf | 808508 | `b32928186c119599e03ca6a1ffc680fdcb7fac95772f4b95d989cf6cd3861517` |
| carlito/OFL.txt | 4424 | `58402f82a7c332a700294988fe7554fbb0a63a8d27ccc1ee3bbc640311990a00` |
| caladea/Caladea-Regular.ttf | 81600 | `f1e899278b7b4491aba5b6a8253c4b04c050cc59b21865be5c37559a775153cd` |
| caladea/Caladea-Bold.ttf | 84492 | `ae3cb2dcbc925809dd29d2a44e9802211cab66be541bacbfc9c08c74b27c3742` |
| caladea/Caladea-Italic.ttf | 83780 | `4359a8e24f748b6447b1ff6d7a174febe70961d29f8bb8634b56dacd740a3deb` |
| caladea/Caladea-BoldItalic.ttf | 83356 | `ccabaa7b7e2fdf253d2b1a5fa699dd8a3df8d835a9eb285ad82631a677eb76c0` |
| caladea/OFL.txt | 4393 | `ccdab61d371d8c8683a128a92cd7d498dbdb1d37689f7cb21f1bf6b16658d213` |
| dejavu/DejaVuSans.ttf | 757076 | `7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954` |
| dejavu/DejaVuSans-Bold.ttf | 705684 | `e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724` |
| dejavu/DejaVuSans-Oblique.ttf | 635416 | `4af75fa16ee6d3ad43e1ecec41862c24954af26a55c6bb1ebb27bd486a50f5f4` |
| dejavu/DejaVuSans-BoldOblique.ttf | 643292 | `eb436dca0c2594b73d8b603b892e374fdfd8d885d25ffb4f18df4c4c0b49e50f` |
| dejavu/DejaVuSerif.ttf | 380132 | `42d1edeb7952f31b1f96d767ed7030b08a39e0c372b0071641518864e2bffb51` |
| dejavu/DejaVuSerif-Bold.ttf | 356088 | `c47b5527bcdc8dcf9ea8c77054454c5a884beaca2f44851a2a823ee639cbf07f` |
| dejavu/DejaVuSerif-Italic.ttf | 345996 | `2e39b1d50f90b933b00c7bb54a96afd3f86419b3d717c7cf202e36f2d4973e47` |
| dejavu/DejaVuSerif-BoldItalic.ttf | 347460 | `8d3dd3d31350309042ed226af82b34539bd773518e6107cb352712853ba80308` |
| dejavu/LICENSE | 8816 | `7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93` |
| amiri/Amiri-Regular.ttf | 453396 | `b092096eb992aebe59084b0cb5cf0015ae82c7e76b0aac5e7d60ae5113af5f54` |
| amiri/Amiri-Bold.ttf | 429088 | `c20c5fda0b648bfd7672fd3ded17488daa3a4810673b9b012fbb4a0af6a26aab` |
| amiri/Amiri-Italic.ttf | 451684 | `7a678bab178232bb154bdf045bc7eaa0ab4193c312ff618037b4ac8977ac1b2a` |
| amiri/Amiri-BoldItalic.ttf | 426584 | `0ea3471e45795c04fd90c630575546df7c6d77c13ab976dde0a0e1354b38d3ac` |
| amiri/OFL.txt | 4387 | `72598f78a098644c6b4ac04665e0bad7a69841c0dc504e81a8399d963a23e50d` |
| script/GreatVibes-Regular.ttf | 457588 | `8d509802186f1b51572531ecf313e8098f9a5bfdfaca93f0c9b34467f9982d15` |
| script/Allura-Regular.ttf | 246632 | `9c142b2e515832c0dfc4ff8b8ea18f40314943bf937b72e2b23c4661bac14cc6` |
| script/Sacramento-Regular.ttf | 79696 | `9341fda10adbfeb7efc94302b34507a3e227d7e7f5c432df3f5ac8753ff73d24` |
| script/OFL-greatvibes.txt | 4399 | `61093a21f5e63dedf54222b3c09997e54c0fe43e3851d21386e02ddcbc246d49` |
| script/OFL-allura.txt | 4478 | `c8cb4c94a558b6cb82ac5dc981a73a0b8aee35a5c52c8a7832fc0280c289ad49` |
| script/OFL-sacramento.txt | 4431 | `2e2cb5a98da665f2ab82a9fd01fb18c2337f845761b0c163f690ed65f3b94677` |

`tests/test_api_fonts.py` re-hashes every file listed in this document.
