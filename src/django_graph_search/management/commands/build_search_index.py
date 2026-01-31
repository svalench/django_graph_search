from django.core.management.base import BaseCommand

from ...indexer import Indexer
from ...settings import get_settings


class Command(BaseCommand):
    help = "Build vector search index for configured models."

    def add_arguments(self, parser):
        parser.add_argument("--model", help="Model label in 'app.Model' format.")

    def handle(self, *args, **options):
        config = get_settings()
        model_label = options.get("model")
        indexer = Indexer(config=config)

        if model_label:
            model_cfgs = [cfg for cfg in config.models if cfg.model == model_label]
            if not model_cfgs:
                self.stdout.write(self.style.ERROR("Model not found in GRAPH_SEARCH settings."))
                return
        else:
            model_cfgs = config.models

        result = {}
        for cfg in model_cfgs:
            model_cls = indexer._get_model_class(cfg.model)
            count = indexer.index_queryset(model_cls.objects.all(), cfg)
            result[cfg.model] = count

        for model_name, count in result.items():
            self.stdout.write(f"{model_name}: {count}")

