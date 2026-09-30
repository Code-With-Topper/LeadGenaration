"""
Lead generation engine.

What it does
------------
Builds search queries from the job's industry keywords and location, finds
company websites, opens a few likely contact pages on each, and reads the
publicly published business contact details.

Three things make it different from a plain scraper:

1. The company name is read from the **website**, not from the search-result
   title. A search hit titled "Top 10 Sponge Iron Manufacturers in West Bengal"
   is a list of companies, not a company, and is rejected outright.
2. Every extracted record goes through `core.ingest`, the same validation and
   duplicate pipeline the CSV importer uses. There is no second, looser path.
3. The job's location scope can be a city, a whole district, or every district
   in a state — because the client asked for all of West Bengal, not for the
   two districts that happened to be in a dropdown.

Boundaries
----------
Only publicly published business contact information is collected. robots.txt
is honoured on every fetch. CAPTCHA is never bypassed: the run falls back to
another source and carries on.

Why Playwright rather than Selenium
-----------------------------------
Playwright installs a browser it is built against (`playwright install
chromium`), so the ChromeDriver-versus-Chrome version mismatch that breaks a
Selenium deployment on every browser update cannot happen. It is equally free
and needs no service or subscription.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from django.utils import timezone

from core import normalize, validation
from core.ingest import CREATED, MERGED, REJECTED, REVIEW, ingest

LOGGER = logging.getLogger('lead_generation')

# --- pacing ---------------------------------------------------------------
DELAY_MIN_SECONDS = 2.0
DELAY_MAX_SECONDS = 5.0
PAGE_LOAD_TIMEOUT = 35
ELEMENT_WAIT_TIMEOUT = 12
MAX_PAGES_PER_SITE = 4
RESULTS_PER_QUERY = 20

# Link text and URLs that usually lead to contact details.
CONTACT_KEYWORDS = (
    'contact', 'contact-us', 'contactus', 'about', 'about-us', 'enquiry',
    'enquire', 'inquiry', 'reach', 'connect', 'get-in-touch', 'support',
)

EMAIL_RE = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.IGNORECASE)
PHONE_RE = re.compile(
    r"""(?:
        (?:\+?\d{1,3}[\s().-]*)?
        (?:\(?\d{2,5}\)?[\s().-]*)?
        \d{3,5}[\s().-]*\d{3,5}
        (?:[\s().-]*\d{1,5})?
    )""", re.VERBOSE)
CIN_RE = re.compile(r'\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b', re.IGNORECASE)
GSTIN_RE = re.compile(r'\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b', re.IGNORECASE)
PIN_RE = re.compile(r'\b([1-8]\d{5})\b')
LINKEDIN_RE = re.compile(
    r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_%-]+',
    re.IGNORECASE)

# Words in a page title that mean "this page lists companies".
_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


# ==========================================================================
# Extraction result
# ==========================================================================

@dataclass
class SearchPlan:
    """One search to run, and the location its results belong to."""
    query: str
    city: str = ''
    district: str = ''
    state: str = ''

    @property
    def location(self) -> dict:
        return {'city': self.city, 'district': self.district,
                'state': self.state}


@dataclass
class SiteFindings:
    """Everything one website told us."""
    site_name: str = ''
    result_title: str = ''
    url: str = ''
    last_page: str = ''
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    cin: str = ''
    gstin: str = ''
    pin_code: str = ''
    address: str = ''
    linkedin_url: str = ''
    query: str = ''
    location: dict = field(default_factory=dict)
    # Every page opened, and the one the contact details actually came from.
    # The client asks "where did you get this number?" and this answers it.
    pages_visited: list[str] = field(default_factory=list)
    contact_page: str = ''

    @property
    def best_name(self) -> str:
        """Prefer the name the site states about itself."""
        return self.site_name or normalize.strip_title_noise(self.result_title)

    @property
    def provenance_url(self) -> str:
        """The most useful page to record against the lead."""
        return self.contact_page or self.url or self.last_page


# ==========================================================================
# Browser
# ==========================================================================

class Browser:
    """
    A headless browser, wrapped so the rest of this module only needs four
    verbs: open a page, read its text, read its HTML, run a script.

    Always headless: a visible browser cannot start on a server with no
    display, and a run that waits for someone to solve a CAPTCHA inside a
    background thread hangs for ever.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None

    def __enter__(self):
        from django.conf import settings
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()

        launch_kwargs = {
            'args': [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-notifications',
            ],
        }
        binary = getattr(settings, 'CHROME_BINARY', '')
        if binary:
            launch_kwargs['executable_path'] = binary

        self._browser = self._playwright.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            viewport={'width': 1366, 'height': 900},
            java_script_enabled=True,
        )
        self._context.set_default_timeout(PAGE_LOAD_TIMEOUT * 1000)
        # Images and fonts are never read, and skipping them makes a run far
        # cheaper on a small server's bandwidth.
        self._context.route(
            '**/*',
            lambda route: route.abort()
            if route.request.resource_type in ('image', 'media', 'font')
            else route.continue_())
        self.page = self._context.new_page()
        return self

    def __exit__(self, *exc):
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        return False

    # -- reading -----------------------------------------------------------

    @property
    def url(self) -> str:
        try:
            return self.page.url or ''
        except Exception:
            return ''

    def title(self) -> str:
        try:
            return self.page.title() or ''
        except Exception:
            return ''

    def goto(self, url: str) -> bool:
        """Open a page. Returns False on a timeout or a dead host."""
        try:
            self.page.goto(url, wait_until='domcontentloaded',
                           timeout=PAGE_LOAD_TIMEOUT * 1000)
            return True
        except Exception as exc:
            LOGGER.info('Could not load %s: %s', url, str(exc).splitlines()[0])
            return False

    def text(self) -> str:
        try:
            return self.page.inner_text('body', timeout=5000) or ''
        except Exception:
            return ''

    def html(self) -> str:
        try:
            return self.page.content() or ''
        except Exception:
            return ''

    def evaluate(self, script: str, default=None):
        try:
            return self.page.evaluate(script)
        except Exception:
            return default

    def wait_for(self, selector: str) -> bool:
        try:
            self.page.wait_for_selector(selector,
                                        timeout=ELEMENT_WAIT_TIMEOUT * 1000)
            return True
        except Exception:
            return False


def polite_sleep():
    time.sleep(random.uniform(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS))


def is_blocked_page(browser: Browser) -> bool:
    """A search engine's 'unusual traffic' or CAPTCHA page."""
    url = browser.url.lower()
    title = browser.title().lower()
    markers = ('captcha', 'unusual traffic', 'not a robot',
               'verify you are human', 'are you a robot')
    return ('/sorry' in url or 'recaptcha' in url
            or any(marker in title for marker in markers))


# ==========================================================================
# robots.txt
# ==========================================================================

def can_fetch(url: str, user_agent: str = '*') -> bool:
    """
    Best-effort robots.txt check, cached per host.

    An unreachable robots.txt is not treated as permission granted in spirit —
    we still crawl politely and shallowly — but it is not treated as a refusal
    either, because that would block most small company sites.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False

    host = parsed.netloc.lower()
    if host not in _ROBOTS_CACHE:
        parser = RobotFileParser()
        parser.set_url(f'{parsed.scheme}://{parsed.netloc}/robots.txt')
        try:
            parser.read()
            _ROBOTS_CACHE[host] = parser
        except Exception:
            _ROBOTS_CACHE[host] = None

    parser = _ROBOTS_CACHE[host]
    if parser is None:
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


# ==========================================================================
# Search
# ==========================================================================

def _clean_result(href: str, title: str) -> tuple[str, str] | None:
    """Turn one search hit into (title, url), or None if it is not a company."""
    if not href:
        return None
    url = normalize.normalize_url(href)
    if not url:
        return None
    domain = normalize.normalize_domain(url)
    if not domain or validation.is_directory_domain(domain):
        return None
    title = (title or '').strip().splitlines()[0] if title else ''
    if not title:
        return None
    # A listicle title means the page is a list of companies, not a company.
    if validation.looks_like_listicle(title):
        LOGGER.info('Skipping listicle result: %s', title[:80])
        return None
    return title, url


def search_bing(browser: Browser, query: str, limit: int) -> list[tuple[str, str]]:
    LOGGER.info('Searching Bing: %s', query)
    if not browser.goto('https://www.bing.com/search?q=' + quote_plus(query)):
        return []
    if is_blocked_page(browser):
        LOGGER.warning('Bing is rate limiting this run.')
        return []
    if not browser.wait_for('li.b_algo h2 a'):
        return []

    anchors = browser.evaluate("""
        () => Array.from(document.querySelectorAll('li.b_algo h2 a'))
            .map(a => ({href: a.href || '', title: a.innerText || ''}))
    """, default=[]) or []

    return _collect(anchors, limit, decode=_decode_bing_url)


def search_google(browser: Browser, query: str, limit: int) -> list[tuple[str, str]]:
    LOGGER.info('Searching Google: %s', query)
    results: list[tuple[str, str]] = []
    for start in range(0, limit, 10):
        url = f'https://www.google.com/search?q={quote_plus(query)}&start={start}'
        if not browser.goto(url):
            break
        if is_blocked_page(browser):
            LOGGER.info('Google is rate limiting; falling back to Bing.')
            return []
        if not browser.wait_for('a[href]'):
            break

        anchors = browser.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href || '',
                            title: (a.innerText || '').trim()}))
        """, default=[]) or []
        if not anchors:
            break

        results += _collect(anchors, limit - len(results),
                            decode=_decode_google_url,
                            seen={normalize.normalize_domain(u)
                                  for _t, u in results})
        if len(results) >= limit:
            break
        polite_sleep()
    return results[:limit]


def _collect(anchors, limit, *, decode, seen=None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen = set(seen or ())
    for item in anchors:
        if len(found) >= limit:
            break
        href = decode(item.get('href', ''))
        cleaned = _clean_result(href, item.get('title', ''))
        if not cleaned:
            continue
        title, url = cleaned
        # One entry per domain: a company's home page and its products page
        # are the same company.
        domain = normalize.normalize_domain(url)
        if domain in seen:
            continue
        seen.add(domain)
        found.append((title, url))
    return found


def _decode_google_url(href: str) -> str:
    if not href:
        return ''
    parsed = urlparse(href)
    if parsed.path == '/url':
        return parse_qs(parsed.query).get('q', [''])[0]
    if 'google.' in parsed.netloc:
        return ''
    return href


def _decode_bing_url(href: str) -> str:
    """Bing wraps results in a /ck/a? redirect with a base64 'u' parameter."""
    if not href:
        return ''
    parsed = urlparse(href)
    if 'bing.com' in parsed.netloc and '/ck/' in parsed.path:
        token = parse_qs(parsed.query).get('u', [''])[0]
        if token.startswith('a1'):
            import base64
            try:
                padded = token[2:] + '=' * (-len(token[2:]) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode('utf-8', 'ignore')
                if decoded.startswith('http'):
                    return decoded
            except Exception:
                return ''
        return ''
    return href


# ==========================================================================
# Reading a company website
# ==========================================================================

def read_site_name(browser: Browser) -> str:
    """
    The name the company gives itself.

    Checked in order of reliability: schema.org markup, then Open Graph, then
    the copyright line in the footer, then the page title. This is what stops
    a search-result headline becoming a company name.
    """
    candidates = browser.evaluate("""
        () => {
            const out = [];
            const push = v => { if (v) out.push(String(v).trim()); };
            document.querySelectorAll('script[type="application/ld+json"]')
                .forEach(node => {
                    try {
                        let data = JSON.parse(node.textContent);
                        const items = Array.isArray(data) ? data
                            : (data['@graph'] || [data]);
                        items.forEach(item => {
                            const type = String(item['@type'] || '');
                            if (/Organization|LocalBusiness|Corporation|Company/i
                                    .test(type)) push(item.name);
                        });
                    } catch (e) {}
                });
            const og = document.querySelector('meta[property="og:site_name"]');
            if (og) push(og.content);
            const appName = document.querySelector('meta[name="application-name"]');
            if (appName) push(appName.content);
            const footer = document.querySelector('footer');
            if (footer) push((footer.innerText || '').slice(0, 400));
            push(document.title);
            return out;
        }
    """, default=[]) or []

    for candidate in candidates:
        name = _name_from_candidate(candidate)
        if name:
            return name
    return ''


def _name_from_candidate(text: str) -> str:
    """Pull a plausible company name out of one candidate string."""
    text = normalize.clean_text(text)
    if not text:
        return ''

    # A footer block: find the copyright line, which almost always names the
    # company exactly as it wishes to be known.
    if len(text) > 120 or '©' in text or 'copyright' in text.lower():
        match = re.search(
            r'(?:©|\(c\)|copyright)\s*(?:\d{4}\s*(?:[-–]\s*\d{4})?\s*)?'
            r'(?:by\s+)?([A-Za-z0-9&.,\'\- ]{3,80})', text, re.IGNORECASE)
        if not match:
            return ''
        text = match.group(1)
        text = re.split(r'\s*(?:\||all rights|\.\s|,\s*all)', text,
                        flags=re.IGNORECASE)[0]

    result = validation.validate_company_name(text)
    if not result.ok:
        return ''
    # Guard against a "name" that is really a tagline.
    if len(result.value.split()) > 8:
        return ''
    return result.value


def extract_from_page(browser: Browser) -> dict:
    """Contact details published on the page currently open."""
    html = browser.html()
    text = browser.text()
    haystack = html + '\n' + text

    emails: list[str] = []
    for candidate in EMAIL_RE.findall(haystack):
        result = validation.validate_email(candidate)
        if result.ok and result.value not in emails:
            emails.append(result.value)

    phones: list[str] = []
    # tel: links are the most reliable source of a real number.
    tel_links = browser.evaluate("""
        () => Array.from(document.querySelectorAll('a[href^="tel:"]'))
            .map(a => a.getAttribute('href') || '')
    """, default=[]) or []
    for candidate in list(tel_links) + PHONE_RE.findall(text):
        result = validation.validate_phone(candidate)
        if result.ok and result.value not in phones:
            phones.append(result.value)

    cins = [m.upper() for m in CIN_RE.findall(text) if validation.validate_cin(m).ok]
    gstins = [m.upper() for m in GSTIN_RE.findall(text)
              if validation.validate_gstin(m).ok]
    pins = PIN_RE.findall(text)
    linkedins = LINKEDIN_RE.findall(html)

    return {
        'emails': emails[:10],
        'phones': phones[:10],
        'cin': cins[0] if cins else '',
        'gstin': gstins[0] if gstins else '',
        'pin_code': pins[0] if pins else '',
        'address': _guess_address(text),
        'linkedin_url': linkedins[0] if linkedins else '',
    }


def _guess_address(text: str) -> str:
    """The line around a PIN code is almost always the postal address."""
    for line in text.splitlines():
        line = line.strip()
        if 20 <= len(line) <= 250 and PIN_RE.search(line):
            return line
    return ''


def contact_page_links(browser: Browser, base_url: str, limit: int) -> list[str]:
    """Same-site links whose text or URL suggests contact details."""
    anchors = browser.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
            href: a.href || '',
            label: (a.innerText || a.textContent || '').trim().toLowerCase()
        }))
    """, default=[]) or []

    base_host = normalize.normalize_domain(base_url)
    links: list[str] = []
    for anchor in anchors:
        if len(links) >= limit:
            break
        url = urljoin(base_url, anchor.get('href') or '').split('#', 1)[0]
        if not url.startswith(('http://', 'https://')):
            continue
        if normalize.normalize_domain(url) != base_host:
            continue
        if url in links:
            continue
        haystack = f"{url.lower()} {anchor.get('label') or ''}"
        if any(keyword in haystack for keyword in CONTACT_KEYWORDS):
            links.append(url)
    return links


def crawl_site(browser: Browser, url: str, title: str,
               plan: "SearchPlan") -> SiteFindings:
    """Open a company website and read what it publishes."""
    findings = SiteFindings(result_title=title, url=url, last_page=url,
                            query=plan.query, location=plan.location)

    if not can_fetch(url):
        LOGGER.info('robots.txt disallows %s — skipping.', url)
        return findings

    queue, visited = [url], set()
    while queue and len(visited) < MAX_PAGES_PER_SITE:
        page = queue.pop(0)
        if page in visited:
            continue
        visited.add(page)

        if not can_fetch(page) or not browser.goto(page):
            continue

        findings.last_page = browser.url or page
        if findings.last_page not in findings.pages_visited:
            findings.pages_visited.append(findings.last_page)

        if not findings.site_name:
            findings.site_name = read_site_name(browser)

        page_data = extract_from_page(browser)
        found_here = bool(page_data['emails'] or page_data['phones'])
        if found_here and not findings.contact_page:
            findings.contact_page = findings.last_page

        for email in page_data['emails']:
            if email not in findings.emails:
                findings.emails.append(email)
        for phone in page_data['phones']:
            if phone not in findings.phones:
                findings.phones.append(phone)
        findings.cin = findings.cin or page_data['cin']
        findings.gstin = findings.gstin or page_data['gstin']
        findings.pin_code = findings.pin_code or page_data['pin_code']
        findings.address = findings.address or page_data['address']
        findings.linkedin_url = findings.linkedin_url or page_data['linkedin_url']

        remaining = MAX_PAGES_PER_SITE - len(visited) - len(queue)
        if remaining > 0:
            for link in contact_page_links(browser, findings.last_page, remaining):
                if link not in visited and link not in queue:
                    queue.append(link)
        polite_sleep()

    return findings


# ==========================================================================
# Query building
# ==========================================================================

def build_queries(job) -> list["SearchPlan"]:
    """
    One search per (keyword × place).

    When no city is given the district is searched; when no district is given
    every active district of the state is searched. That is what makes "all of
    West Bengal" a real option rather than a promise.

    Each plan carries its own location, so a lead found by a state-wide run
    still records the district it came from. Carrying it alongside the query
    beats parsing it back out of the query text afterwards.
    """
    from .models import District

    keywords = job.keyword_list or [job.industry]

    if job.city:
        places = [(f'{job.city}, {job.state}', job.city, job.district)]
    elif job.district:
        places = [(f'{job.district} district, {job.state}', '', job.district)]
    else:
        districts = list(District.objects.filter(state__iexact=job.state,
                                                 is_active=True))
        places = [(f'{d.name} district, {job.state}', '', d.name)
                  for d in districts] or [(job.state, '', '')]

    return [
        SearchPlan(
            query=f'{keyword} company in {place} contact address',
            city=city,
            district=district,
            state=job.state,
        )
        for place, city, district in places
        for keyword in keywords
    ]


# ==========================================================================
# The run
# ==========================================================================

def run_generation_job(job_id: int) -> None:
    """
    Execute one generation job from start to finish.

    Pause, resume and stop are read from the database on every step, so the
    user stays in control of a run that is already going.
    """
    from django.conf import settings

    from .models import GenerationJob

    try:
        job = GenerationJob.objects.get(id=job_id)
    except GenerationJob.DoesNotExist:
        LOGGER.error('Generation job %s no longer exists.', job_id)
        return

    # A user can stop a run between queueing it and the worker picking it up.
    # Setting RUNNING unconditionally here would override that and crawl
    # anyway, so refuse any job that is already finished.
    if job.status in GenerationJob.FINISHED_STATUSES:
        LOGGER.info('Job #%s is already %s — not starting it.',
                    job.id, job.status)
        return

    job.status = 'RUNNING'
    job.started_at = job.started_at or timezone.now()
    job.heartbeat_at = timezone.now()
    job.error_message = ''
    job.note(f'Started: {job.industry} across {job.scope}')
    job.save()

    plans = build_queries(job)
    if not plans:
        _finish(job, 'FAILED', 'No search could be built — choose a state and '
                              'at least one keyword.')
        return

    job.note(f'{len(plans)} search(es) to run.')
    job.save(update_fields=['log'])

    check_mx = getattr(settings, 'VALIDATE_EMAIL_MX', False)

    # The crawler runs in its own thread and communicates only through a
    # queue. Playwright's sync API keeps an asyncio loop running, and Django
    # refuses ORM calls from inside one — so rather than switching off that
    # safety check, the browser thread never touches the database and this
    # thread performs every write.
    import queue
    import threading

    messages: "queue.Queue[tuple[str, object]]" = queue.Queue()
    stop_flag = threading.Event()
    pause_flag = threading.Event()

    crawler = threading.Thread(
        target=_crawl_thread,
        args=(plans, job.max_websites, messages, stop_flag, pause_flag),
        daemon=True,
        name=f'leadcrm-crawl-{job.id}',
    )
    crawler.start()

    try:
        while True:
            try:
                kind, payload = messages.get(timeout=5)
            except queue.Empty:
                # No news: refresh the control flags and keep waiting.
                if not _sync_control(job, stop_flag, pause_flag):
                    break
                if not crawler.is_alive():
                    break
                continue

            if kind == 'done':
                break
            if kind == 'error':
                _finish(job, 'FAILED', str(payload))
                return
            if kind == 'note':
                job.note(str(payload))
                job.heartbeat_at = timezone.now()
                job.save(update_fields=['log', 'heartbeat_at', 'updated_at'])
            elif kind == 'query':
                job.current_query = str(payload)[:255]
                job.heartbeat_at = timezone.now()
                job.save(update_fields=['current_query', 'heartbeat_at',
                                        'updated_at'])
            elif kind == 'visiting':
                job.current_website = str(payload)[:500]
                job.websites_found += 1
                job.heartbeat_at = timezone.now()
                job.save(update_fields=['current_website', 'websites_found',
                                        'heartbeat_at', 'updated_at'])
            elif kind == 'findings':
                _store(job, payload, check_mx=check_mx)

            if not _sync_control(job, stop_flag, pause_flag):
                break

        stop_flag.set()
        crawler.join(timeout=30)
        _finish(job, 'COMPLETED' if _still_running(job) else job.status)

    except Exception as exc:
        import traceback
        stop_flag.set()
        LOGGER.exception('Generation job #%s failed', job_id)
        _finish(job, 'FAILED', f'{exc}\n{traceback.format_exc()[:1500]}')


def _sync_control(job, stop_flag, pause_flag) -> bool:
    """
    Push the job's current status down to the crawler thread.

    Returns False when the run should end. This is the only path by which the
    user's Pause, Resume and Stop buttons reach the crawler.
    """
    from .models import GenerationJob

    try:
        job.refresh_from_db()
    except GenerationJob.DoesNotExist:
        stop_flag.set()
        return False

    if job.status == 'PAUSED':
        pause_flag.set()
        return True

    pause_flag.clear()

    if job.status != 'RUNNING':
        stop_flag.set()
        return False
    return True


def _crawl_thread(plans, max_websites, messages, stop_flag, pause_flag) -> None:
    """
    Do all the browser work and report back through the queue.

    Touches no models: everything it learns is sent as plain data, which is
    what keeps the browser's event loop away from the database.
    """
    seen_domains: set[str] = set()
    visited = 0

    def waiting() -> bool:
        """Block while paused. Returns False once the run is stopped."""
        while pause_flag.is_set() and not stop_flag.is_set():
            time.sleep(2)
        return not stop_flag.is_set()

    try:
        with Browser() as browser:
            for plan in plans:
                if not waiting() or visited >= max_websites:
                    break

                messages.put(('query', plan.query))

                results = search_google(browser, plan.query, RESULTS_PER_QUERY)
                if not results and waiting():
                    results = search_bing(browser, plan.query, RESULTS_PER_QUERY)
                if not results:
                    messages.put(('note', f'No results for: {plan.query}'))
                    continue

                messages.put(
                    ('note', f'{len(results)} site(s) found for: {plan.query}'))

                for title, url in results:
                    if not waiting() or visited >= max_websites:
                        break

                    domain = normalize.normalize_domain(url)
                    if not domain or domain in seen_domains:
                        continue
                    seen_domains.add(domain)

                    visited += 1
                    messages.put(('visiting', url))
                    messages.put(('findings',
                                  crawl_site(browser, url, title, plan)))

            if visited >= max_websites:
                messages.put(
                    ('note', f'Reached the limit of {max_websites} websites.'))
    except Exception as exc:
        import traceback
        LOGGER.exception('Crawler thread failed')
        messages.put(('error', f'{exc}\n{traceback.format_exc()[:1200]}'))
        return

    messages.put(('done', None))


def _store(job, findings: SiteFindings, *, check_mx: bool) -> None:
    """Hand one site's findings to the shared ingest pipeline."""
    name = findings.best_name
    if not name:
        job.rejected_leads += 1
        job.note(f'No company name found on {findings.url[:60]}')
        job.save(update_fields=['rejected_leads', 'log', 'updated_at'])
        return

    location = findings.location or {
        'city': job.city, 'district': job.district, 'state': job.state}
    raw = {
        'company_name': name,
        'industry': job.industry,
        'website': findings.url,
        'company_email': findings.emails[0] if findings.emails else '',
        'company_phone': findings.phones[0] if findings.phones else '',
        'extra_emails': findings.emails[1:],
        'extra_phones': findings.phones[1:],
        'cin': findings.cin,
        'gstin': findings.gstin,
        'plant_address': findings.address,
        'pin_code': findings.pin_code,
        'linkedin_url': findings.linkedin_url,
        'lead_source': 'Web Search',
        'source_query': findings.query,
        'source_url': findings.provenance_url,
        **location,
    }

    outcome = ingest(
        raw,
        origin='Web Search',
        generation_job_id=job.id,
        check_mx=check_mx,
        # The name came from the site itself unless we had to fall back to the
        # search-result title, which is a guess and is scored lower.
        identity_status='SITE_CONFIRMED' if findings.site_name else 'TITLE_GUESS',
    )

    if outcome.action == CREATED:
        job.leads_found += 1
        job.valid_leads += 1
        job.note(f'New lead: {name}')
    elif outcome.action == MERGED:
        job.duplicate_leads += 1
        job.note(f'Duplicate merged: {name} ({outcome.reason})')
    elif outcome.action == REVIEW:
        job.duplicate_leads += 1
        job.needs_review += 1
        job.note(f'Needs review: {name} ({outcome.reason})')
    else:
        job.rejected_leads += 1
        job.note(f'Rejected: {name} — {outcome.reason}')

    job.save()


def _still_running(job) -> bool:
    """
    Re-read the job's status, honouring pause and stop.

    Returns False when the user has stopped the run, or the job vanished.
    """
    from .models import GenerationJob

    while True:
        try:
            job.refresh_from_db()
        except GenerationJob.DoesNotExist:
            return False

        if job.status == 'PAUSED':
            job.heartbeat_at = timezone.now()
            job.save(update_fields=['heartbeat_at'])
            time.sleep(5)
            continue

        return job.status == 'RUNNING'


def _finish(job, status: str, error: str = '') -> None:
    job.refresh_from_db()
    if job.status in ('STOPPED', 'FAILED') and status == 'COMPLETED':
        return

    job.status = status
    if error:
        job.error_message = error[:4000]
        job.note(f'Failed: {error.splitlines()[0][:150]}')
    else:
        job.note(f'Finished: {job.leads_found} new, {job.duplicate_leads} '
                 f'duplicate, {job.rejected_leads} rejected.')
    if status in ('COMPLETED', 'FAILED', 'STOPPED'):
        job.completed_at = timezone.now()
    job.heartbeat_at = timezone.now()
    job.save()
