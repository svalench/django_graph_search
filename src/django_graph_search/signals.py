from __future__ import annotations

import logging
import threading

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .indexer import get_indexer
from .settings import get_settings

log = logging.getLogger(__name__)


def _get_model_config(model_label: str):
    config = get_settings()
    for model_cfg in config.models:
        if model_cfg.model == model_label:
            return model_cfg
    return None


def _sync_index(instance) -> None:
    model_cfg = _get_model_config(instance._meta.label)
    if model_cfg is None:
        return
    indexer = get_indexer()
    indexer.index_instance(instance, model_cfg)


def _sync_delete(instance) -> None:
    if _get_model_config(instance._meta.label) is None:
        return
    indexer = get_indexer()
    indexer.delete_instance(instance._meta.label, instance.pk)


def _dispatch_index(instance) -> None:
    """
    Индексация: синхронно или асинхронно по ASYNC_INDEXING.

    Celery — в очередь из настроек; thread — daemon; django-q — async_task;
    иначе или при ошибке — синхронный путь.
    """
    cfg = get_settings()
    if not cfg.async_indexing.enabled:
        _sync_index(instance)
        return

    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    pk = instance.pk

    backend = cfg.async_indexing.backend.lower()

    if backend == "celery":
        task = import_string(cfg.async_indexing.celery_task_path)
        if hasattr(task, "apply_async"):
            task.apply_async(
                args=[app_label, model_name, pk],
                queue=cfg.async_indexing.celery_queue,
            )
        else:
            log.warning(
                "Celery task %r has no apply_async; running sync index",
                cfg.async_indexing.celery_task_path,
            )
            _sync_index(instance)
    elif backend == "thread":
        from .tasks import index_instance_task_fn

        thread = threading.Thread(
            target=index_instance_task_fn,
            args=[app_label, model_name, pk],
            daemon=True,
            name=f"dgs-index-{model_name}-{pk}",
        )
        thread.start()
    elif backend == "django_q":
        try:
            from django_q.tasks import async_task
        except ImportError:
            log.warning("django-q not installed, falling back to sync indexing")
            _sync_index(instance)
            return
        async_task(
            "django_graph_search.tasks.index_instance_task_fn",
            app_label,
            model_name,
            pk,
            q_options={"group": "graph_search"},
        )
    else:
        log.warning("Unknown ASYNC_INDEXING.BACKEND=%r, using sync", backend)
        _sync_index(instance)


def _dispatch_delete(instance) -> None:
    """Удаление из индекса: синхронно или через тот же ASYNC_INDEXING."""
    cfg = get_settings()
    if not cfg.async_indexing.enabled:
        _sync_delete(instance)
        return

    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    pk = instance.pk
    backend = cfg.async_indexing.backend.lower()

    if backend == "celery":
        task = import_string(cfg.async_indexing.celery_delete_task_path)
        if hasattr(task, "apply_async"):
            task.apply_async(
                args=[app_label, model_name, pk],
                queue=cfg.async_indexing.celery_queue,
            )
        else:
            _sync_delete(instance)
    elif backend == "thread":
        from .tasks import delete_instance_task_fn

        threading.Thread(
            target=delete_instance_task_fn,
            args=[app_label, model_name, pk],
            daemon=True,
            name=f"dgs-del-{model_name}-{pk}",
        ).start()
    elif backend == "django_q":
        try:
            from django_q.tasks import async_task
        except ImportError:
            log.warning("django-q not installed, falling back to sync delete index")
            _sync_delete(instance)
            return
        async_task(
            "django_graph_search.tasks.delete_instance_task_fn",
            app_label,
            model_name,
            pk,
            q_options={"group": "graph_search"},
        )
    else:
        _sync_delete(instance)


@receiver(post_save)
def on_model_save(sender, instance, **kwargs):
    config = get_settings()
    if not config.auto_index:
        return
    if _get_model_config(instance._meta.label) is None:
        return
    _dispatch_index(instance)


@receiver(post_delete)
def on_model_delete(sender, instance, **kwargs):
    config = get_settings()
    if not config.auto_index:
        return
    if _get_model_config(instance._meta.label) is None:
        return
    _dispatch_delete(instance)
