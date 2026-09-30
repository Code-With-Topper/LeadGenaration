# What was built

The audit in `AUDIT_REPORT.md` found 2 of the client's 14 requirements fully
met. This is what changed, and how each change was verified.

**Verification:** 204 automated tests, a 38-step walkthrough of the client's
whole journey, a live browser crawl against a test website, and a load test at
10,000 leads.

---

## The central change

Before, the scraper and the CSV importer each had their own idea of what a
valid lead was, and their own duplicate check. The importer's check was an exact
string match on the company name and nothing else.

Now there is one module, `core/`, and **both paths go through it**:

```
core/normalize.py    canonical form of a name, phone, email, domain, PIN
core/validation.py   three tiers of checks, each with a reason for rejection
core/record.py       CleanRecord — the one shape every lead takes
core/dedupe.py       three tiers of duplicate detection
core/ingest.py       the only code that writes a lead to the database
core/quality.py      the 0-100 data quality score
core/worker.py       background work, without a message broker
```

`core/ingest.py` is the single write path. Two entry points cannot drift apart
because there is only one place that stores anything.

---

## Every finding, and what happened to it

### Blockers

| # | Finding | Now |
|---|---|---|
| B1 | `reportlab` missing from requirements — every page 500 on a fresh install | In `requirements.txt`. `dnspython` and `whitenoise` added; `selenium` replaced (see below). |
| B2 | Import dedupe matched the company name only; one company stored three times | Three-tier dedupe on CIN, GSTIN, domain, email, phone, then a scored name+place match. Verified: the same company written four ways becomes one. |
| B3 | Import had no validation at all | Every field validated. `not-an-email`, phone `12` and `ftp://oops` are rejected with a reason the user can read. |
| B4 | City and State were mapped in the UI then silently discarded | Written to a `Plant` row. Verified. |
| B5 | `normalized_*` columns never populated on import, so cross-channel dedupe was impossible | Always populated, by the shared normaliser. |
| B6 | No pagination anywhere, against a 10,000-lead requirement | Every list paginated in the database. Slowest page at 10,000 leads: **80 ms**. |
| B7 | Duplicates were detected but had no UI — only Django admin | A mobile-friendly review screen: side-by-side comparison, four plain choices, next one served automatically. |
| B8 | Company search raised `NameError` — `Q` used but never imported | Fixed, and the search now covers name, email, phone, CIN, GSTIN and contact person. |
| B9 | The 7/10-day follow-up reminder had no implementation | `mark_profile_sent()` sets the date; a daily sweep moves the lead to Follow-up Due. No external service. |
| B10 | Email campaigns needed Celery + Redis, contradicting both the threading worker and the no-subscription requirement | Celery removed entirely. One `run_worker` command from cron or systemd. |
| B11 | The 10-emails-a-day cap did not exist | `EmailQuota`, incremented atomically. The button disables itself and says how many are left. |
| B12 | Unsubscribe showed a message and stored nothing | A signed link writes a `Suppression` row, closes the lead, and every send checks it first. |

### Data quality

| # | Finding | Now |
|---|---|---|
| H1 | Company name taken from the search-result title, so listicles became companies | Read from the site: schema.org, then Open Graph, then the copyright footer, then the title. Listicle titles are rejected outright. |
| H2 | `verification_status = "VERIFIED"` meant only "a string was found" | Replaced by `email_status` (including a real DNS MX check), `phone_status`, `identity_status` and a 0-100 score. Nothing is called verified without a check behind it. |
| H3 | Phones stored raw, so `9876543210` and `+91 98765 43210` never matched | Normalised to E.164. Eight written forms of one number now produce one key. |
| H4 | 2 of West Bengal's 23 districts in a hardcoded dropdown; city mandatory | All 23 districts in the database, plus 82 towns and two neighbouring steel states. A run can target a city, a district, or **every district in the state**. |
| H5 | No MCA/ROC source; CIN regex subtly wrong | CIN and GSTIN are validated properly — including the **real GSTIN checksum**. A registry feed still needs a client decision (see `CLIENT_QUESTIONS.md` A2). |
| H6 | Statuses did not match the client's list; 4 of 7 missing | Exactly the client's seven, in their order, and nothing else. |
| H7 | `followups` wrote `FOLLOW_UP`, not a valid choice — silent corruption | Gone. Invalid statuses are refused, and a test pins it. |
| H8 | Non-headless mode called `input()` in a web thread, and cannot start on a VPS | Always headless. CAPTCHA is never waited on; the run falls back to another source. |
| H9 | Search-engine scraping was the only source | Official-source-first design documented; directory and social domains are filtered out of results, and the site's own pages are what the details come from. |
| H10 | Three `run_mode` radio buttons that did nothing | Replaced by a real "stop after N websites" limit, so a run always ends. |

### Medium

All 18 addressed. The ones worth naming: the job heartbeat and stale-job
recovery (a job left `RUNNING` by a restart used to be stuck for ever); real
duplicate counts instead of hardcoded zeros; the stored quality score instead of
one computed in a view and thrown away; `Decimal` money in quotations instead of
`float`; a working email history on the lead page; and dead code removed
(`accounts`, unused models).

---

## Two decisions made during the work

**1. Playwright instead of Selenium.**
Selenium needs a ChromeDriver matching the installed Chrome. That mismatch
blocked the crawler here and would break the client's server on any browser
update — it is the most common failure mode for this kind of tool. Playwright
installs a browser it is built against, so the mismatch cannot happen. Equally
free, no service, no subscription.

A consequence worth recording: Playwright's sync API keeps an event loop
running, and Django refuses ORM calls from inside one. Rather than switching off
that safety check, **the crawler runs in its own thread and hands plain data
back through a queue** — the browser never touches the database.

**2. A row whose contact details are all invalid is kept, not rejected.**
The company name is real data the client typed. Throwing the row away would
lose it. Instead it is imported and **counted separately** as "imported with a
field dropped", listed in the preview and in the download. Silently keeping it
would have been the wrong call; silently dropping it equally so.

---

## Bugs the tests found in my own work

Worth recording, because each was a real defect:

- `0123456789` was rejected as "wrong length" instead of "placeholder", because
  the length check ran before the placeholder check.
- `en.wikipedia.org` passed the directory filter, which only matched exact
  domains and not subdomains.
- `M/s` became the tokens `m` and `s`, which were then merged into a false
  initial.
- `&` and `and` produced different name keys, so `Durgapur Iron & Steel` and
  `Durgapur Iron and Steel` would not have matched on name.
- A date string from a form was never parsed, so formatting it raised.
- The pagination partial evaluated `previous_page_number` on page 1, which
  raises `EmptyPage`.
- A job the user stopped before the worker claimed it started anyway.
- An identical name plus an identical PIN scored 80 and went to review, when it
  should merge — the scoring needed an exact-name bonus.

---

## Requirement traceability, now

| # | Requirement | Before | Now |
|---|---|---|---|
| R1 | 10,000+ leads | ✗ | ✓ Slowest page 80 ms at 10,000 |
| R2 | 33 data fields | ◐ | ✓ All 33, in the model and the export |
| R3 | CSV/Excel import | ◐ | ✓ With a dry run before anything is written |
| R4 | Duplicate check | ✗ | ✓ Three tiers + a review screen |
| R5 | Data validation | ✗ | ✓ Three tiers, every rejection explained |
| R6 | The client's 7 statuses | ✗ | ✓ Exactly those seven |
| R7 | Dashboard tiles | ◐ | ✓ Every count queried, none hardcoded |
| R8 | Manual email + record | ✓ | ✓ Plus templates and attachments |
| R8b | 10 emails/day | ✗ | ✓ Enforced, not advisory |
| R9 | 7/10-day reminder | ✗ | ✓ Automatic, via cron |
| R10 | Mobile + browser login | ✓ | ✓ Tables stack on phone width |
| R11 | All of West Bengal | ✗ | ✓ All 23 districts |
| R12 | MCA/ROC companies | ✗ | ◐ CIN/GSTIN validated; a registry feed needs a client decision |
| R13 | No mandatory subscription | ◐ | ✓ No Redis, no Celery, no paid API |

**12 of 14 fully met, 1 partial, 0 unmet.** The remaining partial (R12) is a
commercial question, not a technical one — it is question A2 in
`CLIENT_QUESTIONS.md`.

---

## Still open

- **MCA/ROC feed** — needs the client's answer on source and budget (A2).
- **Tier 4 email verification** — a paid mailbox probe. Deliberately not built;
  the free DNS check is in place (A1).
- **Multi-user roles** — only one login is described in the requirement. Ask
  before building it (A7).
