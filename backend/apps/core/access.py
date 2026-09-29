from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied


class AdminRequiredMixin(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_admin:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class StaffOrAdminRequiredMixin(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {request.user.Role.ADMIN, request.user.Role.STAFF}:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not request.user.is_admin:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def staff_or_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if request.user.role not in {request.user.Role.ADMIN, request.user.Role.STAFF}:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def permission_required_staff(permission_name):
    def decorator(view_func):
        @wraps(view_func)
        @staff_or_admin_required
        def wrapped(request, *args, **kwargs):
            if request.user.is_admin:
                return view_func(request, *args, **kwargs)
            if not request.user.permissions.get(permission_name, False):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator