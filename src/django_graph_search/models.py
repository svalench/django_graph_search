from __future__ import annotations

from django.db import models


class GraphSearch(models.Model):
    """Пункт меню админки: семантический поиск (без таблицы в БД)."""

    class Meta:
        managed = False
        verbose_name = "Поиск"
        verbose_name_plural = "Поиск"


class GraphSearchIndexStatus(models.Model):
    """Пункт меню админки: снимок покрытия индекса (без таблицы в БД)."""

    class Meta:
        managed = False
        verbose_name = "Статус индексации"
        verbose_name_plural = "Статус индексации"
