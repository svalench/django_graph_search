from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional, Set, Tuple

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .indexer import get_indexer
from .settings import GraphSearchConfig, get_settings

log = logging.getLogger(__name__)

_LOCAL_EMBEDDING_BACKEND_MARKER = "SentenceTransformerBackend"

# Daemon-воркеры + очередь: THREAD_POOL_SIZE ограничивает параллелизм, но
# потоки daemon=True (как раньше) — не блокируют shutdown процесса.
_pool_lock = threading.Lock()
_index_pool: Optional["_DaemonWorkerPool"] = None
_index_pool_size: int = 0


class _DaemonWorkerPool:
    """Ограниченный пул daemon-потоков с очередью задач."""

    def __init__(self, size: int) -> None:
        self._size = max(1, int(size or 1))
        self._queue: queue.Queue[Optional[Tuple[Callable, tuple]]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        for idx in range(self._size):
            worker = threading.Thread(
                target=self._loop,
                name=f"dgs-index-{idx}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def submit(self, fn: Callable, *args) -> None:
        self._queue.put((fn, args))

    def shutdown(self, wait: bool = False) -> None:
        # Сигнал остановки каждому воркеру; wait=False — не блокируем restart пула.
        for _ in self._workers:
            self._queue.put(None)
        if wait:
            for worker in self._workers:
                worker.join()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - фоновая индексация не должна ронять воркер
                log.exception("Background index/delete worker failed")


def _get_index_pool(size: int) -> _DaemonWorkerPool:
    global _index_pool, _index_pool_size  # pylint: disable=global-statement
    size = max(1, int(size or 1))
    with _pool_lock:
        if _index_pool is None or _index_pool_size != size:
            old = _index_pool
            _index_pool = _DaemonWorkerPool(size)
            _index_pool_size = size
            if old is not None:
                old.shutdown(wait=False)
        return _index_pool


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


def _only_skip_fields_changed(instance, old, skip: Set[str]) -> bool:
    """
    Полный save() без update_fields: пропуск, если относительно снимка ДО save
    отличаются только поля из skip (типичный login: изменён last_login).

    ``old`` — снимок значений, сделанный в pre_save; сравнивать post_save
    инстанс с БД нельзя: внутри транзакции строка уже обновлена.
    """
    if not skip or old is None:
        return False
    model = instance.__class__
    for field in model._meta.concrete_fields:
        name = field.name
        if name in skip or name in ("id", "pk"):
            continue
        if getattr(instance, name, None) != old.get(name):
            return False
    return True


def _should_skip_auth_user_noise(instance, model_cfg, config, **kwargs) -> bool:
    if kwargs.get("update_fields") is not None:
        return False
    old_snapshot = getattr(instance, "_dgs_pre_save_snapshot", None)
    if old_snapshot is None:
        return False
    return _only_skip_fields_changed(instance, old_snapshot, _skip_field_names(config, model_cfg))


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


def _sync_delete(model_label: str, pk) -> None:
    if pk is None:
        log.warning("Skip delete from index: %s pk is None", model_label)
        return
    if _get_model_config(model_label) is None:
        return
    indexer = get_indexer()
    indexer.delete_instance(model_label, pk)


def _run_index_in_pool(app_label: str, model_name: str, pk, pool_size: int) -> None:
    from .tasks import index_instance_task_fn

    pool = _get_index_pool(pool_size)
    pool.submit(index_instance_task_fn, app_label, model_name, pk)


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
        _run_index_in_pool(app_label, model_name, pk, cfg.async_indexing.thread_pool_size)
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


def _dispatch_delete(model_label: str, app_label: str, model_name: str, pk) -> None:
    """
    Удаление из индекса: синхронно или через тот же ASYNC_INDEXING.

    ``pk`` передаётся явно: к моменту on_commit Django уже обнуляет
    ``instance.pk``, поэтому closure на instance здесь небезопасна.
    """
    cfg = get_settings()
    if not cfg.async_indexing.enabled:
        _sync_delete(model_label, pk)
        return

    backend = cfg.async_indexing.backend.lower()

    if backend == "celery":
        task = import_string(cfg.async_indexing.celery_delete_task_path)
        if hasattr(task, "apply_async"):
            task.apply_async(
                args=[app_label, model_name, pk],
                queue=cfg.async_indexing.celery_queue,
            )
        else:
            _sync_delete(model_label, pk)
    elif backend == "thread":
        from .tasks import delete_instance_task_fn

        pool = _get_index_pool(cfg.async_indexing.thread_pool_size)
        pool.submit(delete_instance_task_fn, app_label, model_name, pk)
    elif backend == "django_q":
        try:
            from django_q.tasks import async_task
        except ImportError:
            log.warning("django-q not installed, falling back to sync delete index")
            _sync_delete(model_label, pk)
            return
        async_task(
            "django_graph_search.tasks.delete_instance_task_fn",
            app_label,
            model_name,
            pk,
            q_options={"group": "graph_search"},
        )
    else:
        _sync_delete(model_label, pk)


@receiver(pre_save)
def capture_pre_save_snapshot(sender, instance, **kwargs):
    """
    Снимок значений полей ДО save для отсечения «шумных» полных save().

    Снимок делается только для модели auth_user (это единственный сценарий,
    где эвристика применяется) и только когда задан непустой skip-список,
    чтобы не добавлять лишний SELECT на каждый save остальных моделей.
    """
    config = get_settings()
    if not config.auto_index:
        return
    if kwargs.get("update_fields") is not None or instance.pk is None:
        return
    try:
        user_model = get_user_model()
    except Exception:  # pragma: no cover
        return
    if not isinstance(instance, user_model):
        return
    model_cfg = _get_model_config(instance._meta.label)
    if model_cfg is None or not _skip_field_names(config, model_cfg):
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    if old is None:
        return
    instance._dgs_pre_save_snapshot = {
        field.name: getattr(old, field.name, None)
        for field in sender._meta.concrete_fields
    }


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
    # Индексация только после коммита: фоновая задача перечитывает объект из
    # БД, и до commit он может быть невидим (или транзакция откатится).
    transaction.on_commit(lambda: _dispatch_index(instance))


@receiver(post_delete)
def on_model_delete(sender, instance, **kwargs):
    config = get_settings()
    if not config.auto_index:
        return
    model_label = instance._meta.label
    if _get_model_config(model_label) is None:
        return
    # pk ещё доступен в post_delete, но Django обнуляет его до возврата из
    # delete() — к on_commit instance.pk уже None. Захватываем значения сейчас.
    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    pk = instance.pk
    transaction.on_commit(
        lambda ml=model_label, al=app_label, mn=model_name, p=pk: _dispatch_delete(
            ml, al, mn, p
        )
    )
