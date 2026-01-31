from django.apps import AppConfig


class DjangoGraphSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_graph_search"
    verbose_name = "Django Graph Search"

    def ready(self) -> None:
        # Import settings to validate early
        from .settings import get_settings  # noqa: WPS433
        from . import signals  # noqa: WPS433,F401

        get_settings()

