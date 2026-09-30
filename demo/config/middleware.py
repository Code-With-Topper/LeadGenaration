from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Exclude paths that don't need authentication
        allowed_paths = [reverse('login'), '/admin/', '/', '/privacy-policy/', '/terms/', '/sitemap.xml', '/robots.txt']
        
        # If user is not authenticated and path is not allowed
        if not request.user.is_authenticated:
            if not any(request.path == p or (p != '/' and request.path.startswith(p)) for p in allowed_paths):
                return redirect('login')
        else:
            # Role-Based Access Control
            if not request.user.is_superuser:
                groups = [g.name for g in request.user.groups.all()]
                is_staff = 'Staff' in groups
                is_manager = 'Manager' in groups
                
                # Staff cannot access reports or settings
                if is_staff and not is_manager:
                    if request.path.startswith('/reports/') or request.path.startswith('/settings/'):
                        from django.core.exceptions import PermissionDenied
                        raise PermissionDenied("You do not have permission to access this page.")
                        
        response = self.get_response(request)
        return response
