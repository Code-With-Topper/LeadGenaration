from __future__ import annotations

"""
Lead Generation Engine
======================

Purpose
-------
Search Google for publicly listed company/provider websites with Selenium,
open those websites, inspect a small number of likely contact pages, and
extract publicly displayed email addresses and phone numbers.

This module is designed to be used as the "Lead Generation" layer of a
larger CRM system. It writes a normalized CSV that can later be processed by
validation, duplicate-checking, import, and CRM modules.

Important
---------
- This script does NOT bypass CAPTCHA. If Google presents a verification page,
  the browser is left open so the user can solve it manually.
- Use only publicly available business/contact information.
- Respect applicable site terms, robots.txt, privacy laws, and anti-spam laws.
- Do not use this tool to collect sensitive personal information.
"""

import argparse
import csv
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SEARCH_QUERIES = [
    "iron steel manufacturing West Bengal company contact",
    "sponge iron DRI West Bengal company contact",
    "foundry cast iron West Bengal company contact",
    "rolling mill West Bengal company contact",
    "ferro alloys West Bengal company contact",
    "industrial gas manufacturing West Bengal company contact",
]

DEFAULT_OUTPUT = "generated/leads.csv"

MAX_RESULTS_PER_QUERY = 20
MAX_PAGES_PER_SITE = 4

DELAY_MIN_SECONDS = 2.0
DELAY_MAX_SECONDS = 5.0

PAGE_LOAD_TIMEOUT_SECONDS = 35
WAIT_TIMEOUT_SECONDS = 15

CHROME_PROFILE = None
CHROMEDRIVER_PATH = None
HEADLESS = False

# Search/social/directory sites are not treated as company websites.
SKIP_RESULT_DOMAINS = (
    "google.",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "justdial.com",
    "sulekha.com",
    "urbanpro.com",
    "indiamart.com",
    "wikipedia.org",
)

CONTACT_LINK_KEYWORDS = (
    "contact",
    "about",
    "enquiry",
    "enquire",
    "admission",
    "training",
    "course",
    "support",
    "reach",
    "connect",
)

EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

PHONE_RE = re.compile(
    r"""
    (?:
        (?:\+?\d{1,3}[\s().-]*)?
        (?:\(?\d{2,5}\)?[\s().-]*)?
        \d{3,5}[\s().-]*\d{3,5}
        (?:[\s().-]*\d{1,5})?
    )
    """,
    re.VERBOSE,
)

CIN_RE = re.compile(r"\b[L|U]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", re.IGNORECASE)
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}[Z]{1}[A-Z\d]{1}\b", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Z0-9_-]+", re.IGNORECASE)

# Values that are clearly not useful contact numbers.
INVALID_PHONE_PATTERNS = (
    re.compile(r"^20\d{2}20\d{2}$"),
    re.compile(r"^1234567890$"),
    re.compile(r"^0123456789$"),
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("lead_generator")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LeadResult:
    source_query: str
    result_title: str
    result_url: str
    last_page_checked: str = ""
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    cin: str = ""
    gstin: str = ""
    linkedin_url: str = ""

    @property
    def primary_email(self) -> str:
        return sorted(self.emails)[0] if self.emails else ""

    @property
    def primary_phone(self) -> str:
        return sorted(self.phones)[0] if self.phones else ""

    @property
    def website(self) -> str:
        return self.result_url


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def make_driver(args: argparse.Namespace) -> webdriver.Chrome:
    options = Options()
    options.page_load_strategy = "eager"

    if args.headless:
        options.add_argument("--headless=new")
    else:
        options.add_argument("--start-maximized")

    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")

    # Reduce unnecessary browser automation noise; this is not a CAPTCHA
    # bypass and should not be treated as one.
    options.add_argument("--disable-notifications")

    if args.chrome_profile:
        options.add_argument(f"--user-data-dir={args.chrome_profile}")

    if args.chromedriver:
        from selenium.webdriver.chrome.service import Service

        driver = webdriver.Chrome(
            service=Service(args.chromedriver),
            options=options,
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(args.page_load_timeout)
    driver.set_script_timeout(args.page_load_timeout)

    return driver


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def polite_sleep(args: argparse.Namespace) -> None:
    delay = random.uniform(args.delay_min, args.delay_max)
    time.sleep(delay)


def wait_for_page(
    driver: webdriver.Chrome,
    timeout: int = WAIT_TIMEOUT_SECONDS,
) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def load_page(
    driver: webdriver.Chrome,
    url: str,
    timeout: int,
) -> bool:
    try:
        driver.get(url)
        wait_for_page(driver, timeout=min(timeout, WAIT_TIMEOUT_SECONDS))
        return True

    except TimeoutException:
        LOGGER.warning("Timeout while loading %s; stopping page load.", url)
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
        return True

    except WebDriverException as exc:
        LOGGER.warning("Skipping %s: %s", url, exc)
        return False

    except Exception as exc:
        LOGGER.warning("Unexpected error loading %s: %s", url, exc)
        return False


def visible_text(driver: webdriver.Chrome) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text or ""
    except WebDriverException:
        return ""


# ---------------------------------------------------------------------------
# CAPTCHA / verification handling
# ---------------------------------------------------------------------------

def is_captcha_or_block_page(driver: webdriver.Chrome) -> bool:
    url = driver.current_url.lower()
    title = driver.title.lower()
    text = visible_text(driver).lower()

    markers = (
        "captcha",
        "unusual traffic",
        "not a robot",
        "verify you are human",
        "our systems have detected",
    )

    return (
        "google.com/sorry" in url
        or "recaptcha" in url
        or "sorry" in title
        or any(marker in title for marker in markers)
        or any(marker in text for marker in markers)
    )


def handle_manual_captcha(driver: webdriver.Chrome, args: argparse.Namespace) -> bool:
    if not is_captcha_or_block_page(driver):
        return True

    if args.headless:
        LOGGER.warning("CAPTCHA detected in Headless Mode. Bypassing site automatically to prevent hang.")
        return False

    print(
        "\nCAPTCHA / verification page detected.\n"
        "Please solve it manually in the open Chrome window.\n"
    )

    input("After the normal page is visible again, press Enter to continue...")
    return True


# ---------------------------------------------------------------------------
# Robots.txt
# ---------------------------------------------------------------------------

def can_fetch_url(url: str, user_agent: str = "*") -> bool:
    """
    Best-effort robots.txt check.

    If robots.txt cannot be retrieved, return True rather than pretending
    that the site has explicitly permitted crawling. The caller should still
    obey site terms and applicable law.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False

        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.read()

        return parser.can_fetch(user_agent, url)

    except Exception:
        LOGGER.info("Could not evaluate robots.txt for %s.", url)
        return True


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def normalize_google_url(href: str) -> str | None:
    if not href:
        return None

    parsed = urlparse(href)

    # Google often uses /url?q=<actual-url>
    if parsed.path == "/url":
        query = parse_qs(parsed.query)
        href = query.get("q", [None])[0]

    if not href:
        return None

    if not href.startswith(("http://", "https://")):
        return None

    href = href.split("#", 1)[0]

    domain = urlparse(href).netloc.lower()

    if any(skip_domain in domain for skip_domain in SKIP_RESULT_DOMAINS):
        return None

    return href


def normalize_site_url(url: str) -> str:
    """
    Normalize a website URL enough for duplicate comparison without
    destroying the actual navigable URL.
    """
    parsed = urlparse(url)

    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")

    return f"{scheme}://{host}{path}"


def same_site(url_a: str, url_b: str) -> bool:
    host_a = urlparse(url_a).netloc.lower().removeprefix("www.")
    host_b = urlparse(url_b).netloc.lower().removeprefix("www.")

    return host_a == host_b


# ---------------------------------------------------------------------------
# Google search
# ---------------------------------------------------------------------------



def _decode_bing_url(href: str) -> str:
    """Decode a Bing tracking/redirect URL to get the real destination URL."""
    if not href:
        return href
    parsed = urlparse(href)
    # Bing wraps real URLs in /ck/a? redirects with a 'u' parameter
    if 'bing.com' in parsed.netloc and '/ck/' in parsed.path:
        params = parse_qs(parsed.query)
        u_param = params.get('u', [None])[0]
        if u_param:
            # Bing base64-encodes the URL with a prefix like 'a1'
            import base64
            try:
                # Remove the 'a1' prefix that Bing adds
                if u_param.startswith('a1'):
                    decoded = base64.urlsafe_b64decode(u_param[2:] + '==').decode('utf-8', errors='ignore')
                    if decoded.startswith('http'):
                        return decoded
            except Exception:
                pass
    return href


def collect_fallback_results(
    driver: webdriver.Chrome,
    query: str,
    max_results: int,
    args: argparse.Namespace,
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    search_url = "https://www.bing.com/search?q=" + quote_plus(query)
    
    LOGGER.info("Searching Bing (Fallback): %s", query)

    if not load_page(driver, search_url, args.page_load_timeout):
        return []

    try:
        WebDriverWait(driver, WAIT_TIMEOUT_SECONDS).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "li.b_algo h2 a"))
        )
    except TimeoutException:
        LOGGER.warning("No links found on Bing search page.")
        return []

    try:
        anchor_data = driver.execute_script(
            """
            return Array.from(document.querySelectorAll("li.b_algo h2 a")).map(a => ({
                href: a.href || "",
                title: a.innerText || a.textContent || ""
            }));
            """
        )
    except Exception as e:
        LOGGER.warning("Failed to extract links via JS from Bing: %s", e)
        anchor_data = []

    for item in anchor_data:
        href = item.get("href", "")
        title = item.get("title", "").strip()

        if not href:
            continue

        # Decode Bing tracking URLs to get real destination
        href = _decode_bing_url(href)

        normalized = normalize_site_url(href)
        if normalized in seen or not normalized:
            continue
        
        # Skip search/social/directory domains
        domain = urlparse(href).netloc.lower()
        if any(skip in domain for skip in SKIP_RESULT_DOMAINS):
            continue
            
        seen.add(normalized)
        title = title.splitlines()[0] if title else ""
        
        if not title:
            continue
            
        results.append((title, href))
        
        if len(results) >= max_results:
            break

    LOGGER.info("Collected %d new result(s) from Bing.", len(results))
    return results


def collect_google_results(
    driver: webdriver.Chrome,
    query: str,
    max_results: int,
    args: argparse.Namespace,
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for start in range(0, max_results, 10):
        search_url = (
            "https://www.google.com/search?"
            f"q={quote_plus(query)}&start={start}"
        )

        LOGGER.info(
            "Searching Google: %s | results %d-%d",
            query,
            start + 1,
            start + 10,
        )

        if not load_page(driver, search_url, args.page_load_timeout):
            break

        if not handle_manual_captcha(driver, args):
            break

        try:
            WebDriverWait(driver, WAIT_TIMEOUT_SECONDS).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a"))
            )
        except TimeoutException:
            LOGGER.warning("No links found on search page.")
            break

        try:
            anchor_data = driver.execute_script(
                """
                return Array.from(document.querySelectorAll("a")).map(a => ({
                    href: a.href || "",
                    title: a.innerText || a.textContent || ""
                }));
                """
            )
        except Exception as e:
            LOGGER.warning("Failed to extract links via JS from Google: %s", e)
            anchor_data = []

        before_count = len(results)

        for item in anchor_data:
            href = normalize_google_url(item.get("href", ""))

            if not href:
                continue

            normalized = normalize_site_url(href)

            if normalized in seen:
                continue

            title_raw = item.get("title", "").strip()
            title = title_raw.splitlines()[0] if title_raw else ""

            if not title:
                continue

            seen.add(normalized)
            results.append((title, href))

            if len(results) >= max_results:
                break

        LOGGER.info(
            "Collected %d new result(s).",
            len(results) - before_count,
        )

        if len(results) >= max_results:
            break

        polite_sleep(args)

    return results


# ---------------------------------------------------------------------------
# Contact extraction
# ---------------------------------------------------------------------------

def clean_email(email: str) -> str:
    return email.strip().strip(".,;:)(").lower()


def normalize_phone(raw: str) -> str | None:
    value = raw.strip().strip(".,;:|")
    digits = re.sub(r"\D", "", value)

    # Avoid obviously useless/invalid lengths.
    if len(digits) < 8 or len(digits) > 15:
        return None

    for pattern in INVALID_PHONE_PATTERNS:
        if pattern.fullmatch(digits):
            return None

    if value.startswith("+"):
        return "+" + digits

    return digits


def extract_contacts_from_current_page(
    driver: webdriver.Chrome,
) -> tuple[set[str], set[str]]:
    html = driver.page_source or ""
    text = visible_text(driver)

    emails = {
        clean_email(match)
        for match in EMAIL_RE.findall(html + "\n" + text)
    }

    # Ignore image/file-like false positives.
    emails = {
        email
        for email in emails
        if not email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
    }

    phones: set[str] = set()

    # Prefer explicit tel: links because they are generally more reliable.
    try:
        tel_links = driver.execute_script(
            """
            return Array.from(
                document.querySelectorAll('a[href^="tel:"]')
            ).map(a => a.href || "");
            """
        )
    except WebDriverException:
        tel_links = []

    for href in tel_links:
        phone = normalize_phone(href.replace("tel:", ""))
        if phone:
            phones.add(phone)

    # Also inspect visible text.
    for match in PHONE_RE.findall(text):
        phone = normalize_phone(match)
        if phone:
            phones.add(phone)
            
    # Extract CIN, GSTIN, LinkedIn
    cins = CIN_RE.findall(text)
    gstins = GSTIN_RE.findall(text)
    linkedins = LINKEDIN_RE.findall(html)
    
    return emails, phones, cins, gstins, linkedins


# ---------------------------------------------------------------------------
# Contact-page discovery
# ---------------------------------------------------------------------------

def likely_contact_links(
    driver: webdriver.Chrome,
    base_url: str,
    limit: int,
) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    try:
        anchor_data = driver.execute_script(
            """
            return Array.from(document.querySelectorAll("a[href]")).map(a => ({
                href: a.href || "",
                label: (a.innerText || a.textContent || "").trim().toLowerCase()
            }));
            """
        )
    except WebDriverException:
        return links

    for anchor in anchor_data:
        href = anchor.get("href") or ""
        label = anchor.get("label") or ""

        normalized = urljoin(base_url, href).split("#", 1)[0]

        if not normalized.startswith(("http://", "https://")):
            continue

        if normalized in seen:
            continue

        if not same_site(base_url, normalized):
            continue

        lower_url = normalized.lower()

        if any(
            keyword in lower_url or keyword in label
            for keyword in CONTACT_LINK_KEYWORDS
        ):
            seen.add(normalized)
            links.append(normalized)

        if len(links) >= limit:
            break

    return links


# ---------------------------------------------------------------------------
# Website crawling
# ---------------------------------------------------------------------------

def crawl_provider_site(
    driver: webdriver.Chrome,
    query: str,
    title: str,
    url: str,
    args: argparse.Namespace,
) -> LeadResult:
    lead = LeadResult(
        source_query=query,
        result_title=title,
        result_url=url,
        last_page_checked=url,
    )

    if not can_fetch_url(url):
        LOGGER.info("robots.txt does not allow fetching %s; skipping.", url)
        return lead

    pages_to_visit = [url]
    visited: set[str] = set()

    while pages_to_visit and len(visited) < args.max_pages_per_site:
        page_url = pages_to_visit.pop(0)

        if page_url in visited:
            continue

        if not can_fetch_url(page_url):
            LOGGER.info("robots.txt disallows %s; skipping.", page_url)
            visited.add(page_url)
            continue

        LOGGER.info("Visiting: %s", page_url)
        visited.add(page_url)

        if not load_page(driver, page_url, args.page_load_timeout):
            continue

        if not handle_manual_captcha(driver, args):
            break

        lead.last_page_checked = driver.current_url

        emails, phones, cins, gstins, linkedins = extract_contacts_from_current_page(driver)

        lead.emails.update(emails)
        lead.phones.update(phones)
        if cins and not lead.cin:
            lead.cin = cins[0].upper()
        if gstins and not lead.gstin:
            lead.gstin = gstins[0].upper()
        if linkedins and not lead.linkedin_url:
            lead.linkedin_url = linkedins[0]

        remaining_slots = (
            args.max_pages_per_site
            - len(visited)
            - len(pages_to_visit)
        )

        if remaining_slots > 0:
            for link in likely_contact_links(
                driver,
                driver.current_url,
                remaining_slots,
            ):
                if link not in visited and link not in pages_to_visit:
                    pages_to_visit.append(link)

        polite_sleep(args)

    return lead


# ---------------------------------------------------------------------------
# DJANGO INTEGRATION
# ---------------------------------------------------------------------------

from typing import Iterable
from pathlib import Path
import csv

# Assuming this function runs inside a Django environment (e.g. Celery task)
def run_generation_job(job_id: int):
    from lead_generation.models import GenerationJob
    from leads.models import Company, Lead, Contact
    from lead_generation.services.extraction_service import save_extraction_result
    import logging

    try:
        job = GenerationJob.objects.get(id=job_id)
    except GenerationJob.DoesNotExist:
        LOGGER.error(f"GenerationJob {job_id} not found.")
        return

    job.status = 'RUNNING'
    job.save()

    LOGGER.info(f"Starting Generation Job #{job.id} for {job.city}, {job.industry}")

    # Build queries based on job keywords
    queries = []
    keywords = [k.strip() for k in job.keywords.split(',') if k.strip()]
    if not keywords:
        keywords = [job.industry]
        
    for kw in keywords:
        if kw and job.city and job.district and job.state:
            queries.append(f"{kw} in {job.city}, {job.district}, {job.state}")

    if not queries:
        job.status = 'FAILED'
        job.error_message = "Generation configuration is incomplete. Missing required location or keywords."
        job.save()
        LOGGER.error("Job %d failed: Missing required configuration to build queries.", job.id)
        return

    seen_websites: set[str] = set()

    # For now we create a dummy args object to satisfy the existing functions
    class DummyArgs:
        max_results = 50 # We can change this or remove the hard limit later
        max_pages_per_site = MAX_PAGES_PER_SITE
        delay_min = DELAY_MIN_SECONDS
        delay_max = DELAY_MAX_SECONDS
        page_load_timeout = PAGE_LOAD_TIMEOUT_SECONDS
        chrome_profile = CHROME_PROFILE
        chromedriver = CHROMEDRIVER_PATH
        headless = job.is_headless
        query = queries

    args = DummyArgs()
    driver = make_driver(args)

    try:
        for query in queries:
            job.current_query = query
            job.save()
            
            job.refresh_from_db()
            while job.status == 'PAUSED':
                import time
                time.sleep(5)
                job.refresh_from_db()
            
            if job.status == 'STOPPED':
                driver.quit()
                return
                
            # The collect_google_results function limits to max_results, but we can make it huge if run_mode is infinite.
            # For simplicity, let's keep it to max_results=100 for now or rely on the job's run mode later.
            google_results = collect_google_results(
                driver=driver,
                query=query,
                max_results=args.max_results,
                args=args,
            )
            
            if not google_results:
                LOGGER.info("Google returned 0 results or was blocked by CAPTCHA. Trying Fallback source (Bing).")
                google_results = collect_fallback_results(
                    driver=driver,
                    query=query,
                    max_results=args.max_results,
                    args=args,
                )
                
            if not google_results:
                LOGGER.warning("Both sources failed or returned 0 results for query: %s", query)
                job.error_message = (job.error_message or "") + f"\nNo results for: {query}"
                job.save()

            for title, url in google_results:
                job.refresh_from_db()
                while job.status == 'PAUSED':
                    LOGGER.info(f"Job {job.id} is PAUSED. Waiting...")
                    import time
                    time.sleep(5)
                    job.refresh_from_db()
                
                if job.status == 'STOPPED':
                    LOGGER.info(f"Job {job.id} is STOPPED. Halting extraction.")
                    driver.quit()
                    return

                normalized = normalize_site_url(url)
                if normalized in seen_websites:
                    continue

                seen_websites.add(normalized)
                job.current_website = url
                job.websites_found += 1
                job.save()

                lead_data = crawl_provider_site(
                    driver=driver,
                    query=query,
                    title=title,
                    url=url,
                    args=args,
                )
                
                # Delegate saving to the extraction service
                result_dict = {
                    'company_name': lead_data.result_title,
                    'website': lead_data.website,
                    'primary_email': lead_data.primary_email,
                    'primary_phone': lead_data.primary_phone,
                    'emails': list(lead_data.emails),
                    'phones': list(lead_data.phones),
                    'cin': lead_data.cin,
                    'gstin': lead_data.gstin,
                    'linkedin_url': lead_data.linkedin_url,
                    'source_query': lead_data.source_query,
                    'result_url': lead_data.result_url
                }
                
                save_extraction_result(result_dict, job.id)
    except Exception as e:
        import traceback
        job.status = 'FAILED'
        job.error_message = str(e) + "\n" + traceback.format_exc()
        job.save()
        LOGGER.error(f"Job {job.id} failed: {e}\n{traceback.format_exc()}")
    finally:
        driver.quit()
        job.refresh_from_db()
        if job.status not in ['PAUSED', 'STOPPED', 'FAILED']:
            job.status = 'COMPLETED'
            job.save()

    LOGGER.info(f"Finished Generation Job #{job.id}")
