from django.core.management.base import BaseCommand

from ...component_registry import get_shared_components
from ...settings import get_settings


class Command(BaseCommand):
    help = "Clear vector search index."

    def handle(self, *args, **options):
        config = get_settings()
        # Shared-экземпляр стора: отдельный backend_cls(...) для in-memory
        # бэкендов очищал бы «чужую» пустую коллекцию.
        _cfg, vector_store, _embedding, _resolver = get_shared_components(config)
        vector_store.clear_collection()
        self.stdout.write(self.style.SUCCESS("Search index cleared."))
