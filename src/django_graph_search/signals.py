from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .indexer import Indexer
from .settings import get_settings


def _get_model_config(model_label: str):
    config = get_settings()
    for model_cfg in config.models:
        if model_cfg.model == model_label:
            return model_cfg
    return None


@receiver(post_save)
def on_model_save(sender, instance, **kwargs):
    config = get_settings()
    if not config.auto_index:
        return
    model_cfg = _get_model_config(instance._meta.label)
    if model_cfg is None:
        return
    indexer = Indexer(config=config)
    indexer.index_instance(instance, model_cfg)


@receiver(post_delete)
def on_model_delete(sender, instance, **kwargs):
    config = get_settings()
    if not config.auto_index:
        return
    model_cfg = _get_model_config(instance._meta.label)
    if model_cfg is None:
        return
    indexer = Indexer(config=config)
    indexer.delete_instance(instance._meta.label, instance.pk)

