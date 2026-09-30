# Lead Generation & CRM — S.D Enterprise

A small, self-hosted system for collecting industrial B2B leads in West Bengal
and working them through to a sale. Django + SQLite. No subscription, no
message broker, no paid API.

Built for a client who is not technical, on a one-time budget, and who asked for
two things above all: **no duplicate data** and **data validation**.

---

## What it does

| | |
|---|---|
| **Find leads** | Search by industry across a city, a district, or all 23 districts of West Bengal. Reads contact details a company publishes on its own website. |
| **Import leads** | CSV or Excel, with a dry run that shows what will happen before anything is written. |
| **Never duplicate** | Three tiers: certain matches merge automatically, uncertain ones go to a review screen. |
| **Validate** | Email (including a DNS check that the domain accepts mail), Indian phone numbers, PIN codes, CIN, and GSTIN with its real checksum. |
| **Work the pipeline** | Seven statuses, a drag-and-drop board, notes and requirements per lead. |
| **Email** | Manual sending with templates and attachments, capped at 10 a day, with a working unsubscribe list. |
| **Never forget a lead** | A reminder 7 or 10 days after the profile is sent, raised automatically each morning. |
| **Quotations** | Line items, tax, discount, a PDF, and email delivery. |
| **On a phone** | Every screen works at phone width. |

---

## Start here

- **[SETUP.md](demo/SETUP.md)** — run it locally, then deploy it
- **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — for the person using it, no technical knowledge assumed
- **[docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)** — the architecture and why
- **[docs/WHAT_CHANGED.md](docs/WHAT_CHANGED.md)** — what was rebuilt, and how it was verified
- **[docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md)** — the findings that prompted the rebuild
- **[docs/CLIENT_QUESTIONS.md](docs/CLIENT_QUESTIONS.md)** — what still needs the client's answer

```bash
cd demo
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python manage.py migrate
python manage.py seed_reference_data
python manage.py create_demo_data        # optional: sample leads, including messy ones
python manage.py createsuperuser
python manage.py runserver
```

In a second terminal, for lead generation and reminders:

```bash
python manage.py run_worker --loop
```

---

## How it is put together

```
demo/
  core/              the shared data-quality engine — read this first
    normalize.py       canonical form of a name, phone, email, domain, PIN
    validation.py      three tiers of checks, each with a stated reason
    record.py          CleanRecord: the one shape every lead takes
    dedupe.py          three tiers of duplicate detection
    ingest.py          the ONLY code that writes a lead to the database
    quality.py         the 0-100 data quality score
    worker.py          background work, without a message broker
  leads/             companies, plants, contacts, leads, the review queue
  lead_generation/   the crawler and the job control screen
  imports/           CSV/Excel import with a dry run
  emails/            templates, sending, the daily cap, suppression
  followups/         the 7/10-day reminder and hand-scheduled tasks
  crm/               the pipeline board and activity trail
  quotations/        quotations and their PDFs
  reports/           data quality report and audit log
  dashboard/         sign-in, the dashboard, public pages
  deploy/            systemd, nginx and cron examples
```

The important design decision is that **`core/ingest.py` is the only write
path.** The crawler and the importer both go through it, so there is one set of
rules for validation and duplicates and no way for the two to disagree.

---

## Running the tests

```bash
cd demo
python manage.py test          # 204 tests
```

They cover the parts that would cost the client money if they broke: the
duplicate rules, the validators, the daily email cap, the unsubscribe list and
the follow-up reminder.

---

## What it costs to run

Software: nothing. Hosting: one small VPS. Domain: yearly, to the registrar.
TLS: free. Email: free at 10 a day on a standard mailbox.

No Redis, no Celery broker, no n8n, no Make, no scraping API — each would be a
service to pay for and monitor, and the client asked specifically to avoid them.

---

## Boundaries

Only contact details that companies publish themselves are collected.
`robots.txt` is honoured on every fetch, crawling is rate-limited, and CAPTCHA
is never bypassed — a blocked search falls back to another source and carries
on. Every outgoing email identifies the sender and carries a working unsubscribe
link, and an unsubscribe is permanent and enforced in code.
