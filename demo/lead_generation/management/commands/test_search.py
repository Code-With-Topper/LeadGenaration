"""
Diagnose lead generation from the server it actually runs on.

When a run finds nothing, the question is always the same: is the browser
broken, is the network blocked, or is every search engine refusing us? This
answers it in one command, without touching the database.

    python manage.py test_search
    python manage.py test_search --query "sponge iron company in Durgapur"
    python manage.py test_search --url https://example.in    # test extraction
"""
from django.core.management.base import BaseCommand

from lead_generation import engine


class Command(BaseCommand):
    help = 'Check that the browser starts and that a search engine answers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--query',
            default='sponge iron manufacturer company in Durgapur, West Bengal',
            help='The search to try.')
        parser.add_argument(
            '--url',
            help='Skip searching and crawl this one site, to test extraction.')
        parser.add_argument('--limit', type=int, default=5,
                            help='How many results to ask for.')
        parser.add_argument(
            '--debug', action='store_true',
            help='Save each results page to a file, to see what came back.')

    def handle(self, *args, **options):
        ok = self.style.SUCCESS
        bad = self.style.ERROR
        warn = self.style.WARNING

        self.stdout.write('1. Starting the browser…')
        try:
            with engine.Browser() as browser:
                self.stdout.write(ok('   started.'))

                identity = browser.page.evaluate(
                    '() => ({ua: navigator.userAgent, '
                    'wd: navigator.webdriver, lang: navigator.language})')
                self.stdout.write(f'   user agent : {identity["ua"]}')
                self.stdout.write(f'   webdriver  : {identity["wd"]}')
                self.stdout.write(f'   language   : {identity["lang"]}')
                if 'Headless' in (identity['ua'] or ''):
                    self.stdout.write(bad(
                        '   PROBLEM: the browser is announcing itself as '
                        'headless. Search engines will block it.'))
                if identity['wd']:
                    self.stdout.write(bad(
                        '   PROBLEM: navigator.webdriver is set.'))

                if options['url']:
                    self._crawl_one(browser, options['url'])
                    return

                self.stdout.write('\n2. Asking each search source…')
                query = options['query']
                self.stdout.write(f'   query    : {query}')
                self.stdout.write(f'   sent as  : {engine.with_exclusions(query)}\n')

                any_worked = False
                for name, search in engine.SEARCH_SOURCES:
                    self.stdout.write(f'   {name}…')
                    try:
                        results, outcome, stats = search(browser, query,
                                                         options['limit'])
                    except Exception as exc:
                        self.stdout.write(bad(f'     crashed: {exc}'))
                        continue

                    if outcome == engine.SearchOutcome.OK:
                        any_worked = True
                        self.stdout.write(ok(f'     {len(results)} result(s)'
                                             f'  [{stats.summary}]'))
                        for title, url in results:
                            self.stdout.write(f'       - {title[:58]}')
                            self.stdout.write(f'         {url[:76]}')
                    elif outcome == engine.SearchOutcome.NO_MATCH:
                        # The engine worked. Something about the results, or
                        # the filters, is the problem — say which.
                        self.stdout.write(warn(
                            '     WORKED, but no result was a company site.'))
                        self.stdout.write(f'       {stats.summary}')
                        self._explain_no_match(stats)
                    elif outcome == engine.SearchOutcome.BLOCKED:
                        self.stdout.write(warn(
                            '     BLOCKED — a challenge, consent wall or rate '
                            'limit, not a real answer.'))
                        self.stdout.write(
                            f'       landed on: {browser.url[:76]}')
                        self.stdout.write(f'       page title: {browser.title()[:70]}')
                    elif outcome == engine.SearchOutcome.UNREACHABLE:
                        self.stdout.write(bad(
                            '     UNREACHABLE — the page would not load. '
                            'Check outbound network and DNS.'))
                    else:
                        self.stdout.write(warn(
                            '     the page answered but held no result links.'))

                    if options['debug']:
                        self._dump(browser, name)

                self.stdout.write('')
                if any_worked:
                    self.stdout.write(ok(
                        'At least one source works — lead generation can run.'))
                else:
                    self.stdout.write(bad('No source answered.'))
                    self.stdout.write(
                        '\nWhat to try, in order:\n'
                        '  1. Wait an hour. Rate limits are usually temporary.\n'
                        '  2. Check outbound HTTPS works:\n'
                        '       curl -I https://html.duckduckgo.com/\n'
                        '  3. If the server is on a shared or data-centre IP, '
                        'search engines often refuse it outright. Import leads '
                        'from CSV instead, which never uses a search engine.\n')
        except Exception as exc:
            self.stdout.write(bad(f'   the browser would not start: {exc}'))
            self.stdout.write(
                '\nThis is almost always a missing browser. Install it with:\n'
                '    playwright install --with-deps chromium\n'
                'If the browser lives somewhere unusual, set CHROME_BINARY to '
                'its full path.\n')

    def _explain_no_match(self, stats):
        """Point at the actual cause, rather than leaving a dead end."""
        if stats.directory and stats.directory >= stats.anchors // 2:
            self.stdout.write(
                '       Most results were directory sites (IndiaMART, '
                'Justdial and similar).\n'
                '       Those are listings, not companies, so they are not '
                'stored as leads.\n'
                '       Try a narrower query, e.g. the town plus the product '
                'without the word "company".')
        elif stats.bad_url:
            self.stdout.write(
                '       Links were found but could not be turned into usable '
                'addresses.\n'
                '       That points at the URL decoder for this engine — worth '
                'reporting with --debug output.')
        elif stats.listicle:
            self.stdout.write(
                '       Results were "Top 10 …" style list pages rather than '
                'companies.\n'
                '       Try a more specific query.')
        else:
            self.stdout.write(
                '       Run again with --debug to save the page and see what '
                'came back.')

    def _dump(self, browser, name):
        """Save the page so its markup can be looked at afterwards."""
        import pathlib
        target = pathlib.Path(f'search-debug-{name.lower()}.html')
        try:
            target.write_text(browser.html(), encoding='utf-8')
            self.stdout.write(f'       page saved to {target}')
        except Exception as exc:
            self.stdout.write(f'       could not save the page: {exc}')

    def _crawl_one(self, browser, url):
        """Crawl one site and print what was extracted."""
        self.stdout.write(f'\n2. Crawling {url} …')
        plan = engine.SearchPlan(query='manual test', state='West Bengal')
        findings = engine.crawl_site(browser, url, '', plan)

        self.stdout.write(f'   pages read   : {findings.pages_visited}')
        self.stdout.write(f'   company name : {findings.best_name or "(none found)"}')
        self.stdout.write(f'   emails       : {findings.emails or "(none)"}')
        self.stdout.write(f'   phones       : {findings.phones or "(none)"}')
        self.stdout.write(f'   CIN / GSTIN  : {findings.cin or "-"} / {findings.gstin or "-"}')
        self.stdout.write(f'   PIN          : {findings.pin_code or "-"}')
        self.stdout.write(f'   address      : {findings.address[:70] or "-"}')

        if findings.best_name and (findings.emails or findings.phones):
            self.stdout.write(self.style.SUCCESS(
                '\n   Extraction works on this site.'))
        else:
            self.stdout.write(self.style.WARNING(
                '\n   Little was found. Either the site publishes no contact '
                'details, or robots.txt disallows the pages that hold them.'))
