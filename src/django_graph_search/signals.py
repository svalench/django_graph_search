from __future__ import annotations

import logging
import threading
from typing import Set

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .indexer import get_indexer
from .settings import GraphSearchConfig, get_settings

log = logging.getLogger(__name__)

_LOCAL_EMBEDDING_BACKEND_MARKER = "SentenceTransformerBackend"


def _get_model_config(model_label: str):
    config = get_settings()
    for model_cfg in config.models:
        if model_cfg.model == model_label:
            return model_cfg
    return None


def _skip_field_names(config: GraphSearchConfig, model_cfg) -> Set[str]:
    skip = set(config.auto_index_skip_update_fields)
    if model_cfg.skip_update_fields:
        skip.update(model_cfg.skip_update_fields)
    return skip


def _should_skip_auto_index_on_update_fields(model_cfg, config, **kwargs) -> bool:
    """Не индексировать save(update_fields=...), если затронуты только «шумные» поля."""
    update_fields = kwargs.get("update_fields")
    if not update_fields:
        return False
    skip = _skip_field_names(config, model_cfg)
    touched = {str(f) for f in update_fields}
    return bool(touched) and touched <= skip


def _only_skip_fields_changed_on_instance(instance, skip: Set[str]) -> bool:
    """
    Полный save() без update_fields: пропуск, если в БД отличаются только поля из skip.

    Типичный login: user.last_login обновлён, остальное без изменений.
    """
    if not skip or instance.pk is None:
        return False
    model = instance.__class__
    old = model.objects.filter(pk=instance.pk).first()
    if old is None:
        return False
    for field in model._meta.concrete_fields:
        name = field.name
        if name in skip or name in ("id", "pk"):
            continue
        if getattr(instance, name) != getattr(old, name):
            return False
    return True


def _should_skip_auth_user_noise(instance, model_cfg, config, **kwargs) -> bool:
    if kwargs.get("update_fields") is not None:
        return False
    try:
        user_model = get_user_model()
    except Exception:  # pragma: no cover
        return False
    if not isinstance(instance, user_model):
        return False
    if instance._meta.label != model_cfg.model:
        return False
    return _only_skip_fields_changed_on_instance(instance, _skip_field_names(config, model_cfg))


def _uses_local_sentence_transformer(config: GraphSearchConfig) -> bool:
    profile = config.embeddings[config.default_embedding]
    return _LOCAL_EMBEDDING_BACKEND_MARKER in profile.backend


def _should_index_in_background(config: GraphSearchConfig) -> bool:
    if config.async_indexing.enabled:
        return True
    return config.auto_index_non_blocking and _uses_local_sentence_transformer(config)


def _indexing_backend_name(config: GraphSearchConfig) -> str:
    if config.async_indexing.enabled:
        return config.async_indexing.backend.lower()
    return "thread"


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


def _run_index_in_thread(app_label: str, model_name: str, pk) -> None:
    from .tasks import index_instance_task_fn

    thread = threading.Thread(
        target=index_instance_task_fn,
        args=[app_label, model_name, pk],
        daemon=True,
        name=f"dgs-index-{model_name}-{pk}",
    )
    thread.start()


def _dispatch_index(instance) -> None:
    """
    Индексация: синхронно или асинхронно (ASYNC_INDEXING / AUTO_INDEX_NON_BLOCKING).
    """
    cfg = get_settings()
    if not _should_index_in_background(cfg):
        _sync_index(instance)
        return

    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    pk = instance.pk
    backend = _indexing_backend_name(cfg)

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
        _run_index_in_thread(app_label, model_name, pk)
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
    model_cfg = _get_model_config(instance._meta.label)
    if model_cfg is None:
        return
    if _should_skip_auto_index_on_update_fields(model_cfg, config, **kwargs):
        return
    if _should_skip_auth_user_noise(instance, model_cfg, config, **kwargs):
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
