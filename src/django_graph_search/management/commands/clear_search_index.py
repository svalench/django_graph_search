from django.core.management.base import BaseCommand
from django.utils.module_loading import import_string

from ...settings import get_settings


class Command(BaseCommand):
    help = "Clear vector search index."

    def handle(self, *args, **options):
        config = get_settings()
        backend_cls = import_string(config.vector_store.backend)
        vector_store = backend_cls(**config.vector_store.options)
        vector_store.clear_collection()
        self.stdout.write(self.style.SUCCESS("Search index cleared."))

