from django.core.management.base import BaseCommand

from ...settings import get_settings


class Command(BaseCommand):
    help = "Show configured vector search index settings."

    def handle(self, *args, **options):
        config = get_settings()
        self.stdout.write(f"Vector store backend: {config.vector_store.backend}")
        self.stdout.write(f"Default embedding: {config.default_embedding}")
        self.stdout.write("Embeddings:")
        for name, profile in config.embeddings.items():
            self.stdout.write(f" - {name}: {profile.backend} ({profile.model_name})")
        self.stdout.write("Models:")
        for model_cfg in config.models:
            self.stdout.write(f" - {model_cfg.model} (fields: {', '.join(model_cfg.fields)})")

