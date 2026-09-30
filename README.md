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

## Quick start

```bash
cd demo
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium                        # only for lead generation

python manage.py migrate
python manage.py seed_reference_data               # districts, industries, templates
python manage.py create_demo_data                  # optional sample leads
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in.

In a second terminal, for lead generation, large imports and reminders:

```bash
python manage.py run_worker --loop
```

There is no Redis and no Celery. The database is the queue.

`create_demo_data` loads eight realistic West Bengal companies **plus three
deliberately bad rows** — a duplicate spelling, an invalid email and phone, and
a search-result listicle — so the duplicate check and the validation can be
watched working rather than described.

---

## How it is put together

```
demo/
  core/              the shared data-quality engine — read this first
    normalize.py       canonical name, phone (E.164), email, domain, PIN
    validation.py      three tiers of checks, each with a stated reason
    record.py          CleanRecord: the one shape every lead takes
    dedupe.py          three tiers of duplicate detection
    ingest.py          the ONLY code that writes a lead to the database
    quality.py         the 0-100 data quality score
    worker.py          background work, without a message broker
  leads/             companies, plants, contacts, leads, the review queue
  lead_generation/   the crawler (engine.py) and the job control screen
  imports/           CSV/Excel import with a dry run
  emails/            templates, sending, the daily cap, suppression
  followups/         the 7/10-day reminder and hand-scheduled tasks
  crm/               the pipeline board and activity trail
  quotations/        quotations and their PDFs
  reports/           data quality report and audit log
  dashboard/         sign-in, the dashboard, public pages
  deploy/            systemd, nginx and cron examples
```

**`core/ingest.py` is the only write path.** The crawler and the importer both
go through it, so there is one set of rules for validation and duplicates and no
way for the two to disagree.

### How duplicates are decided

1. **Hard keys** — same CIN, GSTIN, website domain, email or E.164 phone.
   Certain, so it merges automatically and only ever adds information.
2. **Scored match** — normalised name (50) + same PIN (20) + same city (10) +
   same phone tail (20), with a bonus when the normalised names are identical.
   85 or more merges; 60–84 goes to review; under 60 is a new company.
3. **Human review** — a side-by-side screen with four choices: merge, keep
   mine, use the new data, or not the same.

Within one file, rows are also de-duplicated against each other before anything
is written.

### How validation is tiered

| Tier | Check | Cost |
|---|---|---|
| 1 | Syntax: email, Indian phone, URL, PIN, CIN, GSTIN checksum | free |
| 2 | Plausibility: placeholders, repeated digits, `noreply@`, directory domains, listicle titles | free |
| 3 | Deliverability: a DNS MX lookup on the email domain | free |
| 4 | Paid mailbox probe | **not built** — see the note on "verified" below |

Nothing is labelled verified without a check behind it. A company marked as
having a checked email means its domain accepts mail — not that a person
replied.

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

## Deployment

One small VPS (1 vCPU / 1 GB) is enough for one user and 10,000+ leads.
The slowest page at 10,000 leads is 80 ms.

```bash
# as root
apt update && apt install -y python3-venv nginx
adduser --system --group --home /srv/leadcrm leadcrm

# as leadcrm
cd /srv/leadcrm && git clone <your-repo> app && cd app/demo
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium

# create your environment file here (see Settings below), then:
python manage.py migrate
python manage.py seed_reference_data
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Then install the examples from `demo/deploy/`:

```bash
cp deploy/leadcrm.service deploy/leadcrm-worker.service /etc/systemd/system/
cp deploy/nginx.conf /etc/nginx/sites-available/leadcrm
ln -s /etc/nginx/sites-available/leadcrm /etc/nginx/sites-enabled/

systemctl enable --now leadcrm leadcrm-worker
nginx -t && systemctl reload nginx
```

HTTPS, free and auto-renewing:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d crm.yourdomain.in
```

Then set `ENFORCE_HTTPS=True` and `SITE_URL=https://crm.yourdomain.in`, and
`systemctl restart leadcrm`.

### Or run the worker from cron instead of systemd

```cron
* * * * * cd /srv/leadcrm/app/demo && .venv/bin/python manage.py run_worker --once
15 7 * * * cd /srv/leadcrm/app/demo && .venv/bin/python manage.py process_followups
```

`--once` does a single pass and exits, so it is safe every minute.
See `demo/deploy/crontab.example`.

### Backups

The whole database is one file:

```cron
30 2 * * * cd /srv/leadcrm/app/demo && sqlite3 db.sqlite3 ".backup '/srv/leadcrm/backups/db-$(date +\%F).sqlite3'" && find /srv/leadcrm/backups -name 'db-*' -mtime +30 -delete
```

Copy that directory off the server weekly — a backup on the same disk is not a
backup. The client's lead data lives entirely in that one file, which is what
"my data is mine" means in practice.

---

## Settings

Read from the environment, or from an environment file next to `manage.py`.
Everything has a working default except the first four.

### Essential in production

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Django's signing key. **Set a long random value.** |
| `DEBUG` | `False` in production |
| `ALLOWED_HOSTS` | Comma separated, e.g. `crm.yourdomain.in` |
| `SITE_URL` | Full base URL. Builds the unsubscribe links inside outgoing email, so it must be right. |

### Email

| Variable | Default | Notes |
|---|---|---|
| `EMAIL_HOST` | `smtp.gmail.com` | Zoho: `smtp.zoho.in` |
| `EMAIL_PORT` | `587` | |
| `EMAIL_USER` | *(blank)* | The mailbox address |
| `EMAIL_PASSWORD` | *(blank)* | An **app password**, not the account password |
| `DEFAULT_FROM_EMAIL` | `EMAIL_USER` | What recipients see |
| `EMAIL_DAILY_LIMIT` | `10` | Hard cap, enforced in code |
| `COMPANY_NAME` | `S.D Enterprise` | Signature and unsubscribe footer |
| `COMPANY_ADDRESS` | `West Bengal, India` | Signature footer |

With no `EMAIL_USER` / `EMAIL_PASSWORD`, mail is printed to the console instead
of sent — which is what lets the system be demonstrated before the client's
mailbox exists.

### Business rules

| Variable | Default | Notes |
|---|---|---|
| `FOLLOW_UP_DELAY_DAYS` | `7` | Days after Profile Sent before the reminder |
| `FOLLOW_UP_REPEAT_DAYS` | `7` | Gap between later reminders |
| `FOLLOW_UP_MAX_ROUNDS` | `3` | Stop chasing after this many |
| `MIN_QUALITY_TO_CONTACT` | `40` | Below this a lead is flagged as thin |
| `PAGE_SIZE` | `50` | Rows per page on every list |
| `SESSION_HOURS` | `12` | How long a sign-in lasts |

### Data quality, crawler, database

| Variable | Default | Notes |
|---|---|---|
| `VALIDATE_EMAIL_MX` | `True` | DNS check that the mail domain accepts mail. Needs outbound DNS; set `False` offline. |
| `CHROME_BINARY` | *(blank)* | Only if Playwright's browser is not on the default path |
| `WORKER_AUTOSTART` | `True` | Also start work in a thread on click, not only from cron |
| `DATABASE_URL` | *(blank)* | A `postgres://…` URL switches off SQLite. Also `pip install psycopg2-binary dj-database-url`. |
| `ENFORCE_HTTPS` | `False` | `True` once the certificate is installed |
| `TIME_ZONE` | `Asia/Kolkata` | |

---

## When lead generation finds nothing

Run the diagnostic **on the server itself**. It checks the browser and asks
every search source in turn, and changes nothing in the database:

```bash
python manage.py test_search
```

It will tell you which of three things is wrong:

| It says | Meaning | Fix |
|---|---|---|
| *the browser would not start* | Playwright's browser is missing | `playwright install --with-deps chromium`, or set `CHROME_BINARY` to its full path |
| *UNREACHABLE* | No outbound HTTPS or no DNS | Check the firewall: `curl -I https://html.duckduckgo.com/` |
| *BLOCKED* | The engine is refusing this server — it shows the page it landed on, so you can confirm | See below |
| *WORKED, but no result was a company site* | The engine answered fine; the results were directories or list pages | It prints how many links it saw and why each was dropped. Usually a narrower query fixes it. |

Add `--debug` to save each results page to a file, when the counts are not
enough to explain what came back.

To test extraction on one site, without searching at all:

```bash
python manage.py test_search --url https://somecompany.in
```

### If the search engines are blocking you

This is the common one, and it is not a fault in the code or your data. Search
engines rate-limit servers that query them in bulk, and a data-centre IP address
makes it more likely. Three sources are tried in order — DuckDuckGo, then Bing,
then Google — and a source that refuses twice is dropped for the rest of the
run, so a blocked run now fails in seconds with an explanation instead of
grinding for hours and reporting "0 leads".

In order of what usually works:

1. **Wait an hour** and run again. The limit is usually temporary.
2. **Run a smaller search** — one district and one keyword, not the whole state.
3. **Import from CSV instead.** This never touches a search engine, and is the
   reliable path for bulk data.
4. If it keeps happening, the server needs a different IP address.

### Why a state-wide run is capped

A whole state with seven keywords is 161 searches — most of a day's crawling,
and impossible to tell apart from a stuck run. A run is capped at 40 searches
(`MAX_QUERIES_PER_RUN` in `lead_generation/engine.py`) and the queries are
ordered **keyword first, then district**, so the first pass sweeps every
district rather than spending all seven keywords on the first one. Run it again
to go deeper.

---

## Maintenance commands

| Command | What it does |
|---|---|
| `run_worker --once` | One pass of all pending work. Safe every minute. |
| `run_worker --loop` | Keep running (use under systemd). |
| `process_followups` | Mark leads whose reminder date has arrived. |
| `seed_reference_data` | Load districts, industries, keywords, email templates. Idempotent. |
| `backfill_normalized` | Rebuild the matching keys and quality scores. Run after upgrading, or after changing a normalisation rule. `--dry-run` to preview. |
| `create_demo_data` | Sample leads for a demonstration. `--clear` wipes first. |
| `test_search` | Diagnose lead generation on the server: browser, network, and each search source. Changes nothing. |

### Upgrading

```bash
git pull && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py backfill_normalized     # if normalisation rules changed
systemctl restart leadcrm leadcrm-worker
```

---

## What it costs to run

| Item | Cost |
|---|---|
| Software | None. No subscription, no licence, no per-lead fee. |
| Hosting | One small VPS. Confirm the current price with your provider. |
| Domain | Yearly, to the registrar. |
| TLS certificate | Free (Let's Encrypt, auto-renewing). |
| Email | Free at 10/day on a standard Zoho or Gmail mailbox. |
| Lead generation | Free. Playwright and Python only — no scraping API, no data provider. |

There is deliberately no Redis, no Celery broker, no n8n and no Make, because
each would add a service to pay for and monitor.

---

## Two design notes

**Playwright, not Selenium.** Selenium needs a ChromeDriver matching the
installed Chrome, and that mismatch breaks on any browser update — the most
common failure for this kind of tool. Playwright installs a browser it is built
against. Its sync API keeps an event loop running and Django refuses ORM calls
from inside one, so rather than disabling that safety check, the crawler runs in
its own thread and hands plain data back through a queue: the browser never
touches the database.

**The browser presents a normal desktop identity.** Playwright's default
announces `HeadlessChrome` and sets `navigator.webdriver`, and search engines
block that on sight — it was the main reason lead generation returned nothing.
The context now sends a real Chrome user agent, `en-IN` locale and an Indian
time zone, which also makes the results India-relevant. This is not a CAPTCHA
bypass: when a challenge or consent wall appears, the run detects it, moves to
another source, and stops with an explanation if none will answer.

**A row whose contact details are all invalid is kept, not rejected.** The
company name is real data the client typed. It is imported and counted
separately as "imported with a field dropped", listed in the preview and in the
download. Silently dropping it would lose their work; silently keeping it would
hide the problem.

---

## Still open

- **MCA/ROC registry feed.** The client asked twice for MCA/ROC registered
  companies. CIN and GSTIN are validated wherever a company publishes them, but
  there is no registry integration — MCA bulk data gives a CIN and registered
  address but no phone or email, so this needs a decision on source and budget
  before it is built.
- **Paid mailbox verification (tier 4).** The free DNS check is in place;
  per-lead paid verification is deliberately not built.
- **Multi-user roles.** Only one login is described in the requirement.

---

## Boundaries

Only contact details that companies publish themselves are collected.
`robots.txt` is honoured on every fetch, crawling is rate-limited, and CAPTCHA
is never bypassed — a blocked search falls back to another source and carries
on. Every outgoing email identifies the sender and carries a working unsubscribe
link, and an unsubscribe is permanent and enforced in code.
