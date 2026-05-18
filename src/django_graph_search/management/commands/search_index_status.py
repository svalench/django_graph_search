from django.core.management.base import BaseCommand

from ...index_coverage import get_index_coverage
from ...settings import get_settings


class Command(BaseCommand):
    help = "Show configured vector search index settings and index coverage (DB vs store)."

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

        report = get_index_coverage(config=config)
        self.stdout.write("")
        self.stdout.write(
            f"Coverage: {report.overall_percent:.1f}% "
            f"({report.total_indexed} indexed / {report.total_db} in DB)"
        )
        self.stdout.write(f"{'Model':<40} {'DB':>8} {'Indexed':>10} {'%':>8}")
        for row in report.rows:
            self.stdout.write(
                f"{row.model_label:<40} {row.db_count:>8} {row.indexed_count:>10} "
                f"{row.percent:>7.1f}"
            )
