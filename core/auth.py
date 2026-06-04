"""Bot / brute-force protection for the operator login.

Locks out an IP after too many failed login attempts within a window, using
the shared (database) cache so it works across gunicorn workers and survives
restarts. Configurable via settings:

    LOGIN_MAX_ATTEMPTS   (default 8)
    LOGIN_LOCKOUT_SECONDS (default 900 = 15 min)
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache


def _client_ip(request) -> str:
    """Best-effort client IP, honoring the platform proxy header."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _max_attempts() -> int:
    try:
        return int(getattr(settings, "LOGIN_MAX_ATTEMPTS", 8))
    except (TypeError, ValueError):
        return 8


def _lockout_seconds() -> int:
    try:
        return int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", 900))
    except (TypeError, ValueError):
        return 900


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"

    def _key(self):
        return f"loginfail:{_client_ip(self.request)}"

    def _attempts(self) -> int:
        return cache.get(self._key(), 0)

    def post(self, request, *args, **kwargs):
        # Blocked? Don't even check credentials.
        if self._attempts() >= _max_attempts():
            return self.render_to_response(
                self.get_context_data(form=self.get_form(), locked=True)
            )
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(self._key())  # success → clear the counter
        return super().form_valid(form)

    def form_invalid(self, form):
        key = self._key()
        try:
            count = cache.get(key, 0) + 1
            cache.set(key, count, _lockout_seconds())
        except Exception:
            pass
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("locked", self._attempts() >= _max_attempts())
        ctx["lockout_minutes"] = max(_lockout_seconds() // 60, 1)
        return ctx
