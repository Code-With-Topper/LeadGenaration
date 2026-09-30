"""
Mark leads whose follow-up date has arrived.

`run_worker` already does this on every pass. This command exists so the rule
can be scheduled on its own — for example once each morning — and so it can be
run by hand to check the result.
"""
from django.core.management.base import BaseCommand

from core import worker


class Command(BaseCommand):
    help = 'Move leads whose follow-up date has arrived into Follow-up Due.'

    def handle(self, *args, **options):
        count = worker.sweep_followups()
        if count:
            self.stdout.write(self.style.SUCCESS(
                f'{count} lead(s) are now due for follow-up.'))
        else:
            self.stdout.write('No follow-ups are due today.')
