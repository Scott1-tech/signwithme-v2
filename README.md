# Contract Signing App

Template-driven contract review, comparison and signing for FMCSA-regulated
carriers. Upload a blank contract template once; the system detects every
fillable region and stores it as a field schema. Uploaded contracts are then
parsed against that schema, compared for missing fields, cross-page
inconsistencies and FMCSA compliance, reviewed in a side-by-side UI, signed on
canvas, and rendered into a final PDF with a full audit trail.

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 18, TypeScript, Vite, pdf.js |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Workers | Celery + Redis |
| Data | PostgreSQL 15 (JSONB field schemas), filesystem or MinIO/S3 |

## Running it

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"   # PII_ENCRYPTION_KEY
docker compose up --build -d
```

* Frontend — http://localhost:3000
* MinIO console — http://localhost:9001

`docker-compose.yml` is the **production** configuration: no source mounts, no
reload, restart policies on, and every port bound to loopback. The API and the
UI are reached through the frontend container, which proxies `/api`. For local
development add the override:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Migrations run automatically before the API starts — only in the API container,
so the worker cannot race it. If `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD`
are set, an admin account is created on first boot; self-registration only ever
produces contractors.

The app **refuses to start** outside debug mode if `JWT_SECRET` is missing or
still the example value.

### Backups

```bash
./scripts/backup.sh          # writes ./backups/<timestamp>/
```

Dumps Postgres and mirrors the object store together — one without the other
restores to a broken system. The encryption key is deliberately **not** included:
a backup holding both the ciphertext and its key is just plaintext in a tarball.
Store `PII_ENCRYPTION_KEY` somewhere else, and do not lose it — without it the
stored SSN and EIN values cannot be read back.

### Local development without Docker

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.workers.celery_app worker --loglevel=info   # in a second shell

cd ../frontend
npm install
npm run dev
```

### Tests

```bash
cd backend && python -m pytest -q      # 87 tests
cd frontend && npm run typecheck && npm run build
```

The suite includes full API tests — auth, RBAC, uploads, the signing gate, PII
handling, finalisation and download — running against SQLite with in-memory
object storage and inline Celery, so no external services are needed. That is
why the models use dialect-portable column types; production is still
PostgreSQL with JSONB. GitHub Actions runs all of this plus both image builds
on every push.

## The flow

1. **Template upload** (admin) — the PDF is stored, and `analyze_template_task`
   detects text fields, checkboxes, signature regions and tables, writing a
   `field_schema` with real coordinates for every one.
2. **Contract upload** — `process_contract_task` crops each region of the
   uploaded PDF, reads what the contractor wrote, and runs the comparison.
3. **Review** — the PDF renders with missing fields highlighted in place;
   the sidebar lists missing fields, cross-page inconsistencies, FMCSA
   violations and format warnings.
4. **Signing** — a reviewer approves, signatures are drawn on canvas and placed
   at the template's own signature coordinates.
5. **Finalise** — signatures and dates are overlaid onto the original PDF, the
   result is hashed, and every step is in the audit log.

## Design notes

These are the decisions that are easy to get wrong, and how this codebase
handles them.

### One coordinate convention

Three conventions are in play: pdfplumber (points, top-left origin,
`x0/top/x1/bottom`), reportlab (points, **bottom-left** origin), and the browser
(CSS pixels, top-left, scaled by zoom). Everything crossing an API or database
boundary uses one canonical form — points, top-left origin,
`{x, y, width, height}` — and conversion happens at exactly two edges:
`BBox.to_reportlab()` when drawing overlays, and multiplying by the viewer's
`scale` when rendering highlights. Mixing these up mirrors signatures
vertically or drifts every highlight the moment someone zooms.

### Field detection produces real coordinates

Bounding boxes come from `page.extract_words()` positions, not placeholders: the
detector maps each regex match back to the words it covers and extends the fill
region across the underscore run that follows. Highlighting and signature
placement both depend on this being accurate.

Repeated labels get an occurrence suffix (`p1_date`, `p1_date_2`), so the
contractor's date and the company representative's date on the same page stay
distinct instead of deduplicating into one. Checkboxes are detected both as
`☐` glyphs and as small vector squares — base-14 fonts have no U+2610 glyph, so
many real templates draw rectangles instead, and a missed checkbox is a missed
required field.

### Normalisation before comparison

Cross-field checks compare normalised values. `Oh Rt305248` and `OH-RT305248`
are the same licence; comparing raw strings would raise a *critical* CDL
mismatch on OCR casing noise and block signing. Names tolerate an added or
omitted middle name and `LAST, FIRST` ordering; dates parse `01/30/2030`,
`01.30.2030` and `1/30/2030` to the same day.

### Missing data fails compliance

`if value and value != "yes"` silently approves a blank DOT-eligibility box. An
absent compliance answer is treated as a violation, not a pass. Rules whose
fields the template does not contain are skipped rather than failed.

### The signing gate is server-side

`ComparisonEngine.can_sign` is enforced in `_assert_signable()` before any
signature is stored and again before the final PDF is built, alongside reviewer
approval. The frontend's hidden button is a convenience; a client posting
directly to the endpoint still cannot sign a contract with outstanding critical
issues.

### Dates use their own coordinates

Signature fields carry a `paired_date_field_id`, resolved at detection time from
proximity on the page. Finalisation writes the contract date into that field's
own bounding box rather than offsetting a fixed number of points from the
signature, and renders `MM/DD/YYYY` — the format the contract prints — rather
than the ISO value the date input submits.

### Sensitive values never sit in plaintext

SSNs, EINs and dates of birth are recognised by field id and encrypted (Fernet)
before they reach the database. They are masked in every API response
(`XXX-XX-6789`), masked in the cached extract, masked in the comparison result,
and scrubbed from the audit log — correcting a misread SSN would otherwise write
both the old and the new value into an append-only table, permanently. An admin
can decrypt one value through `GET /api/contracts/{id}/fields/{field_id}/reveal`,
which is itself an audited event. Reading an SSN is a deliberate act with a
record attached, not a side effect of opening a contract.

### Scans are identified, not silently failed

A scanned PDF has no text layer, so every field extracts as empty and the
comparison reports 100% missing — technically true and completely misleading.
The parser checks the text layer first and fails the document with an explicit
"this is a scan, run OCR on it" message instead.

### Integrity and access

Every artefact (template, upload, signature image, final PDF) is hashed with
SHA-256 when stored. `GET /api/download/{id}/verify` re-hashes the stored
document and compares it with what was recorded at signing.

Files are always served **through the API**, never by a link into the storage
layer. That keeps one authorisation check on every read, and it means the object
store never has to be reachable from a browser — a presigned URL pointing at an
internal host is unreachable, and one pointing at a public host bypasses
authorisation entirely.

Storage has two interchangeable backends, chosen with `STORAGE_BACKEND`:
`local` writes to a mounted volume (the default, and what small hosts should
use), `s3` talks to MinIO or Amazon S3. MinIO is behind a compose profile, so it
only runs when asked for: `docker compose --profile s3 up -d`.

## Schema notes

* `contract_fields` is authoritative; `contracts.extracted_data` is a read cache
  rebuilt from those rows.
* `UNIQUE (contract_id, template_field_id)` — without it, re-parsing a contract
  duplicates every field row instead of updating it.
* `ON DELETE RESTRICT` on `contracts.template_id`: a signed contract must never
  lose the template it was judged against. `CASCADE` on fields and signatures.
* `audit_logs.actor_id` is deliberately **not** a foreign key, so log rows
  survive user deletion.
* `updated_at` is maintained by an ORM hook and by a database trigger, so direct
  SQL writes stay honest.

## Deploying it privately

This app is built for a single operator handling their own driver files. It is
**not** designed to be exposed to the open internet, and nothing in it assumes a
public audience.

Recommended shape:

1. Run `docker compose up -d` on a machine you control.
2. Leave `FRONTEND_BIND=127.0.0.1` so nothing listens on a public interface.
3. Reach it over a private network — Tailscale or WireGuard — from your laptop
   or phone.

That gives encrypted transport, device-level authentication and zero public
attack surface, with no certificate to manage. If you do publish it, put it
behind a reverse proxy that terminates TLS (Caddy gets you a certificate
automatically), set `FRONTEND_BIND=0.0.0.0`, and set `CORS_ORIGINS` to your real
hostname.

> **Putting it into service for the first time?** Follow
> [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — a staged runbook with a check at
> the end of each step, including the template-validation stage that has to pass
> before the system handles anything real. No terminal on your own machine is
> needed; the runbook opens with the browser-only path.

### Deploying to a Google Cloud e2-micro (free tier)

e2-micro is a shared-core machine with **1 GB of RAM**, so the stack is
configured to fit rather than merely to start. `docker-compose.gcp.yml` caps
each service, drops every worker count to 1, and tunes Postgres down from
defaults sized for a much larger machine. Storage stays on the filesystem —
MinIO idles at a few hundred megabytes, a quarter of the machine, for no benefit
at this scale.

**1. Create the instance** (from your own machine):

```bash
gcloud compute instances create signwithme \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard
```

The free tier covers one e2-micro in `us-west1`, `us-central1` or `us-east1`,
with a 30 GB **standard** persistent disk. A balanced or larger disk, or any
other zone, is billed.

**2. Prepare it**:

```bash
gcloud compute ssh signwithme --zone=us-central1-a
git clone <your repo> && cd Signwithme1
bash scripts/gcp-setup.sh      # swap, Docker, log caps
exit                            # log back in for the docker group
```

Then verify the machine and your `.env` before starting anything:

```bash
./scripts/preflight.sh
```

Swap is not optional. With 1 GB and no swap the frontend image build is killed
by the OOM reaper part-way through, which presents as an unexplained hang.

**3. Configure and start**:

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # PII_ENCRYPTION_KEY
# keep STORAGE_BACKEND=local and FRONTEND_BIND=127.0.0.1

docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build
```

First build is 10–20 minutes on a shared core. Check `free -h` before diagnosing
a stall as anything other than memory.

**4. Reach it privately** — do *not* open port 3000 to the internet:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```

Then browse to `http://signwithme:3000` from any device on your tailnet. The app
stays bound to loopback, no firewall rule is added, and nothing is exposed.

**What this actually costs.** The compute and the 30 GB standard disk are free.
The **external IPv4 address is not** — Google has charged for all external IPv4
since 2024, roughly $3/month for an in-use ephemeral address. Running with
`--no-address` avoids it but then the VM cannot pull images without Cloud NAT,
which costs about the same. So budget ~$3/month, or run it on hardware you
already own for genuinely nothing. The free tier also includes only 1 GB of
egress per month; downloading signed PDFs counts against that.

**Operational notes for this machine.** Expect a 40-page parse to take
noticeably longer than on a laptop — the worker is capped at concurrency 1 and
recycles every 8 tasks, because a long-lived Python process never returns the
memory a large PDF spikes. Run `scripts/backup.sh` from cron and copy the output
off the instance; a 30 GB boot disk holding both the app and its only backup is
not a backup.

### Known limits of this deployment

* **Login throttling is per process.** Adequate for a single instance; it does
  not coordinate across replicas.
* **JWTs cannot be revoked** before they expire (8 hours by default). There is
  no password reset flow; an admin changes a password via the database.
* **No automatic backups.** `scripts/backup.sh` exists but nothing schedules it
  — add a cron entry.
* **`docker compose` was never executed in the environment this was built in**,
  so the composition is validated by review and by CI image builds, not by a
  live run. Expect to shake out one or two environment issues on first boot.

## Not yet built

* **`ai-service`** — the optional ML field detector for scanned PDFs from the
  original architecture. Not implemented, and deliberately absent from
  `docker-compose.yml` rather than declared as a service that would fail to
  build. Scanned documents are now *detected and reported* rather than silently
  mis-parsed; making them readable still needs OCR (`ocrmypdf` on the file
  beforehand, or pytesseract wired into `PdfParser`).
* **Cryptographic PDF signing.** Signatures are drawn images overlaid on the
  document, with SHA-256 hashes and a full audit trail for evidentiary value.
  PKI-backed digital signatures (PAdES) are a separate piece of work.
* **Detection accuracy on your real template is still unmeasured.** The detector
  has only ever been run against synthetic PDFs. Upload the real document, walk
  every page in the Template Manager, and correct the boxes that are wrong.
  The cross-field and FMCSA rules reference field ids the detector is *expected*
  to produce; a rule whose fields are absent is skipped rather than failed, so a
  mis-specified rule fails open — re-point them at real ids and verify with a
  deliberately non-compliant contract.

## Layout

```
backend/
  app/
    api/         templates, contracts, review, signatures, dates, download, auth
    core/        config, database, security (JWT + roles), exceptions
    models/      SQLAlchemy ORM
    schemas/     Pydantic request/response models
    services/    geometry, field_detector, pdf_parser, comparison_engine,
                 validators, pdf_builder, template_mapper, storage, audit
    workers/     Celery app and tasks
  alembic/       migrations
  tests/         47 tests, no database required
frontend/
  src/
    api/         typed client (auth, templates, contracts, signatures)
    components/  PdfViewer, FieldHighlighter, SignaturePad, ComparisonSidebar, …
    hooks/       usePdfViewer, useComparison, useSignature, useAuth
    pages/       Dashboard, TemplateManager, ContractUpload, ReviewPage,
                 SignaturePage, FinalContract, ReviewQueue, Login
    types/       field, template, contract, comparison
```
