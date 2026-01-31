from __future__ import annotations

from typing import Any, Iterable, List, Set, Tuple

from django.db import models

from .settings import ModelConfig


class GraphResolver:
    def resolve(self, instance: models.Model, depth: int = 2) -> dict:
        visited: Set[Tuple[str, Any]] = set()
        return self._resolve_instance(instance, depth, visited)

    def build_searchable_text(self, instance: models.Model, config: ModelConfig) -> str:
        parts: List[str] = []
        for field_path in config.fields:
            value = self._resolve_path(instance, field_path)
            for text in self._normalize_to_texts(value):
                parts.extend(self._apply_weight(text, config.weight_fields.get(field_path)))

        if config.follow_relations and config.relation_depth > 0:
            related_texts = self._collect_related_text(instance, config.relation_depth)
            parts.extend(related_texts)

        return " ".join([p for p in parts if p])

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

    def _collect_related_text(self, instance: models.Model, depth: int) -> List[str]:
        visited: Set[Tuple[str, Any]] = set()
        texts: List[str] = []
        self._collect_related_text_inner(instance, depth, visited, texts)
        return texts

    def _collect_related_text_inner(
        self,
        instance: models.Model,
        depth: int,
        visited: Set[Tuple[str, Any]],
        texts: List[str],
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
                for item in related_value.all():
                    self._collect_related_text_inner(item, depth - 1, visited, texts)
            else:
                self._collect_related_text_inner(related_value, depth - 1, visited, texts)

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

    def _apply_weight(self, text: str, weight: float | None) -> List[str]:
        if not weight or weight <= 1:
            return [text]
        repeat = int(round(weight))
        return [text] * max(1, repeat)

