"""
Опциональные Celery-задачи для асинхронной индексации.

Используются только при ``ASYNC_INDEXING.ENABLED = True`` и выбранном BACKEND.
Модуль импортируется без Celery: при отсутствии пакета выполнение остаётся синхронным
с предупреждением в логах (без обязательной зависимости).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _model_config_for_label(model_label: str):
    from .settings import get_settings

    for model_cfg in get_settings().models:
        if model_cfg.model == model_label:
            return model_cfg
    return None


def index_instance_task_fn(app_label: str, model_name: str, pk: Any) -> None:
    """
    Ядро: загрузить инстанс по (app_label, model_name, pk) и вызвать индексатор.

    Безопасно вызывать напрямую из тестов без Celery.
    """
    from django.apps import apps

    from .indexer import get_indexer

    model_cls = apps.get_model(app_label, model_name)
    model_cfg = _model_config_for_label(model_cls._meta.label)
    if model_cfg is None:
        log.debug("Skip async index: no model config for %s", model_cls._meta.label)
        return
    try:
        instance = model_cls.objects.get(pk=pk)
    except model_cls.DoesNotExist:
        log.warning("Async index skip: %s.%s pk=%s not found", app_label, model_name, pk)
        return
    indexer = get_indexer()
    indexer.index_instance(instance, model_cfg)
    log.debug("Async index completed: %s.%s pk=%s", app_label, model_name, pk)


def delete_instance_task_fn(app_label: str, model_name: str, pk: Any) -> None:
    """Удалить документ вектор-стора по метке модели и pk (без загрузки ORM)."""
    from django.apps import apps

    from .indexer import get_indexer

    model_cls = apps.get_model(app_label, model_name)
    label = model_cls._meta.label
    get_indexer().delete_instance(label, pk)
    log.debug("Async delete index completed: %s pk=%s", label, pk)


try:
    from celery import shared_task
except ImportError:
    shared_task = None  # type: ignore[assignment]


if shared_task is not None:

    @shared_task(  # type: ignore[misc]
        name="django_graph_search.tasks.index_instance_task",
        bind=True,
        max_retries=3,
        default_retry_delay=10,
        ignore_result=True,
    )
    def index_instance_task(self, app_label: str, model_name: str, pk: Any) -> None:
        """Celery-обёртка для асинхронной индексации."""
        try:
            index_instance_task_fn(app_label, model_name, pk)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Async index failed for %s.%s pk=%s: %s",
                app_label,
                model_name,
                pk,
                exc,
                exc_info=True,
            )
            raise self.retry(exc=exc) from exc

    @shared_task(  # type: ignore[misc]
        name="django_graph_search.tasks.delete_instance_task",
        bind=True,
        max_retries=3,
        default_retry_delay=10,
        ignore_result=True,
    )
    def delete_instance_task(self, app_label: str, model_name: str, pk: Any) -> None:
        try:
            delete_instance_task_fn(app_label, model_name, pk)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Async delete index failed for %s.%s pk=%s: %s",
                app_label,
                model_name,
                pk,
                exc,
                exc_info=True,
            )
            raise self.retry(exc=exc) from exc

else:

    def index_instance_task(app_label: str, model_name: str, pk: Any) -> None:  # type: ignore[misc]
        log.warning(
            "Celery not installed. Falling back to sync indexing for %s.%s pk=%s",
            app_label,
            model_name,
            pk,
        )
        index_instance_task_fn(app_label, model_name, pk)

    def delete_instance_task(app_label: str, model_name: str, pk: Any) -> None:  # type: ignore[misc]
        log.warning(
            "Celery not installed. Falling back to sync delete index for %s.%s pk=%s",
            app_label,
            model_name,
            pk,
        )
        delete_instance_task_fn(app_label, model_name, pk)
