# Setup & Deployment

Two parts: getting it running on a laptop, and putting it on a server.

> **A note on `.env` files:** I do not open or edit `.env` files, so the
> repository's existing `.env.example` has been left exactly as it was and may
> not list the newer settings. Every variable the system reads is documented in
> §3 below — copy what you need from there into your own `.env`.

---

## 1. Run it locally

```bash
cd demo
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium         # only needed for lead generation

python manage.py migrate
python manage.py seed_reference_data     # districts, industries, templates
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in.

To see the system with data in it:

```bash
python manage.py create_demo_data
```

That loads eight realistic West Bengal companies **plus three deliberately bad
rows** — a duplicate spelling, an invalid email and phone, and a search-result
listicle — so the duplicate check and the validation can be watched working
rather than described.

### Run the background worker

Lead generation, large imports and the follow-up reminders all run from one
command. While developing, leave it running in a second terminal:

```bash
python manage.py run_worker --loop
```

There is no Redis and no Celery. The database is the queue.

---

## 2. Put it on a server

One small VPS (1 vCPU / 1 GB) is enough for one user and 10,000+ leads.

```bash
# --- once, as root ---
apt update && apt install -y python3-venv nginx
adduser --system --group --home /srv/leadcrm leadcrm

# --- as the leadcrm user ---
cd /srv/leadcrm
git clone <your-repo> app && cd app/demo
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium

# create your .env here (see §3), then:
python manage.py migrate
python manage.py seed_reference_data
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Then install the three files in `deploy/`:

```bash
cp deploy/leadcrm.service    /etc/systemd/system/
cp deploy/leadcrm-worker.service /etc/systemd/system/
cp deploy/nginx.conf         /etc/nginx/sites-available/leadcrm
ln -s /etc/nginx/sites-available/leadcrm /etc/nginx/sites-enabled/

systemctl enable --now leadcrm leadcrm-worker
nginx -t && systemctl reload nginx
```

HTTPS, free and auto-renewing:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d crm.yourdomain.in
```

Then set `ENFORCE_HTTPS=True` and `SITE_URL=https://crm.yourdomain.in` in your
`.env` and `systemctl restart leadcrm`.

### Reminders, if you prefer cron to systemd

The worker can run from cron instead of as a service:

```cron
* * * * * cd /srv/leadcrm/app/demo && .venv/bin/python manage.py run_worker --once
15 7 * * * cd /srv/leadcrm/app/demo && .venv/bin/python manage.py process_followups
```

`--once` does a single pass and exits, so it is safe to run every minute.

---

## 3. Settings

Everything below is read from the environment (or a `.env` file next to
`manage.py`). All of it has a working default except the first three.

### Essential in production

| Variable | What it does |
|---|---|
| `SECRET_KEY` | Django's signing key. **Set a long random value.** |
| `DEBUG` | `False` in production. |
| `ALLOWED_HOSTS` | Comma separated, e.g. `crm.yourdomain.in` |
| `SITE_URL` | Full base URL. Used to build the unsubscribe links inside outgoing email, so it must be right. |

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

With no `EMAIL_USER`/`EMAIL_PASSWORD` set, mail is printed to the console
instead of sent. That is what lets the system be demonstrated before the
client's mailbox exists.

### Business rules

| Variable | Default | Notes |
|---|---|---|
| `FOLLOW_UP_DELAY_DAYS` | `7` | Days after Profile Sent before the reminder |
| `FOLLOW_UP_REPEAT_DAYS` | `7` | Gap between later reminders |
| `FOLLOW_UP_MAX_ROUNDS` | `3` | Stop chasing after this many |
| `MIN_QUALITY_TO_CONTACT` | `40` | Below this a lead is flagged as thin |
| `PAGE_SIZE` | `50` | Rows per page on every list |
| `SESSION_HOURS` | `12` | How long a sign-in lasts |

### Data quality

| Variable | Default | Notes |
|---|---|---|
| `VALIDATE_EMAIL_MX` | `True` | DNS check that the mail domain accepts mail. Free, but needs outbound DNS. Set `False` offline. |

### Lead generation

| Variable | Default | Notes |
|---|---|---|
| `CHROME_BINARY` | *(blank)* | Only if Playwright's browser is not on the default path |
| `WORKER_AUTOSTART` | `True` | Start work in a thread on click as well as from cron |

### Database and HTTPS

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(blank)* | Set to a `postgres://…` URL to switch off SQLite. Also `pip install psycopg2-binary dj-database-url`. |
| `ENFORCE_HTTPS` | `False` | `True` once the certificate is installed |
| `TIME_ZONE` | `Asia/Kolkata` | |

---

## 4. Backups

The whole database is one file. Nightly, from cron:

```cron
30 2 * * * cd /srv/leadcrm/app/demo && sqlite3 db.sqlite3 ".backup '/srv/leadcrm/backups/db-$(date +\%F).sqlite3'" && find /srv/leadcrm/backups -name 'db-*' -mtime +30 -delete
```

Copy that directory off the server weekly — a backup on the same disk is not a
backup. The client's lead data lives entirely in that one file, which is what
"my data is mine" means in practice.

---

## 5. Maintenance commands

| Command | What it does |
|---|---|
| `run_worker --once` | One pass of all pending work. Safe every minute. |
| `run_worker --loop` | Keep running (use under systemd). |
| `process_followups` | Mark leads whose reminder date has arrived. |
| `seed_reference_data` | Load districts, industries, keywords, email templates. Idempotent. |
| `backfill_normalized` | Rebuild the matching keys and quality scores. Run after upgrading, or after changing a normalisation rule. `--dry-run` to preview. |
| `create_demo_data` | Sample leads for a demonstration. `--clear` wipes first. |

---

## 6. Upgrading

```bash
git pull
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py backfill_normalized     # if normalisation rules changed
systemctl restart leadcrm leadcrm-worker
```

---

## 7. What it costs to run

| Item | Cost |
|---|---|
| Software | None. No subscription, no licence, no per-lead fee. |
| Hosting | One small VPS. Confirm the current price with your provider. |
| Domain | Yearly, paid to the registrar. |
| TLS certificate | Free (Let's Encrypt, auto-renewing). |
| Email | Free at 10/day on a standard Zoho or Gmail mailbox. |
| Lead generation | Free. Playwright and Python only — no scraping API, no data provider. |

There is deliberately no Redis, no Celery broker, no n8n and no Make, because
each would add a service to pay for and monitor.
