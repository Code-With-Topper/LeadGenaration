"""
The background worker.

Run it from cron once a minute:

    * * * * * cd /srv/leadcrm && .venv/bin/python manage.py run_worker --once

or keep it alive under systemd with `--loop`. Either way there is no broker
and nothing to subscribe to.
"""
import time

from django.core.management.base import BaseCommand

from core import worker


class Command(BaseCommand):
    help = 'Process queued imports, lead generation jobs and follow-up reminders.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='Do one pass and exit (use this from cron).')
        parser.add_argument('--loop', action='store_true',
                            help='Keep running (use this under systemd).')
        parser.add_argument('--interval', type=int, default=30,
                            help='Seconds between passes in --loop mode.')
        parser.add_argument('--jobs', type=int, default=1,
                            help='Maximum jobs to start per pass.')

    def handle(self, *args, **options):
        if options['loop']:
            self.stdout.write('Worker running. Press Ctrl+C to stop.')
            while True:
                self._pass(options['jobs'])
                time.sleep(options['interval'])
        else:
            self._pass(options['jobs'])

    def _pass(self, jobs):
        summary = worker.run_pending(max_jobs=jobs)
        active = {key: value for key, value in summary.items() if value}
        if active:
            self.stdout.write(self.style.SUCCESS(f'Worker pass: {active}'))
