from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """
    Everything is private unless it is explicitly public.

    A deny-by-default list is the safe way round: a view added later is
    protected automatically rather than being exposed until somebody notices.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    # Paths reachable without signing in.
    PUBLIC_PREFIXES = (
        '/login/',
        '/privacy-policy/',
        '/terms/',
        '/robots.txt',
        '/sitemap.xml',
        '/static/',
        # Unsubscribing must work for a recipient who has no account.
        '/emails/unsubscribe/',
    )

    def __call__(self, request):
        if not request.user.is_authenticated and not self._is_public(request.path):
            login_url = reverse('login')
            return redirect(f'{login_url}?next={request.path}')
        return self.get_response(request)

    def _is_public(self, path):
        if path == '/':
            return True
        return any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES)
