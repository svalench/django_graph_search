import warnings

from django.apps import AppConfig
from django.conf import settings as django_settings


class DjangoGraphSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_graph_search"
    verbose_name = "Django Graph Search"

    def ready(self) -> None:
        # Import settings to validate early
        from .settings import get_settings  # noqa: WPS433
        from . import signals  # noqa: WPS433,F401

        get_settings()
        if get_settings().admin_search_enabled:
            from . import admin  # noqa: WPS433

            admin.setup_admin_site()

        self._emit_production_security_warnings(get_settings())

    @staticmethod
    def _emit_production_security_warnings(cfg) -> None:
        """Однократные предупреждения о небезопасных прод-конфигурациях API."""
        if django_settings.DEBUG:
            return
        if cfg.api.require_authentication and not cfg.api.permission_classes:
            warnings.warn(
                "GRAPH_SEARCH API uses Django session authentication while "
                "POST/DELETE endpoints are csrf_exempt. Cookie-authenticated "
                "clients are exposed to CSRF. Configure API.PERMISSION_CLASSES "
                "with a token-based permission (or keep endpoints internal).",
                stacklevel=2,
                category=RuntimeWarning,
            )
        in_memory_throttle = any(
            path.endswith("SimpleScopedRateThrottle")
            for path in cfg.api.throttle_classes
        )
        if in_memory_throttle:
            warnings.warn(
                "GRAPH_SEARCH API.THROTTLE_CLASSES includes "
                "SimpleScopedRateThrottle, which keeps windows in process "
                "memory and does not work under multi-process deployments "
                "(Gunicorn/uWSGI). Use DRF throttles backed by cache/Redis.",
                stacklevel=2,
                category=RuntimeWarning,
            )
