"""
The only way an email leaves this system.

Every send passes the same four gates, in this order:

    1. the lead is still open
    2. the address is valid
    3. the address is not on the suppression list
    4. today's quota is not exhausted

A blocked send is still written to the log with the reason, so the client can
see why a message did not go out instead of wondering.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from core.validation import validate_email
from leads.models import Suppression

from .models import EmailLog, EmailQuota

LOGGER = logging.getLogger('emails')


@dataclass
class SendResult:
    ok: bool
    status: str            # SENT | FAILED | BLOCKED
    message: str           # shown to the user
    log: EmailLog | None = None


def check_can_send(lead) -> tuple[bool, str]:
    """
    Would a send to this lead be allowed right now?

    Used by the views to disable the button and explain why, before the user
    has written a message they cannot send.
    """
    if lead.is_closed:
        return False, f"This lead is marked {lead.get_status_display()} — no more emails."

    recipient = lead.best_email
    if not recipient:
        return False, "This lead has no valid email address."

    if not validate_email(recipient).ok:
        return False, f"{recipient} is not a valid email address."

    if Suppression.blocks_email(recipient):
        return False, f"{recipient} has unsubscribed."

    remaining = EmailQuota.remaining_today()
    if remaining <= 0:
        return False, (f"Today's limit of {EmailQuota.limit()} emails is used up. "
                       "You can send again tomorrow.")

    return True, f"{remaining} of {EmailQuota.limit()} emails left today."


def send_to_lead(lead, subject: str, body: str, *, attachment=None,
                 template=None, user=None,
                 mark_profile_sent: bool = False) -> SendResult:
    """
    Send one message and record it.

    `mark_profile_sent` is what starts the client's follow-up clock: it sets
    the lead to Profile Sent and schedules the reminder 7 (or 10) days out.
    """
    recipient = lead.best_email

    allowed, reason = check_can_send(lead)
    if not allowed:
        log = EmailLog.objects.create(
            lead=lead, template=template, recipient=recipient or '(none)',
            subject=subject, message=body, status='BLOCKED',
            error_message=reason, sent_by=_real_user(user),
        )
        return SendResult(ok=False, status='BLOCKED', message=reason, log=log)

    body = _append_footer(body, recipient)

    try:
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=get_connection(),
            headers={'List-Unsubscribe': f"<{_unsubscribe(recipient)}>"},
        )
        if attachment:
            message.attach(attachment.name, attachment.read(),
                           getattr(attachment, 'content_type', None)
                           or 'application/octet-stream')
        message.send(fail_silently=False)
    except Exception as exc:                      # SMTP, DNS, auth, timeout
        LOGGER.warning("Email to %s failed: %s", recipient, exc)
        log = EmailLog.objects.create(
            lead=lead, template=template, recipient=recipient, subject=subject,
            message=body, status='FAILED', error_message=str(exc),
            sent_by=_real_user(user),
        )
        return SendResult(ok=False, status='FAILED',
                          message=f"Could not send: {exc}", log=log)

    # Only a genuine send consumes quota.
    EmailQuota.record_send()

    log = EmailLog.objects.create(
        lead=lead, template=template, recipient=recipient, subject=subject,
        message=body, attachment=attachment if attachment else None,
        status='SENT', sent_by=_real_user(user),
    )

    _advance_lead(lead, mark_profile_sent=mark_profile_sent)

    remaining = EmailQuota.remaining_today()
    return SendResult(
        ok=True, status='SENT', log=log,
        message=f"Email sent to {recipient}. {remaining} left today.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _advance_lead(lead, *, mark_profile_sent: bool):
    from leads.models import Lead

    lead.last_contacted_at = timezone.now()
    if not lead.first_contact_date:
        lead.first_contact_date = timezone.localdate()

    if mark_profile_sent:
        lead.save(update_fields=['last_contacted_at', 'first_contact_date',
                                 'updated_at'])
        # Sets Profile Sent and schedules the follow-up date in one place.
        lead.mark_profile_sent()
        return

    if lead.status == Lead.NEW:
        lead.status = Lead.CALLED
    lead.save(update_fields=['status', 'last_contacted_at',
                             'first_contact_date', 'updated_at'])


def _unsubscribe(recipient):
    from .models import unsubscribe_url
    return unsubscribe_url(recipient)


def _append_footer(body: str, recipient: str) -> str:
    """
    Sender identity and a working unsubscribe link on every message.

    Required for the client's own domain reputation, and the reason the
    unsubscribe link has to be real rather than decorative.
    """
    if '{{unsubscribe_link}}' in body:
        return body.replace('{{unsubscribe_link}}', _unsubscribe(recipient))

    footer = (
        f"\n\n--\n{settings.COMPANY_NAME}\n{settings.COMPANY_ADDRESS}\n\n"
        f"You received this because we believe your business may need our "
        f"services. To stop receiving emails from us, click here:\n"
        f"{_unsubscribe(recipient)}\n"
    )
    return body.rstrip() + footer


def _real_user(user):
    return user if user is not None and getattr(user, 'is_authenticated', False) else None
