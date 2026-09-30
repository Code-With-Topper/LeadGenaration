# How to use the system

Written for the person using it, not the person who built it. No technical
knowledge needed. Everything works the same on a phone and on a laptop.

---

## Signing in

Go to your web address, type your username and password, press **Sign in**.
That is all — nothing to install, nothing to learn.

---

## The Dashboard

The first screen after signing in. One box per stage of your work:

| Box | Meaning |
|---|---|
| **Total Leads** | Every company in your database |
| **New Lead** | Not contacted yet |
| **Called** | You have spoken to them |
| **Profile Sent** | You have sent your company profile |
| **Follow-up Due** | Time to chase them |
| **Requirement Received** | They have told you what they need |
| **Converted** | They became a customer |
| **Not Relevant** | Not worth pursuing |

Tap any box to see just those companies.

If anything needs your attention — an overdue follow-up, or a possible
duplicate to check — a red panel appears at the top. If there is no red panel,
nothing is waiting for you.

---

## Adding leads

### From a file you already have

1. **Import CSV/Excel** in the menu.
2. Choose your file and press **Upload and check**.
3. The system matches your column headings by itself. Look them over and
   correct anything wrong, then press **Next**.
4. **Read the preview.** Nothing has been saved yet. It shows:
   - how many companies are **new**
   - how many are **duplicates** of companies you already have
   - how many need **your review**
   - how many are **rejected**, and why
5. If it looks right, press **Import**.

You can press **Download the problem rows** to get a file listing every row
that was rejected or had a field dropped, with the reason next to each one.
Nothing disappears without an explanation.

Only the **company name** is required. A row with fewer details still comes in;
it just scores lower on quality.

If you are not sure what your file should look like, press
**Download a template file** — it has the right headings and one example row.

### By searching for new companies

1. **Find New Leads** in the menu.
2. Choose the **state**, and optionally a **district** and **city**.
   Leaving the district blank searches **every district of West Bengal** —
   all 23.
3. Choose the **industry**. The search phrases fill in by themselves; you can
   add your own, separated by commas.
4. Choose how many websites to check, and press **Start Searching**.

Watch it work. You can **Pause**, **Resume** or **Stop** at any time.
Companies appear in the list as they are found, with a quality grade.

You can close the page and come back — the search keeps going.

---

## Duplicates

The system never stores the same company twice.

- When it is **certain** (same email, same phone, same website, same CIN or
  GSTIN), it merges them by itself and fills in any blanks. You are not asked.
- When it is only **probably** the same company, it goes to
  **Duplicate Review** and waits for you.

On the review screen you see the two records side by side, with the differences
highlighted, and four buttons:

- **Merge them** — keep one company, take any new details
- **Keep what I have** — throw the new record away
- **Use the new data** — replace the old details
- **Not the same** — save it as a separate company

You get the next one automatically, so a batch can be cleared quickly.

---

## Quality grades

Every company gets a grade, so you know where to spend your time:

| Grade | Meaning |
|---|---|
| **A** | Ready to contact |
| **B** | Usable |
| **C** | Thin — some details missing |
| **D** | Needs work |

Open any company and it tells you exactly what is missing. Filling those in
raises the grade.

**Important:** a company marked as having a checked email means the system
confirmed that the email's domain accepts mail. It does **not** mean a person
has replied. Nothing here is labelled verified unless a real check was done.

---

## Sending email

1. Open a lead and press **Send Email**.
2. Pick a template — the company name and contact name fill in by themselves.
3. Attach your profile or rate list if you want.
4. Leave **"This is the company profile"** ticked if you are sending your
   profile. The system will then remind you to follow up.
5. Press **Send**.

### The daily limit

You can send **10 emails a day**. The screen always shows how many are left.

This limit is there on purpose. Sending a lot of email from a new domain is the
fastest way to have your messages treated as spam — which would affect all your
mail, not just the new ones. Ten a day, every day, is 300 a month.

### People who unsubscribe

Every email carries an unsubscribe link. If someone uses it:

- their address is blocked permanently — the system will refuse to send to them
- the lead is closed as **Not Relevant**

You can see the list under **Do Not Contact**, and add addresses by hand if
someone asks you by phone.

---

## Follow-ups

This is the part that stops leads being forgotten.

When you mark a profile as sent, the system sets a reminder for **7 days later**
(you can choose 10). On that day the lead moves to **Follow-up Due** and appears
on the **Follow-ups** page and on your dashboard.

The Follow-ups page has three sections:

- **Overdue** — chase these first
- **Due today**
- **Coming up** — the next two weeks

Next to each one, **Email** sends a message straight away, and **+7d** pushes
the reminder a week later if now is not the time.

You can also schedule your own tasks from any lead — a call back on Tuesday, a
site visit next month.

---

## The Pipeline

**Pipeline** shows your leads as cards in columns, one column per stage.
Drag a card to another column to move that lead.

Dragging a card to **Profile Sent** sets the follow-up reminder too.

---

## Working with a lead

Open any company to see everything in one place: phone, email, website,
address, CIN, GSTIN, contact people, every email you have sent, your notes, and
any quotations.

You can record:

- **Requirement** — what they need
- **Quotation reference**
- **Remarks** — anything you want to remember
- **Next follow-up date**

And at the bottom of the page, where each detail came from — which website, and
which page on it.

---

## Quotations

From a lead, press **Quotation**. Add your items with quantity, rate, tax and
discount. The system works out the totals.

Then you can download it as a **PDF**, or **email it** — which goes through the
same daily limit and unsubscribe checks as any other message.

Marking a quotation **Accepted** moves the lead to **Converted**.

---

## Getting your data out

**Export** on the lead list downloads everything as a CSV file you can open in
Excel — all 33 fields, including your notes and follow-up dates. It respects
whatever filter you have applied.

Your data is yours. It lives in one database file on your own server, and you
can take a full copy whenever you like.

---

## Two reports

- **Data Quality** — how good your data is, how many companies you can
  actually contact, and where the leads came from.
- **Audit Log** — every change, who made it and when.

---

## If something looks wrong

- **A lead shows the wrong details.** Open it and edit them. Everything on the
  lead page can be corrected.
- **A search found nothing.** Check the log on the Find New Leads page. Search
  engines sometimes slow us down; try again later, or use a different phrase.
- **An email would not send.** Look at **Email History** — every attempt is
  recorded there with the exact reason it failed or was blocked.
- **A search seems stuck.** Press **Stop**, then start a new one. Nothing is
  lost; the companies already found are saved.
