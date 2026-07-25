from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Set, Tuple

from django.db import models

if TYPE_CHECKING:
    from .settings import ModelConfig

log = logging.getLogger(__name__)

DEFAULT_MAX_RELATED_ITEMS = 100
DEFAULT_MAX_TEXT_LENGTH = 8000


class GraphResolver:
    def __init__(
        self,
        max_related_items: Optional[int] = None,
        max_text_length: Optional[int] = None,
    ) -> None:
        self._max_related_items = max_related_items
        self._max_text_length = max_text_length

    def _limits(self) -> Tuple[int, int]:
        """Лимиты обхода графа: из аргументов или GRAPH_SEARCH (дефолты выше)."""
        mri = self._max_related_items
        mtl = self._max_text_length
        if mri is None or mtl is None:
            try:
                from .settings import get_settings

                cfg = get_settings()
                if mri is None:
                    mri = cfg.max_related_items
                if mtl is None:
                    mtl = cfg.max_text_length
            except Exception:  # noqa: BLE001 - настройки могут быть не сконфигурированы
                if mri is None:
                    mri = DEFAULT_MAX_RELATED_ITEMS
                if mtl is None:
                    mtl = DEFAULT_MAX_TEXT_LENGTH
        return mri, mtl

    def resolve(self, instance: models.Model, depth: int = 2) -> dict:
        visited: Set[Tuple[str, Any]] = set()
        return self._resolve_instance(instance, depth, visited)

    def build_searchable_text(self, instance: models.Model, config: "ModelConfig") -> str:
        max_related, max_text_length = self._limits()
        parts: List[str] = []
        if config.fields == ["__all__"]:
            field_dict = self._collect_fields(instance)
            for name, value in field_dict.items():
                for text in self._normalize_to_texts(value):
                    parts.extend(self._apply_weight(text, config.weight_fields.get(name)))
        else:
            for field_path in config.fields:
                value = self._resolve_path(instance, field_path)
                for text in self._normalize_to_texts(value):
                    parts.extend(self._apply_weight(text, config.weight_fields.get(field_path)))

        if config.follow_relations and config.relation_depth > 0:
            related_texts = self._collect_related_text(
                instance, config.relation_depth, max_related_items=max_related
            )
            parts.extend(related_texts)

        # Ограничение длины: эмбеддинг-модель иначе молча усечёт текст сама.
        return " ".join([p for p in parts if p])[:max_text_length]

    def _resolve_instance(
        self,
        instance: models.Model,
        depth: int,
        visited: Set[Tuple[str, Any]],
    ) -> dict:
        model_label = instance._meta.label
        pk = getattr(instance, "pk", None)
        key = (model_label, pk)
        if key in visited:
            return {"model": model_label, "pk": pk, "cycle": True}

        visited.add(key)
        data = {
            "model": model_label,
            "pk": pk,
            "fields": self._collect_fields(instance),
            "relations": {},
        }

        if depth <= 0:
            return data

        for field in instance._meta.get_fields():
            if not field.is_relation:
                continue
            if field.auto_created and not field.concrete:
                related_name = field.get_accessor_name()
            else:
                related_name = field.name

            related_value = getattr(instance, related_name, None)
            if related_value is None:
                continue

            if field.many_to_many or field.one_to_many:
                related_items = list(related_value.all())
                data["relations"][related_name] = [
                    self._resolve_instance(item, depth - 1, visited) for item in related_items
                ]
            else:
                data["relations"][related_name] = self._resolve_instance(
                    related_value, depth - 1, visited
                )

        return data

    def _collect_fields(self, instance: models.Model) -> dict:
        fields = {}
        for field in instance._meta.concrete_fields:
            name = field.name
            value = getattr(instance, name, None)
            if value is None:
                continue
            fields[name] = value
        return fields

    def _collect_related_text(
        self,
        instance: models.Model,
        depth: int,
        max_related_items: Optional[int] = None,
    ) -> List[str]:
        if max_related_items is None:
            max_related_items, _ = self._limits()
        visited: Set[Tuple[str, Any]] = set()
        texts: List[str] = []
        self._collect_related_text_inner(instance, depth, visited, texts, max_related_items)
        return texts

    def _collect_related_text_inner(
        self,
        instance: models.Model,
        depth: int,
        visited: Set[Tuple[str, Any]],
        texts: List[str],
        max_related_items: int,
    ) -> None:
        if depth <= 0:
            return
        model_label = instance._meta.label
        pk = getattr(instance, "pk", None)
        key = (model_label, pk)
        if key in visited:
            return
        visited.add(key)

        for field in instance._meta.concrete_fields:
            value = getattr(instance, field.name, None)
            if value is None:
                continue
            texts.append(str(value))

        for field in instance._meta.get_fields():
            if not field.is_relation:
                continue
            if field.auto_created and not field.concrete:
                related_name = field.get_accessor_name()
            else:
                related_name = field.name

            related_value = getattr(instance, related_name, None)
            if related_value is None:
                continue

            if field.many_to_many or field.one_to_many:
                # Лимит на число связанных объектов: модель с тысячами
                # reverse-связей не должна взрывать индекс и БД.
                for item in related_value.all()[:max_related_items]:
                    self._collect_related_text_inner(
                        item, depth - 1, visited, texts, max_related_items
                    )
            else:
                self._collect_related_text_inner(
                    related_value, depth - 1, visited, texts, max_related_items
                )

    def _resolve_path(self, instance: models.Model, path: str) -> Any:
        current: Any = instance
        parts = path.split("__")
        for index, part in enumerate(parts):
            if current is None:
                return None
            if isinstance(current, models.Manager):
                current = current.all()
            if isinstance(current, models.QuerySet):
                remaining = "__".join(parts[index:])
                return [self._resolve_path(item, remaining) for item in current]
            current = getattr(current, part, None)
        return current

    def _normalize_to_texts(self, value: Any) -> Iterable[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if item is not None]
        if isinstance(value, models.Model):
            return [str(value)]
        if isinstance(value, models.Manager):
            return [str(item) for item in value.all()]
        if isinstance(value, models.QuerySet):
            return [str(item) for item in value]
        return [str(value)]

    def _apply_weight(self, text: str, weight: Optional[float]) -> List[str]:
        """Повторяет фрагмент текста согласно весу; 0 — полностью исключить из индекса."""
        if not text:
            return []
        w = weight if weight is not None else 1.0
        if w <= 0.0:
            return []
        repeat = max(1, round(w))
        return [text] * repeat

