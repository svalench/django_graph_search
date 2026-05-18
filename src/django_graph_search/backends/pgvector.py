"""
pgvector backend для django-graph-search (опциональная extra ``[pgvector]``).

Требуется PostgreSQL с расширением ``vector``.

Конфигурация ``VECTOR_STORE``::

    {
        "BACKEND": "django_graph_search.backends.pgvector.PgvectorBackend",
        "OPTIONS": {
            "table_name": "django_graph_search_vector",
            "dimension": 384,
            "distance": "cosine",
            "using": "default",
            "hnsw_m": 16,
            "hnsw_ef_construction": 64,
        },
    }
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from django.db import connections

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult

log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise BackendError(f"Invalid SQL identifier for table_name: {name!r}")
    return name


class PgvectorBackend(BaseVectorStore):
    """Векторное хранилище на PostgreSQL + pgvector."""

    def __init__(self, **options: Any) -> None:
        self.table_name = _quote_ident(options.get("table_name", "django_graph_search_vector"))
        self.dimension = int(options.get("dimension", 384))
        self.distance = str(options.get("distance", "cosine")).lower()
        self.using = options.get("using", "default")
        self.hnsw_m = int(options.get("hnsw_m", 16))
        self.hnsw_ef_construction = int(options.get("hnsw_ef_construction", 64))
        self._table_initialized = False

    def _vector_literal(self, vector: List[float]) -> str:
        return "[" + ",".join(str(float(v)) for v in vector) + "]"

    def _ensure_table(self) -> None:
        if self._table_initialized:
            return
        conn = connections[self.using]
        if conn.vendor != "postgresql":
            raise BackendError("PgvectorBackend requires PostgreSQL.")
        tbl = self.table_name
        dim = self.dimension
        create_sql = (
            f"CREATE TABLE IF NOT EXISTS {tbl} ("
            f'id TEXT PRIMARY KEY, "metadata" JSONB NOT NULL DEFAULT \'{{}}\'::jsonb, '
            f"embedding vector({dim}) NOT NULL);"
        )
        with conn.cursor() as cursor:
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Could not create pgvector extension (may lack privileges): %s",
                    exc,
                )
            cursor.execute(create_sql)
            if self.distance == "cosine":
                index_sql = (
                    f"CREATE INDEX IF NOT EXISTS {tbl}_embedding_cosine_idx ON {tbl} "
                    f"USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = {self.hnsw_m}, ef_construction = {self.hnsw_ef_construction});"
                )
            elif self.distance == "l2":
                index_sql = (
                    f"CREATE INDEX IF NOT EXISTS {tbl}_embedding_l2_idx ON {tbl} "
                    f"USING hnsw (embedding vector_l2_ops) "
                    f"WITH (m = {self.hnsw_m}, ef_construction = {self.hnsw_ef_construction});"
                )
            elif self.distance in {"inner_product", "ip"}:
                index_sql = (
                    f"CREATE INDEX IF NOT EXISTS {tbl}_embedding_ip_idx ON {tbl} "
                    f"USING hnsw (embedding vector_ip_ops) "
                    f"WITH (m = {self.hnsw_m}, ef_construction = {self.hnsw_ef_construction});"
                )
            else:
                raise BackendError("distance must be 'cosine', 'l2', or 'inner_product'.")
            try:
                cursor.execute(index_sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not create HNSW index: %s", exc)
        self._table_initialized = True

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        self._ensure_table()
        tbl = self.table_name
        upsert = (
            f"INSERT INTO {tbl} (id, metadata, embedding) VALUES (%s, %s::jsonb, %s::vector) "
            f"ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata, "
            f"embedding = EXCLUDED.embedding;"
        )
        conn = connections[self.using]
        rows = [
            (doc.id, json.dumps(doc.metadata or {}), self._vector_literal(doc.embedding))
            for doc in docs
        ]
        with conn.cursor() as cursor:
            cursor.executemany(upsert, rows)

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        self._ensure_table()
        tbl = self.table_name
        vec = self._vector_literal(query_vector)
        params: List[Any] = []
        where_sql = ""
        if filters:
            params.append(json.dumps(filters))
            where_sql = "WHERE metadata @> %s::jsonb"
        order_op = "<=>"
        if self.distance == "l2":
            order_op = "<->"
        elif self.distance in {"inner_product", "ip"}:
            order_op = "<#>"
        if self.distance == "cosine":
            score_expr = f"(1 - (embedding {order_op} %s::vector))"
        elif self.distance == "l2":
            score_expr = f"(1 / (1 + (embedding {order_op} %s::vector)))"
        else:
            score_expr = f"(-(embedding {order_op} %s::vector))"

        sql = (
            f"SELECT id, metadata, {score_expr} AS score FROM {tbl} "
            f"{where_sql} ORDER BY embedding {order_op} %s::vector LIMIT %s"
        )
        params.extend([vec, vec, limit])
        conn = connections[self.using]
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        results: List[SearchResult] = []
        for row in rows:
            doc_id, metadata_raw, score = row
            if isinstance(metadata_raw, dict):
                meta = metadata_raw
            else:
                meta = json.loads(metadata_raw or "{}")
            s = max(0.0, min(1.0, float(score)))
            results.append(SearchResult(id=str(doc_id), score=s, metadata=meta))
        return results

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = list(doc_ids)
        if not ids:
            return
        self._ensure_table()
        tbl = self.table_name
        conn = connections[self.using]
        with conn.cursor() as cursor:
            cursor.execute(f"DELETE FROM {tbl} WHERE id = ANY(%s)", [ids])

    def clear_collection(self) -> None:
        self._ensure_table()
        tbl = self.table_name
        conn = connections[self.using]
        with conn.cursor() as cursor:
            cursor.execute(f"DELETE FROM {tbl};")

    def count_documents(self, filters: Optional[Dict[str, Any]] = None) -> int:
        self._ensure_table()
        tbl = self.table_name
        conn = connections[self.using]
        if filters:
            sql = f"SELECT COUNT(*) FROM {tbl} WHERE metadata @> %s::jsonb"
            params: List[Any] = [json.dumps(filters)]
        else:
            sql = f"SELECT COUNT(*) FROM {tbl}"
            params = []
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return int(row[0]) if row else 0
