# Full System Check — Findings

Audited: `demo/` Django project, 3,978 lines of Python across 10 apps.
Method: dependency install, `manage.py check`, `migrate`, smoke test of all 30
URLs, plus functional tests of the import and extraction paths.

Every item marked **[reproduced]** was proven by running the code, not by
reading it.

---

## 0. Does it run?

| Step | Result |
|---|---|
| `pip install -r requirements.txt` then boot | **FAILS** — see B1 |
| After installing the missing package: `manage.py check` | 0 issues |
| `makemigrations --check` | No missing migrations |
| `migrate` | OK |
| `manage.py test` | **0 tests exist** |
| All 30 URLs | 29 render, 1 crashes — see B8 |

---

## 1. Blockers

### B1 — `reportlab` missing from requirements.txt **[reproduced]**
`quotations/views.py:7` imports `reportlab`. It is not in `requirements.txt`.
Because `quotations.urls` is included in the root URLconf, the import failure
takes down **the entire site** — every page returns 500, not just quotations.
A fresh deployment cannot boot.
→ Add `reportlab>=4.0`.

### B2 — CSV import has effectively no duplicate check **[reproduced]**
`imports/views.py:112` is `Company.objects.get_or_create(company_name=c_name)`.
Exact string match on the name, nothing else. Test input — four rows for one
company, same email, same phone:

```
SD Steel Pvt Ltd      info@sdsteel.in   9876543210
SD Steel Pvt. Ltd.    info@sdsteel.in   9876543210
SD STEEL PVT LTD      INFO@SDSTEEL.IN   +91 98765 43210
SD Steel Pvt Ltd      info@sdsteel.in   9876543210   (byte-identical)
```

Result: **3 separate companies created.** Only the byte-identical row collapsed.
The client's single loudest requirement — *"No Duplicate data"* — does not hold
for the import path at all. Email and phone are never consulted.

### B3 — CSV import has no validation **[reproduced]**
Same test, a deliberately bad row was accepted and stored as-is:

```
company_email = 'not-an-email'
company_phone = '12'
website       = 'ftp://oops'
```

No email syntax check, no phone length check, no URL scheme check. The client's
second requirement — *"Data validation use"* — is absent on the import path.

### B4 — Import silently discards City and State **[reproduced]**
`imports/views.py:59-60` offers `city` and `state` in the column mapping UI, and
the mapping is saved, but `process_import` never reads them. No `Plant` row is
created. Verified: `plants=0` for every imported company. The client's
"Location" field is accepted from the user and then thrown away.

### B5 — Import never populates the normalized columns **[reproduced]**
`normalized_name` and `normalized_domain` stay `None` on every imported row.
The scraper's duplicate check matches on `normalized_domain`
(`extraction_service.py:68`). So a company imported from CSV can never be
recognised as a duplicate by the scraper, and will be created a second time.
Cross-channel dedupe is broken by construction.

### B6 — No pagination anywhere → the 10,000-lead requirement fails
`Paginator` appears nowhere in the codebase. `leads/index` loads every lead and
then runs a Python loop over all of them (`leads/views.py:10-36`).
`crm/pipeline_view` runs 8 unbounded queries and renders every lead as a card.
`companies_list` and `contacts_index` are the same. At the client's stated
10,000 leads these pages will time out or exhaust memory — on a phone, sooner.

### B7 — The duplicate review queue has no user interface
`DuplicateResolution` rows are created (`extraction_service.py:88`) with status
`PENDING`, and `job.needs_review` is incremented. But `grep` shows the model is
referenced only in the extraction service, the migration, and `admin.py`. There
is **no view, no URL, no template**. Duplicates accumulate forever and can only
be seen through Django admin — which a client who says *"আমি কিছু ভালো কম্পিউটার
জানি না"* will not use. The detection works; the resolution does not exist.

### B8 — Company search crashes **[reproduced]**
`leads/views.py:111` uses `Q(...)`, but `Q` is never imported in that module.
`GET /leads/companies/?q=steel` → `NameError: name 'Q' is not defined`.
The search box on the company list is dead.

### B9 — The 7/10-day follow-up reminder is not implemented
`Lead.follow_up_date` exists as a field. `grep` confirms **nothing ever writes
it**. There is no scheduler (`config/celery.py` has no beat schedule, there is
no cron command). `followups/` only supports manually creating a follow-up one
at a time. Client requirement #5 — *"৭ বা ১০ দিন পর ফলো-আপের রিমাইন্ডার আসবে"* —
has no implementation.

### B10 — Email campaigns require infrastructure that is not deployed
`emails/views.py:102` calls `send_campaign_task.delay(...)`, which needs a
Celery worker **and** Redis. Meanwhile `lead_generation/tasks.py` was
deliberately rewritten to use threads *because* Redis was not wanted. The two
halves of the system disagree. With no broker running, campaign creation either
errors or silently queues nothing. Adding Redis also works against the client's
"no mandatory subscription, ₹100–200/month hosting" constraint.

### B11 — The 10-emails-per-day cap does not exist
No counter, no quota model, no check. `emails/tasks.py` loops over every lead in
the campaign with a 2-second sleep. Requirement #4 (*"দিনে ১০টি করে ইমেইল"*) is
unenforced, and an uncapped send from a new domain is the fastest way to get the
client's domain blacklisted.

### B12 — Unsubscribe is cosmetic
`emails/views.py:110-118` decodes the address, shows a success message, and
**stores nothing**. The comment in the code says so outright. There is no
suppression model and no check before sending. Someone who unsubscribes will
keep receiving mail. This is a legal exposure for the client under India's DPDP
Act, and the unsubscribe URL is hardcoded to `http://127.0.0.1:8000`
(`emails/tasks.py:54`) so it would not work in production anyway.

---

## 2. High — lead-generation data quality

### H1 — Company name is taken from the search-result title **[reproduced]**
`lead_generator.py:889` sets `company_name = lead_data.result_title`. That is
the page title of a search hit. Test result — this was created as a company:

```
"Top 10 Sponge Iron Manufacturers in West Bengal"
```

It was even marked `VERIFIED`, because an email existed on the page. Listicles,
blogs and SEO pages become "companies". The name must be read from the site
itself (schema.org / footer / legal name), not from the search listing.

### H2 — "VERIFIED" does not mean verified **[reproduced]**
`extraction_service.py:118` sets `verification_status='VERIFIED'` whenever an
email *or* a phone string was found. `Lead.data_verified` uses the same test.
There is no MX lookup, no DNS check, no reachability test. The client asked for
*"maximum possible verified accuracy"* and this field will tell them a blog's
`ads@` address is verified. The label actively misleads.

### H3 — Phone numbers are stored unnormalized **[reproduced]**
`+91 9876543210` is stored with the space intact. The dedupe check at
`extraction_service.py:65` is an exact string match on `company_phone`, so
`9876543210`, `+919876543210` and `+91 9876543210` are three different
companies. Phone-based duplicate detection therefore almost never fires across
sources.

### H4 — The West Bengal coverage requirement cannot be met
State, district, city, industry and keywords are **hardcoded as `<option>` tags**
in `lead_generation/templates/lead_generation/index.html:43-99`. West Bengal
offers only **2 districts** (Paschim Bardhaman, Kolkata) and **3 cities**
(Durgapur, Asansol, Kolkata). West Bengal has 23 districts. Worse, `city` is
`required` and `run_generation_job` only builds a query when city **and**
district **and** state are all present (`lead_generator.py:806`), so a
district-wide or state-wide run is impossible. The client asked for *"All data
required across West Bengal"*.
The `Industry` and `SearchKeyword` database models that were meant to drive
these dropdowns exist but are never used by any view.

### H5 — No MCA/ROC data source
The client asked twice for *MCA/ROC registered companies* in specific
industries. There is no MCA or ROC integration. `CIN` and `GSTIN` are picked up
only if a company happens to print them on its own website
(`lead_generator.py:637-639`), which most do not. The `CIN` regex is also
slightly wrong — `[L|U]` is a character class containing a literal `|`, which
works by accident but should be `[LU]`. In practice this requirement is
currently unmet.

### H6 — Lead statuses do not match the client's list
Client asked for: New Lead, Called, Profile Sent, Follow-up Due, Requirement
Received, Converted, Not Relevant.
System has: NEW, CONTACTED, INTERESTED, REQUIREMENT_RECEIVED, QUOTATION_SENT,
NEGOTIATION, CONVERTED, NOT_INTERESTED, WRONG_CONTACT, INVALID, CLOSED_LOST.
**Missing: Called, Profile Sent, Follow-up Due, Not Relevant.** The dashboard
therefore cannot show the tiles the client listed. `Lead.profile_sent` is a
boolean that no code ever sets.

### H7 — An invalid status is written to the database
`followups/views.py:55` sets `lead.status = 'FOLLOW_UP'`. That value is not in
`Lead.STATUS_CHOICES`. Django does not enforce choices on `save()`, so the row
is written and then `get_status_display()` returns the raw string and the CRM
pipeline shows the lead in no column at all. Silent data corruption.

### H8 — Non-headless mode will kill the job on a server
The form exposes an `is_headless` checkbox. If unticked:
(a) Chrome cannot start on a VPS with no display, and
(b) on a CAPTCHA, `handle_manual_captcha` calls `input()`
    (`lead_generator.py:300`) inside a Gunicorn worker thread with no stdin →
    `EOFError` → job marked FAILED.
Headless mode handles CAPTCHA correctly (logs and falls back to Bing), so only
the non-headless path is broken. It should not be offered in production.

### H9 — Search-engine scraping is the primary source
Google and Bing are scraped with Selenium. This is against their terms, breaks
whenever their markup changes, and throttles quickly. The Google path correctly
filters directory and social domains via `normalize_google_url`, and falls back
to Bing on CAPTCHA — that part is sound. The concern is architectural: the
client's requirement (MCA/ROC registered companies, verified accuracy) points at
official registries, with web search as a supporting source, not the only one.

### H10 — `run_mode` is dead UI
Three radio buttons ("until stopped", "duration", "queue completed") are
rendered and read in `views.py:22`, then never stored or used. The client will
select an option that does nothing.

---

## 3. Medium

| # | Finding |
|---|---|
| M1 | Pause/Resume/Stop act on "the latest active job", not the job shown. With two jobs the wrong one is controlled. |
| M2 | `completeness_percentage` is computed in `leads/views.py:36` and never saved, so the dashboard's `incomplete_leads` counts every lead as 0% forever. |
| M3 | Duplicate counts on the dashboard and reports page are hardcoded `0` (`dashboard/views.py:38`, `reports/views.py:12`). |
| M4 | The threading worker dies with the web process. A job left `RUNNING` stays `RUNNING` forever — no heartbeat, no recovery, no way for the client to clear it. |
| M5 | `emails/tasks.py:110` references `subject` in the `except` block; if the failure happens before line 65 this raises `NameError` and hides the real error. |
| M6 | No `DEFAULT_FROM_EMAIL`. Sent mail has no configured sender identity. |
| M7 | `leads/views.py:49` reads `company.emails`, which does not exist (`EmailLog` points at `Lead`). Email history never appears on the lead page — it silently shows empty. |
| M8 | Zero tests in the whole project. Nothing protects the dedupe or validation rules from regressing. |
| M9 | `process_import` runs synchronously inside the HTTP request. 10,000 rows will exceed any gateway timeout. No row-count or file-size limit either. |
| M10 | Column mapping is stored in the **session** (`imports/views.py:34`). Session loss or a different device mid-flow leaves the mapping page blank. |
| M11 | `normalize_site_url` keeps the URL path, so `site.in/` and `site.in/products` count as two websites and the same company is crawled twice. |
| M12 | SQLite plus a background writer thread plus a browsing user risks `database is locked` during generation. Needs WAL mode and a timeout. |
| M13 | `quotations/views.py:55-58` calls `float()` on form input with no guard → 500 on a typo. |
| M14 | Dead code: `Industry` and `SearchKeyword` models are unused. |
| M15 | Demo polish: hardcoded fake notifications in `templates/base.html:97-120`, logo links to `index.html`, Google Analytics left as `[ANALYTICS_ID_PLACEHOLDER]`. |
| M16 | `lead_generation/services/` has no `__init__.py`. It works via namespace packages but is inconsistent with the rest of the project. |
| M17 | `extract_contacts_from_current_page` is annotated `-> tuple[set[str], set[str]]` but returns 5 values. |
| M18 | `driver.quit()` is called both on the STOPPED path and again in `finally`. |

---

## 4. What already works well

Credit where due — these parts are sound and should be kept:

- The **scraper-side duplicate hierarchy** (CIN → GSTIN → email → phone → domain
  → name+city) is the right idea, and domain matching was **verified working**:
  a second hit on `www.sdsteel.in/contact` was correctly detected as a duplicate
  of `sdsteel.in` and queued for review instead of being blindly overwritten.
  This logic just needs to be extracted into shared code and given a UI.
- Bad emails (`a@b`) and implausible phones (`2023`) are **correctly rejected**
  on the scraper path. The validation exists here — it is the import path that
  lacks it.
- `robots.txt` is checked before every page fetch, and politely: an unreadable
  robots.txt does not become an excuse to crawl.
- Headless CAPTCHA handling degrades gracefully to the Bing fallback.
- Directory/social domains are filtered out of search results.
- Empty company names are skipped rather than stored.
- Audit logging is wired through every state change.
- Migrations are clean and complete; `manage.py check` is silent.
- The UI is a responsive Bootstrap admin theme — the mobile requirement is
  achievable, and all referenced static assets are present.

---

## 5. Requirement traceability

| # | Client requirement | Status | Blocked by |
|---|---|---|---|
| R1 | 10,000+ leads | ✗ Not viable | B6 |
| R2 | 33 data fields | ◐ ~28 of 33 in the model | H5, H6 |
| R3 | CSV/Excel import | ◐ Works, loses data | B4 |
| R4 | Duplicate check | ✗ Import: none. Scraper: detects, cannot resolve | B2, B5, B7, H3 |
| R5 | Data validation | ✗ Import: none. Scraper: partial, mislabelled | B3, H2 |
| R6 | Client's 7 statuses | ✗ 4 of 7 missing | H6, H7 |
| R7 | Dashboard tiles | ◐ Renders, several counts are wrong or fake | M2, M3, H6 |
| R8 | Manual email + record | ✓ Works | — |
| R8b | 10 emails/day cap | ✗ Not implemented | B11 |
| R9 | 7/10-day follow-up reminder | ✗ Not implemented | B9 |
| R10 | Mobile + browser login | ✓ Responsive theme, login works | — |
| R11 | All of West Bengal | ✗ 2 of 23 districts, city mandatory | H4 |
| R12 | MCA/ROC companies | ✗ No source | H5 |
| R13 | No mandatory subscription | ◐ At risk — campaigns need Redis | B10 |

**Legend:** ✓ done · ◐ partial · ✗ not met

Score: 2 of 14 fully met. The two the client repeated most often — no duplicates
and data validation — are the two furthest from done.

---

## 6. Recommended order of work

1. **B1** — one line in `requirements.txt`. Without it nothing deploys.
2. **B8, H7** — two-line fixes, both cause visible breakage.
3. **B2, B3, B4, B5** — build `core/validation.py` + `core/dedupe.py` and route
   both the importer and the scraper through them. This is the heart of the
   client's request.
4. **B7** — the duplicate review screen. Detection without resolution is not a
   feature.
5. **B6, M2, M3** — pagination and honest dashboard numbers.
6. **H6** — statuses to the client's exact list, then the dashboard tiles.
7. **B9, B11, B12** — follow-up automation, daily cap, real unsubscribe.
8. **B10** — replace Celery/Redis with the cron worker so there is no
   subscription.
9. **H1, H2, H3, H4** — generation data quality and full West Bengal coverage.
10. **H5** — MCA/ROC source. Needs a client decision first (see the questions
    document).
