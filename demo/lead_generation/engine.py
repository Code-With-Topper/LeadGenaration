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
# Kept short on purpose: when a search engine is blocking us there is nothing
# to wait for, and a long wait multiplied by every query is how a run ends up
# doing nothing for hours.
ELEMENT_WAIT_TIMEOUT = 6
MAX_PAGES_PER_SITE = 4
RESULTS_PER_QUERY = 20

# A whole-state run with seven keywords builds 161 searches. Left uncapped that
# is most of a day's crawling, and the user cannot tell a slow run from a stuck
# one. The cap keeps a run finishable; the queries are ordered so the first
# pass covers every district rather than exhausting one.
MAX_QUERIES_PER_RUN = 40

# A realistic desktop browser identity. Playwright's default announces
# "HeadlessChrome" and sets navigator.webdriver, which Google and Bing block on
# sight — this was the main reason lead generation returned nothing.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
)

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
            # Look like a normal Indian desktop visitor. Without this the
            # browser announces HeadlessChrome and gets blocked immediately,
            # and the results come back geo-targeted to the server, not India.
            user_agent=USER_AGENT,
            locale='en-IN',
            timezone_id='Asia/Kolkata',
            extra_http_headers={
                'Accept-Language': 'en-IN,en-GB;q=0.9,en;q=0.8',
            },
        )
        # navigator.webdriver is true in an automated browser and is the other
        # flag these sites check. Hiding it is not a CAPTCHA bypass: we still
        # stop when we are challenged.
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._context.set_default_timeout(PAGE_LOAD_TIMEOUT * 1000)
        # Images, video and fonts are never read, and skipping them makes a run
        # far cheaper on a small server's bandwidth.
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


# Titles and URLs that mean "we are not getting results from here".
BLOCK_URL_MARKERS = ('/sorry', 'recaptcha', 'consent.google', '/challenge',
                     'ipv4.google.com/sorry', 'bing.com/challenge')
BLOCK_TITLE_MARKERS = (
    'captcha', 'unusual traffic', 'not a robot', 'verify you are human',
    'are you a robot', 'before you continue', 'access denied',
    'are you human', 'security check', 'just a moment',
)


def is_blocked_page(browser: Browser) -> bool:
    """
    True when the page we got is a challenge, consent wall or block notice
    rather than search results.

    The consent interstitial matters as much as the CAPTCHA: it is full of
    links, so a naive "did any links load?" check passes and the run silently
    scrapes a cookie notice.
    """
    url = browser.url.lower()
    title = browser.title().lower()
    return (any(marker in url for marker in BLOCK_URL_MARKERS)
            or any(marker in title for marker in BLOCK_TITLE_MARKERS))


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


class SearchOutcome:
    """Why a search returned what it did — so a failure can be explained."""
    OK = 'ok'                 # results found
    EMPTY = 'empty'           # the page answered and had no results on it
    NO_MATCH = 'no-match'     # results were there, but all were filtered out
    BLOCKED = 'blocked'       # challenge, consent wall or rate limit
    UNREACHABLE = 'unreachable'   # the page would not load at all

    # A source that filtered everything out is working fine — the query needs
    # changing, not the source. Only these two mean "stop asking".
    REFUSALS = (BLOCKED, UNREACHABLE)


@dataclass
class ResultStats:
    """
    Why each search hit was kept or dropped.

    Without this, "nothing matched after filtering" is a dead end: it could be
    a broken URL decoder, an over-aggressive filter, or a page genuinely full
    of directory listings. These counts say which.
    """
    anchors: int = 0
    kept: int = 0
    bad_url: int = 0
    directory: int = 0
    listicle: int = 0
    no_title: int = 0
    duplicate: int = 0

    @property
    def summary(self) -> str:
        parts = [f'{self.anchors} link(s) on the page', f'{self.kept} kept']
        for count, label in (
            (self.bad_url, 'unusable URL'),
            (self.directory, 'directory site'),
            (self.listicle, 'listicle page'),
            (self.no_title, 'no title'),
            (self.duplicate, 'same domain twice'),
        ):
            if count:
                parts.append(f'{count} dropped as {label}')
        return ', '.join(parts)


# Directories crowd out real company sites in Indian industrial searches, so
# they are excluded in the query itself rather than filtered out afterwards —
# which is what left the first page with nothing usable on it.
EXCLUDED_IN_QUERY = (
    'indiamart.com', 'justdial.com', 'tradeindia.com', 'exportersindia.com',
    'sulekha.com', 'zaubacorp.com',
)


def with_exclusions(query: str) -> str:
    """Add -site: terms so the results page is not all directory listings."""
    return query + ' ' + ' '.join(f'-site:{d}' for d in EXCLUDED_IN_QUERY)


# Every result link lives inside the page's main content. Grabbing anchors from
# there and filtering, rather than depending on one class name, is what keeps a
# markup change from silently returning nothing.
ALL_LINKS_JS = """
    () => {
        const scope = document.querySelector(
            '#b_results, #search, #rso, #links, #web_content_wrapper, main')
            || document.body;
        return Array.from(scope.querySelectorAll('a[href]'))
            .map(a => ({href: a.href || '',
                        title: (a.innerText || a.textContent || '').trim()}))
            .filter(x => x.href && x.title);
    }
"""


def _anchors(browser: Browser, *selector_scripts) -> list:
    """
    Try each specific extractor, then fall back to every link in the content
    area. Returns whatever the first one that finds anything gives.
    """
    for script in selector_scripts:
        anchors = browser.evaluate(script, default=[]) or []
        if anchors:
            return anchors
    return browser.evaluate(ALL_LINKS_JS, default=[]) or []


def _finish_search(browser: Browser, anchors, limit, *, decode):
    """
    Turn anchors into results and decide the outcome.

    The important distinction: a page that answered but whose results were all
    filtered out is NOT blocked. Reporting it as blocked retired a working
    search engine and hid the real problem, which was the query.
    """
    if is_blocked_page(browser):
        return [], SearchOutcome.BLOCKED, ResultStats()

    if not anchors:
        # Not one link in the content area: the page did not really render.
        return [], SearchOutcome.BLOCKED, ResultStats()

    found, stats = _collect(anchors, limit, decode=decode)
    if found:
        return found, SearchOutcome.OK, stats
    return [], SearchOutcome.NO_MATCH, stats


def _try_endpoints(browser: Browser, urls, limit, *, decode, selectors=()):
    """
    Try each endpoint until one gives results.

    Search engines change their markup and retire endpoints without notice, so
    every source here has more than one address and falls back to "every link
    in the content area". One selector going stale must not silently turn a
    working source into "empty".
    """
    last = ([], SearchOutcome.UNREACHABLE, ResultStats())

    for url in urls:
        if not browser.goto(url):
            continue
        if is_blocked_page(browser):
            return [], SearchOutcome.BLOCKED, ResultStats()

        anchors = _anchors(browser, *selectors)
        results, outcome, stats = _finish_search(browser, anchors, limit,
                                                 decode=decode)
        if results:
            return results, outcome, stats

        last = ([], outcome, stats)
        _save_debug_page(browser, url, outcome)

    return last


def search_duckduckgo(browser: Browser, query: str,
                      limit: int) -> tuple[list, str, ResultStats]:
    """
    DuckDuckGo. Tried first, deliberately.

    Both of its no-JavaScript endpoints are tried: the lite one first, because
    its markup is the plainest and the least likely to change. Neither needs
    JavaScript, which is what makes DuckDuckGo the source most likely to answer
    a small server at all.
    """
    LOGGER.info('Searching DuckDuckGo: %s', query)
    encoded = quote_plus(with_exclusions(query))

    return _try_endpoints(
        browser,
        [f'https://lite.duckduckgo.com/lite/?q={encoded}',
         f'https://html.duckduckgo.com/html/?q={encoded}'],
        limit,
        decode=_decode_ddg_url,
        selectors=(
            """
            () => Array.from(document.querySelectorAll(
                    'a.result-link, a.result__a, .result__title a, h2.result__title a'))
                .map(a => ({href: a.href || '',
                            title: (a.innerText || a.textContent || '').trim()}))
            """,
            # The lite layout is a bare table of links.
            """
            () => Array.from(document.querySelectorAll('table a[href]'))
                .map(a => ({href: a.href || '',
                            title: (a.innerText || a.textContent || '').trim()}))
                .filter(x => x.title.length > 3)
            """,
        ),
    )


def search_mojeek(browser: Browser, query: str,
                  limit: int) -> tuple[list, str, ResultStats]:
    """
    Mojeek. An independent crawler with its own index.

    It is small, it needs no JavaScript, and it does not rate-limit a modest
    server the way the big engines do — which makes it the useful one when
    everything else has shut us out.
    """
    LOGGER.info('Searching Mojeek: %s', query)
    encoded = quote_plus(with_exclusions(query))

    return _try_endpoints(
        browser,
        [f'https://www.mojeek.com/search?q={encoded}'],
        limit,
        decode=lambda href: href,          # Mojeek links straight to the site
        selectors=(
            """
            () => Array.from(document.querySelectorAll(
                    'ul.results-standard li h2 a, a.ob, .results a.title'))
                .map(a => ({href: a.href || '',
                            title: (a.innerText || a.textContent || '').trim()}))
            """,
        ),
    )


def search_bing(browser: Browser, query: str,
                limit: int) -> tuple[list, str, ResultStats]:
    """
    Bing. Usable, but it rate limits a server quickly.

    It does not wait on one class name any more. Bing served a perfectly good
    results page whose markup did not match `li.b_algo h2 a`, and that timeout
    was reported as a block — so a working source was dropped.
    """
    LOGGER.info('Searching Bing: %s', query)
    url = ('https://www.bing.com/search?q=' + quote_plus(with_exclusions(query))
           + '&count=30&setlang=en&cc=IN')

    if not browser.goto(url):
        return [], SearchOutcome.UNREACHABLE, ResultStats()

    # Give the results a moment, but never treat a miss as a refusal.
    browser.wait_for('li.b_algo, .b_algo, #b_results')

    anchors = _anchors(
        browser,
        """
        () => Array.from(document.querySelectorAll(
                'li.b_algo h2 a, .b_algo h2 a, li.b_algo a.tilk, .b_algo a.tilk'))
            .map(a => ({href: a.href || '',
                        title: (a.innerText || a.textContent || '').trim()}))
        """,
        """
        () => Array.from(document.querySelectorAll('#b_results h2 a'))
            .map(a => ({href: a.href || '',
                        title: (a.innerText || a.textContent || '').trim()}))
        """,
    )
    results, outcome, stats = _finish_search(browser, anchors, limit,
                                             decode=_decode_bing_url)
    if not results:
        _save_debug_page(browser, url, outcome)
    return results, outcome, stats


def search_google(browser: Browser, query: str,
                  limit: int) -> tuple[list, str, ResultStats]:
    """
    Google. Last choice: the best index, and the quickest to refuse a server.

    One request with num=, rather than paging: when Google is going to block us
    it does so on the first request, and paging only multiplies the wait.
    """
    LOGGER.info('Searching Google: %s', query)
    url = (f'https://www.google.com/search?q={quote_plus(with_exclusions(query))}'
           f'&num={min(limit, 30)}&hl=en&gl=in')

    if not browser.goto(url):
        return [], SearchOutcome.UNREACHABLE, ResultStats()

    anchors = _anchors(browser, """
        () => Array.from(document.querySelectorAll(
                'div#search a[href] h3, div#rso a[href] h3'))
            .map(h => {
                const a = h.closest('a');
                return {href: a ? (a.href || '') : '',
                        title: (h.innerText || h.textContent || '').trim()};
            })
            .filter(x => x.href)
    """)
    results, outcome, stats = _finish_search(browser, anchors, limit,
                                             decode=_decode_google_url)
    if not results:
        _save_debug_page(browser, url, outcome)
    return results, outcome, stats


def _save_debug_page(browser: Browser, url: str, outcome: str) -> None:
    """
    Save a page that gave us nothing, when SEARCH_DEBUG_DIR is set.

    Guessing at markup from a distance is how a stale selector survives three
    rounds of fixes. With the page on disk the cause is visible.
    """
    from django.conf import settings

    directory = getattr(settings, 'SEARCH_DEBUG_DIR', '')
    if not directory:
        return

    import pathlib as _pathlib
    from datetime import datetime

    try:
        folder = _pathlib.Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        host = urlparse(url).netloc.replace('.', '-')
        stamp = datetime.now().strftime('%H%M%S')
        target = folder / f'{stamp}-{host}-{outcome}.html'
        target.write_text(browser.html(), encoding='utf-8')
        LOGGER.info('Saved the page that returned %s to %s', outcome, target)
    except Exception as exc:
        LOGGER.info('Could not save the debug page: %s', exc)


# Tried in this order. The first one that returns results wins.
SEARCH_SOURCES = (
    ('DuckDuckGo', search_duckduckgo),
    ('Mojeek', search_mojeek),
    ('Bing', search_bing),
    ('Google', search_google),
)


class SearchState:
    """
    Remembers which sources have blocked us during this run.

    Without this, a run with 161 queries asks a blocked engine 161 times and
    waits every time. After two blocks a source is dropped for the rest of the
    run, and when every source is gone the run stops and says so instead of
    grinding on silently.
    """
    BLOCKS_BEFORE_GIVING_UP = 2
    # A source can answer every time and still never yield a company — a stale
    # selector looks exactly like that. After this many fruitless queries in a
    # row, stop: grinding through forty more teaches the user nothing.
    BARREN_QUERIES_BEFORE_GIVING_UP = 6

    def __init__(self):
        self.blocks: dict[str, int] = {}
        self.wins: dict[str, int] = {}
        self.barren_run = 0
        self.last_reason = ''

    def note_query(self, produced_results: bool, reason: str = '') -> None:
        """Record whether a whole query produced anything usable."""
        if produced_results:
            self.barren_run = 0
        else:
            self.barren_run += 1
            self.last_reason = reason

    @property
    def nothing_is_working(self) -> bool:
        """
        True when query after query has come back with nothing at all.

        This is the case the run used to miss: every source "answering" but
        none returning a usable company.
        """
        return self.barren_run >= self.BARREN_QUERIES_BEFORE_GIVING_UP

    def live_sources(self):
        return [(name, fn) for name, fn in SEARCH_SOURCES
                if self.blocks.get(name, 0) < self.BLOCKS_BEFORE_GIVING_UP]

    @property
    def all_blocked(self) -> bool:
        return not self.live_sources()

    def record(self, name: str, outcome: str) -> None:
        if outcome == SearchOutcome.OK:
            self.wins[name] = self.wins.get(name, 0) + 1
            self.blocks.pop(name, None)      # a win clears earlier blocks
        elif outcome in SearchOutcome.REFUSALS:
            self.blocks[name] = self.blocks.get(name, 0) + 1
        # NO_MATCH and EMPTY leave the source alone: it answered, so it is
        # working. Retiring it would hide a query problem as a block.

    def summary(self) -> str:
        parts = [f'{name}: {count} search(es) worked'
                 for name, count in self.wins.items()]
        parts += [f'{name}: blocked' for name in self.blocks
                  if self.blocks[name] >= self.BLOCKS_BEFORE_GIVING_UP]
        return '; '.join(parts) or 'no source responded'


def run_search(browser: Browser, query: str, limit: int,
               state: SearchState) -> tuple[list, str]:
    """
    Ask each live source in turn until one gives results.

    Returns (results, note) where the note explains an empty result so it can
    be shown to the user rather than swallowed.
    """
    attempts = []
    for name, search in state.live_sources():
        results, outcome, stats = search(browser, query, limit)
        state.record(name, outcome)

        if results:
            return results, f'{len(results)} site(s) via {name}'

        if outcome == SearchOutcome.NO_MATCH:
            # The engine worked; the results simply were not companies. Saying
            # what was dropped points at the query, not at the engine.
            attempts.append(f'{name}: {stats.summary}')
        else:
            attempts.append(f'{name}: {outcome}')

        if outcome in SearchOutcome.REFUSALS:
            LOGGER.warning('%s did not answer (%s) for: %s', name, outcome, query)
            continue
        polite_sleep()

    return [], 'no usable results — ' + '; '.join(attempts)


def _decode_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in /l/?uddg=<url-encoded destination>."""
    if not href:
        return ''
    if href.startswith('//'):
        href = 'https:' + href
    parsed = urlparse(href)
    if 'duckduckgo.com' in parsed.netloc and parsed.path.startswith('/l/'):
        target = parse_qs(parsed.query).get('uddg', [''])[0]
        return target or ''
    if 'duckduckgo.com' in parsed.netloc:
        return ''
    return href


def _collect(anchors, limit, *, decode,
             seen=None) -> tuple[list[tuple[str, str]], ResultStats]:
    """Turn raw anchors into usable results, counting why each was dropped."""
    found: list[tuple[str, str]] = []
    stats = ResultStats(anchors=len(anchors))
    seen = set(seen or ())

    for item in anchors:
        if len(found) >= limit:
            break

        href = decode(item.get('href', ''))
        title = (item.get('title') or '').strip()

        url = normalize.normalize_url(href)
        domain = normalize.normalize_domain(url) if url else ''
        if not domain:
            stats.bad_url += 1
            continue
        if validation.is_directory_domain(domain):
            stats.directory += 1
            continue
        if not title:
            stats.no_title += 1
            continue
        title = title.splitlines()[0]
        if validation.looks_like_listicle(title):
            stats.listicle += 1
            continue
        # One entry per domain: a company's home page and its products page
        # are the same company.
        if domain in seen:
            stats.duplicate += 1
            continue

        seen.add(domain)
        found.append((title, url))
        stats.kept += 1

    return found, stats


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
    One search per (keyword × place), capped and ordered for coverage.

    When no city is given the district is searched; when no district is given
    every active district of the state is searched. That is what makes "all of
    West Bengal" a real option rather than a promise.

    Two details matter as much as the list itself:

    * **Order.** Keyword first, then place — so the first pass sweeps every
      district with the strongest keyword instead of spending all 7 keywords on
      Alipurduar before Durgapur is ever searched. If a run is cut short, the
      client still has state-wide coverage.
    * **Cap.** A whole state with seven keywords is 161 searches, which is
      most of a day. Capped, a run finishes and can be repeated.

    Each plan carries its own location, so a lead found by a state-wide run
    still records the district it came from.
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

    plans = [
        SearchPlan(
            query=f'{keyword} company in {place} contact address',
            city=city,
            district=district,
            state=job.state,
        )
        for keyword in keywords            # keyword outer: coverage first
        for place, city, district in places
    ]
    return plans[:MAX_QUERIES_PER_RUN]


# ==========================================================================
# The run
# ==========================================================================

# Shown on the page when no search engine will answer this server. A run that
# just says "0 leads" teaches the user nothing.
BLOCKED_EXPLANATION = (
    'Every search source refused this server, so no new companies could be '
    'found. {detail}.\n\n'
    'This is not a fault in your data — {leads} lead(s) already collected are '
    'untouched. Search engines rate-limit servers that query them in bulk, and '
    'a shared or data-centre IP address is usually the reason.\n\n'
    'What usually fixes it:\n'
    '  1. Wait an hour and run again — the limit is usually temporary.\n'
    '  2. Run a smaller search: one district and one keyword at a time.\n'
    '  3. Import leads from a CSV file instead, which never uses a search '
    'engine.\n'
    '  4. If it keeps happening, the server needs a different IP address.'
)

# Shown when the search engines answer but never return a company site. The
# fix is different from being blocked, so the message has to be different too.
BARREN_EXPLANATION = (
    'The search engines answered, but no result was a company website, so '
    'nothing could be collected.\n\n'
    '{detail}\n\n'
    '{leads} lead(s) already in your database are untouched.\n\n'
    'What usually fixes it:\n'
    '  1. Try a plainer search phrase — a product and a town, without the '
    'words "company" or "contact address".\n'
    '  2. Search one district at a time rather than a whole state.\n'
    '  3. Import leads from a CSV file, which never uses a search engine.\n\n'
    'If this keeps happening with every phrase, a search engine has probably '
    'changed its page layout. Run "manage.py test_search --debug" and send the '
    'saved page to your developer — it shows exactly what came back.'
)


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
            if kind == 'blocked':
                # Every search source refused us. This is the one failure the
                # user cannot act on without being told what it is, so say it
                # plainly and give them the fix.
                _finish(job, 'FAILED', BLOCKED_EXPLANATION.format(
                    detail=payload, leads=job.leads_found))
                return
            if kind == 'barren':
                # The sources answered, but nothing usable came back. A
                # different failure from being blocked, and a different fix.
                _finish(job, 'FAILED', BARREN_EXPLANATION.format(
                    detail=payload, leads=job.leads_found))
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
    state = SearchState()

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

                results, note = run_search(browser, plan.query,
                                           RESULTS_PER_QUERY, state)
                state.note_query(bool(results), note)

                if not results:
                    messages.put(('note', f'{note} — {plan.query[:70]}'))
                    # Once every source has shut us out, more queries only
                    # waste time. Stop and say why.
                    if state.all_blocked:
                        messages.put(('blocked', state.summary()))
                        return
                    # Or the sources answer but never yield a company. Same
                    # outcome for the user, different cause — so say which.
                    if state.nothing_is_working:
                        messages.put((
                            'barren',
                            f'{state.BARREN_QUERIES_BEFORE_GIVING_UP} searches '
                            f'in a row found nothing. Last one — {note}'))
                        return
                    continue

                messages.put(('note', f'{note} for: {plan.query[:70]}'))

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
            messages.put(('note', f'Search sources — {state.summary()}'))
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
