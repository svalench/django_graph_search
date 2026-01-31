import django
from django.conf import settings


def pytest_configure():
    if settings.configured:
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
    )
    django.setup()

