# Putting it into service

A start-to-finish runbook for a single-operator deployment. Follow the stages in
order — each one ends with a check, so a failure surfaces where it happened
rather than three stages later.

Assumed target is a Google Cloud e2-micro; every stage except **1** applies
equally to a machine you own.

---

## No terminal on your own computer?

You do not need one. Everything below can be done from a browser — Google Cloud
provides two terminals of its own, and neither installs anything locally.

**Cloud Shell** — a Linux shell in a browser tab, used here only to create the
VM. Open <https://console.cloud.google.com> and click the **`>_`** icon in the
top-right toolbar. It takes a few seconds to start, then behaves like any
terminal.

**Browser SSH** — a terminal *on the VM itself*, used for everything after that.
In the Console go to **Compute Engine → VM instances** and click the **SSH**
button on the instance's row. A new window opens, already logged in.

If you would rather not type the `gcloud` command in Stage 1 at all, create the
instance from the Console forms instead:

> **Compute Engine → VM instances → Create instance**
> * **Name:** `signwithme`
> * **Region:** `us-central1` · **Zone:** `us-central1-a`
> * **Machine configuration:** series **E2**, machine type **e2-micro**
> * **Boot disk → Change:** Debian 12, **Standard persistent disk**, **30 GB**
> * **Firewall:** leave *Allow HTTP traffic* and *Allow HTTPS traffic*
>   **unchecked** — the app is reached over Tailscale, not the open internet
> * **Create**

Then click **SSH** on the new instance and carry on from Stage 2. Every command
from that point runs in that browser window.

**Two things worth knowing in browser SSH:**
* **Pasting:** `Ctrl+V` works, as does right-click → Paste. To paste a long
  command, use the gear icon (top right of the SSH window) if the keyboard
  shortcut is intercepted.
* **Editing `.env` with `nano`:** arrow keys to move, type normally, then
  **`Ctrl+O`** → **`Enter`** to save, **`Ctrl+X`** to exit. There is no mouse
  cursor placement — use the arrow keys.

---

## What you need first

| | |
|---|---|
| A machine | e2-micro (1 GB RAM, 30 GB disk), or any spare computer that stays on |
| The repository | pushed somewhere you can `git clone` from |
| Your real template | the blank `CFT_ CD.pdf`, for Stage 6 |
| A test contract | one filled-in copy, ideally with a known mistake in it |
| ~40 minutes | most of it waiting on the first image build |
| A browser | that is genuinely all — see the section above |

---

## Stage 1 — Create the instance

Run this in **Cloud Shell** (the `>_` icon in the Console toolbar), or use the
Console form described at the top of this document.

```bash
gcloud compute instances create signwithme \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard
```

The free tier covers one e2-micro in `us-west1`, `us-central1` or `us-east1`
with a 30 GB **standard** disk. Another zone, a bigger disk, or a balanced disk
is billed. Do **not** create firewall rules opening port 3000 — access comes
over a private network in Stage 5.

**Check:** `gcloud compute instances list` shows the instance as `RUNNING`.

---

## Stage 2 — Prepare the machine

Click **SSH** next to the instance in the Console — or run
`gcloud compute ssh signwithme --zone=us-central1-a` from Cloud Shell.

```bash
sudo apt-get update -qq && sudo apt-get install -y -qq git
git clone <your-repo-url> && cd Signwithme1
bash scripts/gcp-setup.sh
exit
```

This installs Docker, creates 4 GB of swap and caps container log growth. Swap
is not optional on a 1 GB machine: without it the frontend image build is
OOM-killed part-way through and presents as an unexplained hang.

Log out and back in — the `docker` group membership only applies to a new
session. In browser SSH, close the window and click **SSH** again.

```bash
cd Signwithme1
```

**Check:** `free -h` shows a non-zero Swap row, and `docker ps` runs without
`sudo` and without a permission error.

---

## Stage 3 — Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # DB_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # PII_ENCRYPTION_KEY
nano .env
```

Set these, leaving the rest alone:

| Variable | Value |
|---|---|
| `DB_PASSWORD` | a generated string |
| `JWT_SECRET` | a generated string — the app refuses to start with the example one |
| `PII_ENCRYPTION_KEY` | a **different** generated string |
| `SEED_ADMIN_EMAIL` | your email |
| `SEED_ADMIN_PASSWORD` | a password you choose, 8–72 characters |
| `STORAGE_BACKEND` | leave as `local` |
| `FRONTEND_BIND` | leave as `127.0.0.1` |

> **Save `PII_ENCRYPTION_KEY` somewhere outside this machine, now.** It encrypts
> every stored SSN and EIN. Lose it and those values are unreadable; there is no
> recovery path, by design. Store it somewhere separate from your backups — a
> backup containing both the ciphertext and its key is just plaintext.

Then:

```bash
./scripts/preflight.sh
```

**Check:** preflight reports `0 blocking`. It catches default secrets, missing
swap, a full disk and a `.env` that would be committed.

---

## Stage 4 — Start

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build
```

First build takes 10–20 minutes on a shared core. Watch it with
`docker compose logs -f` in another session if you want to see progress.

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

**Check:** five containers `running` (postgres, redis, backend, worker,
frontend), `/health` returns `{"status":"ok"...}`, and `/ready` returns
`{"status":"ready"}` — the second one proves the database is migrated and
answering.

If `/ready` fails, the migration did not complete:
`docker compose logs backend | tail -40`.

---

## Stage 5 — Reach it privately

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```

Follow the printed link to authorise the machine, then install Tailscale on your
laptop and phone under the same account and open `http://signwithme:3000`.

The app stays bound to loopback on the VM. Nothing is published to the internet,
there is no certificate to manage, and access is controlled by which devices are
on your tailnet.

**Check:** the sign-in page loads on another device, and
`sudo ss -tlnp | grep 3000` on the VM shows it bound to `127.0.0.1`, not
`0.0.0.0`.

---

## Stage 6 — Validate detection on your real template

**Do this before trusting the system with anything real.** It is the one part
that has never been tested against your actual document.

1. Sign in as the admin account you seeded.
2. **Templates → Upload a blank template.** Upload the real `CFT_ CD.pdf`.
3. Wait for status `ready` (the page polls; a 40-page document takes a minute or
   two on this machine). If it reports **failed** with an OCR message, the file
   is a scan — run `ocrmypdf in.pdf out.pdf` on it first and upload the result.
4. Click **Inspect** and expand every page. For each page ask:
   * Is each field that should be filled actually listed?
   * Are the signature fields typed `signature`, not `text_line`?
   * Do the coordinates look plausible (`x, y` inside the page, sensible `w × h`)?
5. Note what is missing or wrong. Detection is good, not perfect — expect to
   correct some of ~150 fields.

**Check:** the field count is in the range you expect, and every signature and
date on the document appears in the list.

### Fixing the rules to match

The FMCSA and cross-field rules reference field ids the detector was *expected*
to produce — `p1_cdl`, `p2_dot_eligible`, `p4_straight_truck`, and so on. Compare
them against the real ids from step 4:

```
backend/app/services/comparison_engine.py
  DEFAULT_CROSS_FIELD_RULES   — values that must agree across pages
  DEFAULT_FMCSA_RULES         — compliance requirements
```

**A rule naming fields that do not exist is skipped, not failed.** That keeps a
mis-specified rule from failing every contract — but it also means the rule
silently does nothing. Re-point them at the real ids, then:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build backend worker
```

---

## Stage 7 — Run one contract end to end

Use a filled contract with a **deliberate mistake** — a CDL number that differs
between pages, or a blank DOT-eligibility box.

1. **Upload contract**, pick the template, upload the file.
2. Wait for the review page to finish analysing.
3. Confirm the sidebar reports the mistake you planted, and that approval is
   blocked.
4. Fix it (correct the field, or upload a clean copy), re-run the comparison,
   and confirm it now clears.
5. Approve → place a signature → pick the date → **Generate final contract**.
6. Download the result and open it.

**Check the finished PDF:**
* the signature sits **on** the signature line, not above or beside it
* the date reads `MM/DD/YYYY` and lands in the date field
* nothing is mirrored vertically or drawn on the wrong page

Then confirm the record: the **Final contract** page shows *"Integrity
verified"*, and the audit trail lists the whole lifecycle.

If a signature lands in the wrong place, the template's box is wrong, not the
overlay — correct that field's coordinates in the Template Manager and finalise
a fresh contract.

**Only after this passes is the system in service.**

---

## Stage 8 — Make it durable

**Schedule backups** (`crontab -e`):

```cron
0 2 * * * cd /home/$USER/Signwithme1 && ./scripts/backup.sh >> /var/log/contract-backup.log 2>&1
```

Then copy them off the machine — a 30 GB boot disk holding the app *and* its
only backup is not a backup:

From **Cloud Shell**:

```bash
gcloud compute scp --recurse --zone=us-central1-a \
  signwithme:~/Signwithme1/backups/<timestamp> ~/local-backups/
```

Then use the Cloud Shell **⋮ → Download** menu to pull the files onto your own
computer through the browser.

**Restore is documented** in each backup's `RESTORE.md`. Test it once now, while
nothing depends on it working.

**Updating:**

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build
```

Migrations apply automatically on start. Take a backup first.

---

## Day-2 commands

```bash
# State and health
docker compose ps
docker compose logs -f backend
docker compose logs --tail=100 worker
curl -fsS http://127.0.0.1:8000/ready

# Memory pressure — the usual culprit on this machine
free -h
docker stats --no-stream

# Restart one service
docker compose restart worker

# Stop everything (data volumes are preserved)
docker compose down
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build hangs, or a container is killed mid-build | Out of memory | `free -h`; run `scripts/gcp-setup.sh` if swap is 0 |
| `backend` restarts on a loop | Bad or default `JWT_SECRET` | `docker compose logs backend`; regenerate it |
| `/ready` fails, `/health` works | Migration did not run | `docker compose logs backend | grep -i alembic` |
| Contract stuck at `parsing` | Worker down or crashed | `docker compose logs worker`; `docker compose restart worker` |
| Template `failed`, message mentions OCR | The file is a scan | `ocrmypdf in.pdf out.pdf`, upload the result |
| Every field reports as missing | Template mismatch, or a scan | Confirm the contract matches its template |
| Signature drawn in the wrong place | Template field coordinates | Correct the field in the Template Manager |
| Sensitive value shows as `XXX-XX-6789` | Working as designed | Admin only, via the reveal endpoint — the access is audited |
| Cannot sign, no clear reason | Critical issues outstanding | The review sidebar lists them; the block is server-side |
| Login returns 429 | Throttle after repeated failures | Wait 15 minutes, or `docker compose restart backend` |

---

## What this deployment is not

State this plainly to yourself before relying on it:

* **Not reviewed by a lawyer.** The audit trail and hashes are good evidentiary
  material; whether the flow satisfies ESIGN/UETA for your contracts is a
  question for counsel.
* **Not a signing service for others.** Single operator, private network. There
  is no password reset, no token revocation, and login throttling is per process.
* **Not highly available.** One machine, one worker. If it dies, you restore from
  backup.
* **Not free of the external IPv4 charge** on Google Cloud — roughly $3/month,
  even though the instance itself is free tier.
