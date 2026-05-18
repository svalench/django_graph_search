import django
import pytest
from django.conf import settings


def pytest_configure():
    if settings.configured:
        settings.DEBUG = True
        return
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "tests.test_app",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        MIDDLEWARE=[],
        ROOT_URLCONF="django_graph_search.urls",
        SECRET_KEY="test",
        USE_TZ=True,
        DEBUG=True,
    )
    django.setup()


@pytest.fixture(autouse=True)
def _default_debug_true_for_tests():
    """Подавляет production-warning inmemory при типичном DEBUG в тестах."""
    old = settings.DEBUG
    settings.DEBUG = True
    yield
    settings.DEBUG = old

