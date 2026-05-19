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
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.admin",
            "django_graph_search",
            "tests.test_app",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            },
        ],
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

