"""Small display helpers used across the templates."""
from django import template

register = template.Library()

# Django's message tags do not line up with Bootstrap's alert classes.
ALERT_CLASSES = {
    'debug': 'secondary',
    'info': 'info',
    'success': 'success',
    'warning': 'warning',
    'error': 'danger',
}


@register.filter
def alert_class(tags):
    """Bootstrap alert suffix for a Django message tag."""
    for tag in str(tags or '').split():
        if tag in ALERT_CLASSES:
            return ALERT_CLASSES[tag]
    return 'info'


@register.filter
def status_colour(status):
    """The colour a lead status is shown in, consistently everywhere."""
    return {
        'NEW': 'info',
        'CALLED': 'secondary',
        'PROFILE_SENT': 'warning',
        'FOLLOW_UP_DUE': 'danger',
        'REQUIREMENT_RECEIVED': 'primary',
        'CONVERTED': 'success',
        'NOT_RELEVANT': 'dark',
    }.get(status, 'secondary')


@register.filter
def band_colour(band):
    return {'A': 'success', 'B': 'primary', 'C': 'warning', 'D': 'secondary'} \
        .get(band, 'secondary')


@register.filter
def dash(value):
    """Show an em dash instead of an empty cell, so gaps are obvious."""
    text = '' if value is None else str(value).strip()
    return text or '—'


@register.simple_tag
def query_replace(request, **kwargs):
    """
    Rebuild the current query string with some parameters changed.

    Used by pagination and filters so switching page keeps the active filters.
    """
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop('page', None) if 'page' not in kwargs else None
    encoded = params.urlencode()
    return f'?{encoded}' if encoded else '?'


@register.filter
def get_item(mapping, key):
    """Look up a dict key from a template, which Django cannot do directly."""
    if mapping is None:
        return ''
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''
