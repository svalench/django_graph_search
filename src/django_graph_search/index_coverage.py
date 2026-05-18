"""Покрытие индекса: строки в БД vs точки в векторном хранилище по metadata.model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from django.apps import apps
from django.utils.module_loading import import_string

from .settings import GraphSearchConfig, get_settings

if TYPE_CHECKING:
    from .backends.base import BaseVectorStore


@dataclass(frozen=True)
class IndexCoverageRow:
    """Одна настроенная модель: сколько строк в ORM и сколько точек с тем же model label."""

    model_label: str
    db_count: int
    indexed_count: int
    # Доля проиндексированных относительно БД (может быть >100 при «хвостах» в индексе)
    percent: float
    # Для полоски: не выше 100
    bar_percent: int


@dataclass(frozen=True)
class IndexCoverageReport:
    rows: List[IndexCoverageRow]
    total_db: int
    total_indexed: int
    overall_percent: float
    overall_bar_percent: int
    vector_store_backend: str


def get_index_coverage(
    config: Optional[GraphSearchConfig] = None,
    *,
    vector_store: Optional["BaseVectorStore"] = None,
) -> IndexCoverageReport:
    """
    Снимок на момент вызова (без автообновления).

    При db_count == 0 считаем покрытие тривиально полным (100%): индексировать нечего.
    """
    cfg = config or get_settings()
    if vector_store is None:
        backend_cls = import_string(cfg.vector_store.backend)
        vector_store = backend_cls(**cfg.vector_store.options)

    rows: List[IndexCoverageRow] = []
    total_db = 0
    total_indexed = 0

    for model_cfg in cfg.models:
        app_label, model_name = model_cfg.model.split(".", 1)
        model_cls = apps.get_model(app_label, model_name)
        label = model_cls._meta.label
        db_count = model_cls.objects.count()
        indexed_count = vector_store.count_documents({"model": label})
        total_db += db_count
        total_indexed += indexed_count

        if db_count == 0:
            percent = 100.0
        else:
            percent = 100.0 * indexed_count / db_count
        bar_percent = max(0, min(100, int(round(percent))))

        rows.append(
            IndexCoverageRow(
                model_label=label,
                db_count=db_count,
                indexed_count=indexed_count,
                percent=percent,
                bar_percent=bar_percent,
            )
        )

    if total_db == 0:
        overall_percent = 100.0
    else:
        overall_percent = 100.0 * total_indexed / total_db
    overall_bar_percent = max(0, min(100, int(round(overall_percent))))

    return IndexCoverageReport(
        rows=rows,
        total_db=total_db,
        total_indexed=total_indexed,
        overall_percent=overall_percent,
        overall_bar_percent=overall_bar_percent,
        vector_store_backend=cfg.vector_store.backend,
    )
