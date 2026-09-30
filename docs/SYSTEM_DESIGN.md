# S.D Enterprise — Lead Generation & CRM: System Design

Version 1.0 · Prepared for client review before implementation.

---

## 1. What the client asked for

From the WhatsApp requirement (29–30 Sep):

| # | Requirement |
|---|---|
| R1 | Lead database, 10,000+ companies |
| R2 | 33 data fields (Company → Remarks → Last Updated) |
| R3 | CSV / Excel import of new leads |
| R4 | Duplicate check on company / email / phone |
| R5 | Data validation |
| R6 | Lead status: New, Called, Profile Sent, Follow-up Due, Requirement Received, Converted, Not Relevant |
| R7 | Dashboard with counts per status |
| R8 | Manual email send, ~10/day, with sent record |
| R9 | Follow-up reminder 7 or 10 days after Profile Sent |
| R10 | Mobile + laptop, browser login, no software to learn |
| R11 | Target data: Iron & Steel, Sponge Iron/DRI, Foundry, Rolling Mills, Ferro Alloys, Industrial Gas — all West Bengal |
| R12 | MCA/ROC registered companies |
| R13 | One-time cost, own database, no mandatory subscription |

---

## 2. Design principles

1. **One validation module, one dedupe module.** Both the scraper and the CSV
   importer call the same code. This is the single most important change: today
   they have separate, inconsistent logic, so a lead cleaned by one path is
   dirty by the other.
2. **No Redis, no Celery, no paid API in v1.** Background work runs from a
   database-backed queue driven by one cron entry. This keeps the running cost
   at hosting + domain only, as the client requires.
3. **Never silently guess.** A field is either validated, or explicitly marked
   unverified. No field is labelled "VERIFIED" just because a string was found.
4. **Humans decide the grey cases.** Certain duplicates auto-merge; uncertain
   ones go to a review screen the client can use on a phone.

---

## 3. Architecture

```
                    Browser (mobile + laptop)
                              │  HTTPS
                    ┌─────────▼─────────┐
                    │  Nginx (TLS)      │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Gunicorn         │
                    │  Django app       │
                    └─────────┬─────────┘
                              │
   ┌──────────────────────────┼──────────────────────────┐
   │                          │                          │
┌──▼──────────┐   ┌───────────▼───────────┐   ┌──────────▼────────┐
│ Web views   │   │  core/  (shared)      │   │  Database         │
│ dashboard   │   │   validation.py       │   │  SQLite (v1)      │
│ leads       │   │   dedupe.py           │   │  → Postgres later │
│ crm         │   │   normalize.py        │   └───────────────────┘
│ imports     │   │   quality.py          │
│ emails      │   └───────────▲───────────┘
│ followups   │               │
└─────────────┘   ┌───────────┴───────────┐
                  │  Workers (cron, 1/min)│
                  │   run_jobs            │  ← scraping, import
                  │   send_queued_emails  │  ← 10/day cap
                  │   mark_followups_due  │  ← 7/10 day rule
                  └───────────────────────┘
```

Single small VPS. No message broker. `cron` runs one management command every
minute; that command picks up whatever work the database says is pending.

---

## 4. Data model (golden record)

The rule: **raw values are kept, normalized values are what we match on.**

```
Company                         ← the golden record, one row per real company
  company_name                    as displayed
  legal_name                      from MCA/ROC if available
  normalized_name                 lowercase, legal suffixes stripped  [indexed]
  normalized_domain               host, no www                        [unique]
  normalized_email                lowercase, trimmed                  [indexed]
  normalized_phone                E.164, e.g. +919876543210           [indexed]
  cin / gstin                     checksum-validated                  [unique]
  industry, company_type
  data_quality_score              0-100, computed and stored
  email_status                    UNKNOWN | SYNTAX_BAD | MX_OK | BOUNCED
  phone_status                    UNKNOWN | FORMAT_BAD | PLAUSIBLE
  identity_status                 UNVERIFIED | ROC_MATCHED | GST_MATCHED
  source, first_seen, last_updated

Plant       (1 Company → many)  plant_name, address, city, district, state, pin, lat/lng
Contact     (1 Company → many)  name, designation, department, email, mobile, direct_phone
Lead        (1 Company → many)  status, profile_sent_at, follow_up_date, requirement,
                                quotation, remarks, lead_source, source_url
Suppression                     email / phone that must never be contacted again
EmailQuota                      date + count, enforces the 10/day cap
DuplicateReview                 candidate pair + score + reason + decision
```

Unique constraints do the final safety check at the database level, so a bug in
application code still cannot create two rows for one company.

---

## 5. Lead generation pipeline

Six stages. Each stage writes its own status so the client can see where a lead
came from and why it was kept or dropped.

```
 1. DISCOVER   Build queries from (industry keyword × district × state).
               Sources, in order of trust:
                 a. MCA/ROC company master data  → gives CIN, legal name, address
                 b. GST taxpayer search          → gives GSTIN, trade name
                 c. Web search (Bing/Google)     → gives website
               Directory and social domains are excluded.

 2. RESOLVE    For each candidate, find the official website.
               Reject domains that are blogs, directories, aggregators,
               marketplaces or listicles.

 3. EXTRACT    Open home + up to 4 likely contact pages. Respect robots.txt.
               Pull: company name (from <title>/schema.org/footer — NOT the
               search-result title), emails, phones, address, PIN, CIN, GSTIN,
               LinkedIn.

 4. NORMALIZE  core/normalize.py
               name → strip "Pvt Ltd/Private Limited/Ltd/LLP", collapse spaces
               phone → E.164 (+91…), drop non-Indian and short numbers
               email → lowercase, strip role-noise (info@, sales@ kept but flagged)
               domain → host without www

 5. VALIDATE   core/validation.py  (three tiers, see §6)

 6. RECONCILE  core/dedupe.py  (three tiers, see §7)
               → NEW  |  MERGED  |  NEEDS_REVIEW  |  REJECTED
```

**Important design decision:** company name must come from the *website*, not
from the search-result title. The current system uses the title, which is why
rows like "Top 10 Sponge Iron Manufacturers in West Bengal" become companies.

---

## 6. Validation design

Three tiers. Tier 1 and 2 are free and offline. Tier 3 is free but needs
network. Tier 4 is optional and paid — **not** in v1.

| Tier | Check | Cost | Result |
|---|---|---|---|
| 1 Syntax | email regex + a real TLD; phone 10 digits starting 6-9 for India; URL scheme; PIN 6 digits; CIN 21-char pattern; GSTIN 15-char **with checksum** | free | reject or pass |
| 2 Plausibility | reject repeated digits (9999999999), sequences, years (2023), placeholder emails (`example@`, `test@`, `noreply@`), image-file false positives | free | reject or pass |
| 3 Deliverability | DNS **MX lookup** on the email domain; domain resolves; website returns 200 | free (`dnspython`) | `MX_OK` / `NO_MX` |
| 4 Deep verify | SMTP mailbox probe / paid verification API | paid | optional later |

Each lead then gets a **data quality score** (stored, not recomputed per page
load):

```
company name from website .... 20
plant address + PIN .......... 15
validated phone .............. 15
validated email (MX_OK) ...... 20
website reachable ............ 10
CIN or GSTIN matched ......... 15
named contact person .........  5
                              ----
                               100
```

Bands shown in the UI: **A (80-100) ready to contact · B (60-79) usable ·
C (40-59) thin · D (<40) needs work**.

This replaces the current `verification_status = "VERIFIED"`, which today means
only "some email or phone string was found on the page". That label is
misleading and the client asked specifically for verified accuracy.

---

## 7. Duplicate check design

Runs identically for scraped leads and imported CSV rows.

**Tier 1 — hard keys (auto-merge, no human needed)**
Match on any one of: `cin`, `gstin`, `normalized_domain`, `normalized_email`,
`normalized_phone`. These identify a company beyond doubt.

**Tier 2 — blocked fuzzy match (score it)**
Candidates are restricted ("blocked") to the same PIN, city or domain, so we
never compare all 10,000 rows against each other. Within a block:

```
normalized name similarity (token-set ratio) .... 50
same PIN code .................................. 20
same city ...................................... 10
same phone last 8 digits ....................... 20
```

- score ≥ 85 → auto-merge
- 60 ≤ score < 85 → **DuplicateReview** queue
- score < 60 → treat as new

**Tier 3 — human review screen**
A mobile-friendly page showing the two records side by side with three buttons:
**Keep existing · Use new · Merge field-by-field**. Every decision is written to
the audit log.

**Merge rule when auto-merging:** never overwrite a validated value with an
unvalidated one. Fill blanks, upgrade unverified → verified, and append the new
source URL to the company's source history.

**Within-file dedupe:** a CSV is deduplicated against itself *before* any row is
written, and the client gets a summary — `imported / merged / duplicates /
rejected`, with a downloadable rejected-rows file showing the reason per row.

---

## 8. Import design

```
Upload → read header → map columns → DRY RUN → review → COMMIT
```

The **dry run** is the key addition. Nothing is written until the client has
seen what will happen: how many rows are new, how many merge into existing
companies, how many are rejected and why. Import of 10,000 rows runs in the
cron worker in batches, not inside the web request, so the browser never times
out.

---

## 9. CRM, email, follow-up

**Lead status** — exactly the client's list, no extras:

```
NEW → CALLED → PROFILE_SENT → FOLLOW_UP_DUE → REQUIREMENT_RECEIVED → CONVERTED
                                                                    ↘ NOT_RELEVANT
```

**Email (manual-first, as the client wants):**
- Compose from a lead, with saved templates and attachment.
- Hard cap of 10 sends/day, enforced by the `EmailQuota` row — the button
  disables itself and shows "8 of 10 sent today".
- Suppression list checked before every send. An unsubscribe click actually
  writes a row and blocks all future sends to that address.
- Every send logged: recipient, subject, body, attachment, status, error.
- Gmail / Zoho SMTP — free at 10/day, no paid email API needed.

**Follow-up (the client's 7/10 day rule):**
When status becomes `PROFILE_SENT`, the system sets
`follow_up_date = today + N` where N is 7 or 10, configurable in settings. The
cron job flips those leads to `FOLLOW_UP_DUE` each morning and they appear in
the dashboard's "Follow-up Due" tile. Nothing is forgotten, and no external
reminder service is needed.

**Dashboard** — one card per status, exactly the client's list, plus today's
email count and follow-ups due. Server-side aggregation, so it stays fast at
10,000 leads.

---

## 10. 10,000-lead readiness

| Concern | Design |
|---|---|
| List pages | Server-side pagination, 50/page. Never load all rows. |
| Search | Indexed on `normalized_name`, `normalized_domain`, `normalized_phone`, `status` |
| Dashboard | Single aggregate query, no Python loops over leads |
| Export | Streamed CSV, chunked — not a full in-memory Excel build |
| Dedupe | Blocking keys, so cost grows linearly, not as 10,000² |
| Database | SQLite with WAL is fine to ~10-20k leads with one user. Postgres is a config change, no code change, when the client grows. |

---

## 11. Deployment

- One small VPS (1 vCPU / 1 GB), Ubuntu.
- Nginx + Gunicorn + systemd. TLS via Let's Encrypt (free, auto-renew).
- One cron entry, every minute, runs the worker command.
- Nightly `sqlite3 .backup` to a dated file + weekly off-server copy. The
  client can download a full database backup from the UI at any time — this is
  what "my data is mine" means in practice.
- No Redis, no RabbitMQ, no paid SaaS. **No mandatory subscription.**

---

## 12. Legal and ethical boundaries

These protect the client's own business and domain reputation:

- Only publicly published business contact details are collected. No personal
  or sensitive data.
- `robots.txt` is honoured; rate-limited, polite crawling.
- Search-engine scraping is fragile and against those engines' terms. Official
  sources (MCA/ROC, GST) and the companies' own websites are the primary
  sources; web search is a supporting source only.
- Every outbound email carries sender identity and a working unsubscribe link.
  Unsubscribes are permanent and enforced in code.
- India's DPDP Act and anti-spam expectations are the reason the suppression
  list and the daily cap are non-negotiable parts of the design.

---

## 13. Build order

| Phase | Scope |
|---|---|
| 1 | `core/` — normalize, validate, dedupe, quality score + unit tests |
| 2 | Data model migration to the golden record; backfill normalized columns |
| 3 | Import v2: dry run, self-dedupe, rejected-rows report |
| 4 | Duplicate review screen |
| 5 | CRM statuses to the client's list, dashboard tiles, pagination |
| 6 | Email: quota, suppression, working unsubscribe, logging |
| 7 | Follow-up automation (7/10 day) via cron |
| 8 | Generation v2: official sources first, name from website, quality gate |
| 9 | Deploy, backups, 20–30 lead sample demo |

Phases 1–4 are what make the client's two headline requirements — *no duplicate
data, data validation* — actually true. They come first.
