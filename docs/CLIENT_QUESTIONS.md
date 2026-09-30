# Open Questions for the Client — and the 5 Questions They Asked

Two parts:
**Part A** — what we still need from the client before building.
**Part B** — the five questions they asked, and what the code says the honest
answer is.

---

## Part A — Questions to ask the client

These are the points where the chat is ambiguous enough that guessing would mean
building the wrong thing.

### A1. "Verified" — how verified? *(most important question)*

The client said *"maximum possible verified accuracy"*. That phrase has three
very different price tags:

| Level | What it means | Cost | Accuracy |
|---|---|---|---|
| 1 | Format checks only — the email looks like an email | free | low |
| 2 | Format + **MX/DNS check** — the email's domain really accepts mail | free | good |
| 3 | Level 2 + paid mailbox verification API | per-lead fee | best |

**Recommend Level 2**, and say plainly that a lead marked verified means *the
address is real and the domain accepts mail*, not *a human replied*. Ask whether
Level 2 is acceptable, because the current system does Level 1 and calls it
"VERIFIED", which would eventually cost the client trust with their own
customers.

### A2. MCA/ROC — where does this data come from, and who pays?

Asked for twice, and it is the requirement with no implementation at all.
Options:

- (a) MCA company master data bulk download — free where available, but it gives
  CIN, registered address and status, **not** phone or email.
- (b) MCA V3 portal lookups — some views are chargeable per document.
- (c) A commercial data provider — gives contacts too, but is a recurring cost
  the client has already said they want to avoid.
- (d) Skip MCA/ROC in v1. Collect CIN/GSTIN only where a company publishes it,
  and add registry matching later.

Ask which one. And ask this directly: **is a lead without a CIN still useful to
you?** If yes, (d) fits the budget. If no, the project needs a paid data source
and the budget has to move.

*Note: current MCA/GST portal access rules and fees must be confirmed before
quoting — they change.*

### A3. How many leads for the first delivery?

The client answered "10,000" for *database capacity*, but never said how many
leads should be **delivered**. These are completely different jobs — the
capacity is a few days of engineering; 10,000 genuinely verified leads is months
of crawling plus manual checking. West Bengal probably does not even contain
10,000 companies in these six industries.

Ask for the delivery number: 200? 500? 2,000? And confirm the split:
**"System capacity 10,000 — first data delivery N leads. Is that right?"**

### A4. Which of the 33 fields are mandatory?

Fields like Contact Person, Designation, Department, Direct Phone, GSTIN and
LinkedIn are simply not published by most small West Bengal foundries and
rolling mills. Realistically, roughly 10–15 of the 33 fields will be filled for
a typical lead.

Ask the client to mark **must-have vs nice-to-have**, and agree what counts as a
deliverable lead. Suggested minimum: *company name + industry + city/district +
(validated phone OR validated email)*. Without this agreement there will be a
disagreement at delivery.

### A5. Which email address will actually send the mail?

They gave `enquiry@sdenterpriseindia.in`. Confirm:
- Do they already own that domain and mailbox, or is it still to be bought?
- Gmail/Google Workspace, Zoho, or the hosting provider's mail?
- **Who holds the app password?** It must go in the server's environment, never
  in the code or the database.

At 10/day, a free Zoho or Gmail mailbox is sufficient — no paid email API.

### A6. Follow-up: 7 days or 10?

The chat says "7 or 10". Pick a default. Recommend **7 days**, configurable in
settings. Also ask: after the first follow-up, should it repeat (e.g. +7 again),
and how many times before the lead is dropped?

### A7. How many people will log in?

The chat only ever mentions the client. If it is one user, skip roles entirely
and save time. If a staff member will join later, say so now — adding
multi-user access after the fact is more work than building it in.

### A8. Who owns and pays for the server?

"My data in my database" is best served by the **client owning the hosting
account and the domain**, with developer access granted. Confirm:
- Client's own VPS/hosting account, or the developer's?
- Who renews the domain?
- Backups: automatic nightly on the server is standard — does the client also
  want a downloadable backup button in the UI? (Recommended, and cheap to add.)

### A9. Manual vs automatic email — they asked for both rates

The client asked for both prices and will choose. Worth telling them plainly:
**they should start with manual.** At 10 emails/day, automation saves almost no
time, and automated cold email from a brand-new domain is the fastest way to get
that domain blacklisted. Manual sending with saved templates gives them the same
speed with none of the risk. Automation can be added once the domain has a
sending reputation.

### A10. What happens to the leads already in the system?

If any data has been collected during the demo, decide whether it is kept.
Given the findings (duplicated companies, unvalidated emails, page titles saved
as company names), the honest recommendation is to **re-run the collection after
the fixes** rather than migrate dirty rows forward.

---

## Part B — The five questions the client asked

> They asked: (1) Is it possible in my budget? (2) One-time development cost?
> (3) Any monthly/yearly mandatory charge? (4) Hosting, domain, email/API costs?
> (5) Do I keep full control of the database and my lead data?

**(1) and (2) — budget and one-time cost.**
This is the developer's own commercial call and is not set here. But the audit
gives the facts needed to answer it honestly: **the system is not "almost
done"**. Two of the client's fourteen requirements are fully met. The two the
client repeated most — no duplicates, data validation — are the two furthest
from working. On the import path the duplicate check does not function at all:
the same company with the same email and the same phone was stored three times
in testing.

The useful move is to **split the quote**:

- **System (software)** — one-time, fixed. Build phases 1–7 of the design: the
  validation and duplicate engine, the review screen, the client's statuses, the
  dashboard, the daily cap, the follow-up automation. Scoped and finishable.
- **Data (leads)** — priced per lead or per batch, because it is ongoing work
  that scales with A3. Bundling free data into a fixed software price is what
  makes these projects lose money.

Say clearly which requirements are inside the fixed price and which are not.
MCA/ROC (A2) in particular should be excluded until the client answers.

**(3) Monthly/yearly mandatory charges — answer: no, none, by design.**
The design deliberately uses no Redis, no Celery broker, no n8n, no Make, no
paid API. Background work runs from one cron entry. The only recurring costs are
hosting and the domain, both of which the client pays directly to the provider.
⚠ One caveat: the **current** code's email campaign feature calls Celery, which
needs Redis. That is fix B10 — replacing it with the cron worker is what keeps
this promise true.

**(4) Hosting, domain, email.**
- Hosting: a 1 vCPU / 1 GB VPS is enough for one user and ~10,000 leads.
- TLS certificate: free (Let's Encrypt, auto-renewing).
- Email: free at 10/day on a standard Zoho or Gmail mailbox. No paid email API.
- Domain: already quoted to the client.
- Confirm current provider prices before committing to the ₹100–200/month figure
  — quote the provider's live price, not a remembered one.

**(5) Data ownership — answer: yes, fully.**
Django + SQLite (or PostgreSQL later) on the client's own server. The database
is a file on their machine. Recommend also giving them a **"Download full
backup"** button in the UI, plus automatic nightly backups. Best arrangement:
the hosting account and the domain are in the **client's** name, with developer
access granted — then ownership is a fact, not a promise.

---

## One thing to raise with the client, plainly

The requirement list is reasonable on its own. The gap is between the *data*
expectation and the budget: verified contact data for MCA/ROC-registered
companies across all 23 districts of West Bengal is a data-sourcing project, and
data sourcing costs either money or time regardless of how the software is
built.

The software itself can absolutely be built simple, mobile-friendly and
subscription-free, as designed. The recommendation is therefore:

1. Fix and finish the **system** at a fixed one-time price.
2. Deliver a **20–30 lead verified sample** from West Bengal first, as already
   promised in the chat.
3. Let the client see the real fill rate per field on that sample, then price the
   bulk data with both sides knowing what is actually achievable.

That sequence protects the client from paying for something undeliverable, and
protects the developer from promising 10,000 verified leads for a fixed fee.
