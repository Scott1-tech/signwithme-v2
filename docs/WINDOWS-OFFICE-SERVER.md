# SignWithMe'ni ofisdagi Windows kompyuterda ishga tushirish

Ilova ofisdagi bitta kompyuterda ishlaydi. Ofisdagi boshqa kompyuterlar uni
tarmoq orqali ochadi. Xohlasangiz, **Cloudflare Tunnel** orqali internetdan
ham ochiladi (routerda port ochish kerak emas).

Bu kompyuter **doim yoniq** turishi kerak: u o'chsa, ilova ham ishlamaydi.

---

## 1. Kerakli dasturlar (bir marta)

1. **Docker Desktop** — <https://www.docker.com/products/docker-desktop/>
   - O'rnatishda **"Use WSL 2"** belgisini qoldiring.
   - O'rnatilgach kompyuterni qayta yoqing va Docker Desktop'ni oching.
     Pastki chap burchakda **"Engine running"** chiqishini kuting.
   - Docker Desktop → **Settings → General → "Start Docker Desktop when you sign
     in to your computer"** ni yoqing (kompyuter qayta yonganda ilova o'zi ishga tushadi).
2. **Git** — <https://git-scm.com/download/win> (hamma sozlamalarni o'zgartirmay "Next").
3. **Uyqu rejimini o'chiring:** Settings → System → Power → *Sleep* → **Never**.

## 2. Ilovani yuklab olish

**PowerShell**'ni oching (Start → "PowerShell"):

```
cd C:\
git clone https://github.com/Scott1-tech/signwithme-v2.git
cd C:\signwithme-v2
```

## 3. Birinchi ishga tushirish

PowerShell'ni **administrator sifatida** oching (Start → "PowerShell" → o'ng
tugma → *Run as administrator*), so'ng:

```
cd C:\signwithme-v2
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

Skript so'raydi:
- **Admin e-mail** va **parol** — ilovaga shu bilan kirasiz;
- **Cloudflare Tunnel token** — hozircha shunchaki **Enter** bosing (faqat ofis).
  Internetdan ham kerak bo'lsa, pastdagi 5-bo'limga qarang.

Birinchi marta 5–15 daqiqa davom etadi. Oxirida manzillar chiqadi:
- shu kompyuterda: `http://localhost:3000`
- ofisdagi boshqa kompyuterlarda: `http://<shu-kompyuter-IP>:3000`
  (masalan `http://192.168.1.25:3000`)

> **Muhim:** yaratilgan `.env` faylining nusxasini boshqa joyda (fleshka yoki
> parollar menejerida) saqlang. Undagi `PII_ENCRYPTION_KEY` yo'qolsa,
> saqlangan SSN/EIN qiymatlarini qayta o'qib bo'lmaydi.

Boshqa xodimlar uchun akkauntni admin yaratadi (o'zi ro'yxatdan o'tish o'chirilgan).

## 4. Kundalik ishlar

| Nima | Buyruq (`C:\signwithme-v2` papkasida) |
|---|---|
| Zaxira nusxa olish | `powershell -ExecutionPolicy Bypass -File scripts\windows\backup.ps1` |
| Yangi versiyaga o'tish | `powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1` |
| Holatini ko'rish | `docker compose ps` |
| Xatoliklarni ko'rish | `docker compose logs --tail 100 backend` |
| To'xtatish (ma'lumot saqlanadi) | `docker compose stop` |
| Qayta yoqish | `docker compose up -d` (tunnel bo'lsa: `docker compose --profile tunnel up -d`) |

**Zaxira nusxani avtomatlashtirish:** Task Scheduler → *Create Basic Task* →
har kuni, masalan 20:00 → *Start a program*:
- Program: `powershell`
- Arguments: `-ExecutionPolicy Bypass -File C:\signwithme-v2\scripts\windows\backup.ps1`

Nusxalar `C:\signwithme-v2\backups\` ga tushadi (oxirgi 14 tasi saqlanadi).
**Ularni boshqa diskka yoki bulutga ham ko'chirib turing** — kompyuter
buzilsa, shu kompyuterdagi nusxa ham yo'qoladi.

## 5. Internetdan kirish (Cloudflare Tunnel) — ixtiyoriy

Buning uchun Cloudflare'da o'z **domeningiz** bo'lishi kerak (masalan
`signwithme.com`; yiliga ~10$). `workers.dev` manzili tunnel uchun yaramaydi.

1. <https://one.dash.cloudflare.com> → **Networks → Tunnels → Create a tunnel**
   → *Cloudflared* → nom bering (masalan `signwithme-office`).
2. "Install connector" sahifasida **Docker** ni tanlang va buyruqdagi
   `--token` dan keyingi uzun **token**ni nusxalang (buyruqning o'zini ishlatmang).
3. **Public Hostname** qo'shing:
   - Subdomain/Domain: masalan `app.signwithme.com`
   - Service: **HTTP** → `frontend:80`
4. `C:\signwithme-v2\.env` faylini Notepad'da oching va qatorni to'ldiring:
   `CLOUDFLARE_TUNNEL_TOKEN=<token>` (bo'sh joysiz).
5. Ishga tushiring:
   ```
   docker compose --profile tunnel up -d
   ```
6. Brauzerda `https://app.signwithme.com` ni oching.

Xavfsizlik uchun tavsiya: Cloudflare Zero Trust → **Access → Applications**
orqali shu manzilga faqat ruxsat berilgan e-maillar kirishini sozlang.

## 6. Muammolar

- **"Docker Desktop is not running"** — Docker Desktop'ni oching, "Engine running"ni kuting.
- **Boshqa kompyuterdan ochilmayapti** — `setup.ps1`'ni administrator sifatida
  qayta ishga tushiring (Firewall qoidasi qo'shiladi) va ikkala kompyuter bitta
  tarmoqdaligini tekshiring. Wi-Fi tarmog'i Windows'da **Private** bo'lishi kerak
  (Settings → Network → Wi-Fi → tarmoq → *Private network*).
- **Kompyuter IP manzili o'zgarib qoldi** — routerda shu kompyuterga doimiy IP
  (DHCP reservation) bering.
- **Ilova ochilmayapti** — `docker compose ps` (hammasi *running/healthy*
  bo'lishi kerak) va `docker compose logs --tail 100 backend`.
