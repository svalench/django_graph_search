"""Tests for the optional SmartIndexer pipeline (Sprint 4)."""
from __future__ import annotations

from django.test import TestCase

from django_graph_search.indexer import Indexer, get_indexer
from django_graph_search.langgraph_indexer import (
    DocumentTemplate,
    FieldSection,
    SmartIndexer,
    default_template_for,
)
from django_graph_search.settings import ModelConfig, SmartIndexingConfig

from .test_app.models import Category, Product, Tag
from .utils import make_basic_config


class _Store:
    def __init__(self):
        self.docs = []

    def add_documents(self, docs):
        self.docs.extend(list(docs))

    def search(self, query_vector, limit, filters=None):
        return []

    def delete(self, doc_ids):
        ids = set(doc_ids)
        self.docs = [d for d in self.docs if d.id not in ids]

    def clear_collection(self):
        self.docs = []


class _Embedding:
    def embed(self, text, *, is_query: bool = False):
        return [0.0]

    def embed_batch(self, texts, *, is_query: bool = False):
        return [[0.0] for _ in texts]


def _smart_config(**overrides):
    cfg = make_basic_config(delta_indexing=False, models=[
        ModelConfig(
            model="test_app.Product",
            fields=["name", "description", "category__name"],
            follow_relations=True,
            relation_depth=2,
        )
    ])
    # Replace smart_indexing with one that may be enabled.
    si = SmartIndexingConfig(
        enabled=overrides.get("enabled", True),
        indexer="django_graph_search.langgraph_indexer.SmartIndexer",
        templates=overrides.get("templates", {}) or {},
    )
    return cfg.__class__(  # GraphSearchConfig is frozen, so build a new one.
        models=cfg.models,
        vector_store=cfg.vector_store,
        embeddings=cfg.embeddings,
        default_embedding=cfg.default_embedding,
        api_url_prefix=cfg.api_url_prefix,
        admin_search_enabled=cfg.admin_search_enabled,
        auto_index=cfg.auto_index,
        default_results_limit=cfg.default_results_limit,
        delta_indexing=cfg.delta_indexing,
        cache=cfg.cache,
        langgraph=cfg.langgraph,
        conversational=cfg.conversational,
        smart_indexing=si,
        streaming=cfg.streaming,
    )


class SmartIndexerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cat = Category.objects.create(name="Phones")
        cls.product = Product.objects.create(
            name="Pixel 8",
            description="Camera-first Android phone",
            category=cat,
        )

    # --- Templates ---------------------------------------------------------

    def test_default_template_picks_title_field(self):
        cfg = ModelConfig(
            model="test_app.Product",
            fields=["name", "description"],
            follow_relations=False,
            relation_depth=1,
        )
        tpl = default_template_for(cfg)
        self.assertEqual(tpl.title_field, "name")
        self.assertEqual([s.field for s in tpl.sections], ["description"])
        # description gets multiline=True automatically.
        self.assertTrue(tpl.sections[0].multiline)

    def test_template_from_dict_validates_sections(self):
        tpl = DocumentTemplate.from_dict({
            "title_field": "name",
            "sections": [
                {"label": "Summary", "field": "description", "multiline": True},
                {"label": "Category", "field": "category__name"},
                "ignored-non-dict",
            ],
        })
        self.assertEqual(tpl.title_field, "name")
        self.assertEqual(len(tpl.sections), 2)
        self.assertEqual(tpl.sections[0].label, "Summary")
        self.assertTrue(tpl.sections[0].multiline)

    # --- Pipeline ---------------------------------------------------------

    def test_smart_indexer_writes_structured_text(self):
        cfg = _smart_config()
        store = _Store()
        indexer = SmartIndexer(
            config=cfg,
            vector_store=store,
            embedding_backend=_Embedding(),
        )
        indexer.index_instance(self.product, cfg.models[0])

        self.assertEqual(len(store.docs), 1)
        doc = store.docs[0]
        self.assertIn("Title: Pixel 8", doc.text)
        self.assertIn("Description:", doc.text)
        # Category resolved through the relation.
        self.assertIn("Phones", doc.text)
        # Legacy text is appended as a safety net.
        self.assertIn("Pixel 8", doc.text)

    def test_smart_indexer_respects_explicit_template(self):
        templates = {
            "test_app.Product": DocumentTemplate(
                title_field="name",
                sections=[FieldSection(label="Body", field="description")],
            )
        }
        cfg = _smart_config(templates=templates)
        store = _Store()
        indexer = SmartIndexer(
            config=cfg,
            vector_store=store,
            embedding_backend=_Embedding(),
            templates=templates,
        )
        indexer.index_instance(self.product, cfg.models[0])
        self.assertIn("Body: Camera-first Android phone", store.docs[0].text)

    def test_smart_indexer_normalises_dict_templates(self):
        cfg = _smart_config(templates={
            "test_app.Product": {
                "title_field": "name",
                "sections": [{"label": "About", "field": "description"}],
            }
        })
        store = _Store()
        # Don't pass templates explicitly; SmartIndexer must read them from config.
        indexer = SmartIndexer(
            config=cfg, vector_store=store, embedding_backend=_Embedding()
        )
        indexer.index_instance(self.product, cfg.models[0])
        self.assertIn("About: Camera-first Android phone", store.docs[0].text)

    def test_smart_indexer_rejects_invalid_template_type(self):
        cfg = _smart_config()
        with self.assertRaises(TypeError):
            SmartIndexer(
                config=cfg,
                vector_store=_Store(),
                embedding_backend=_Embedding(),
                templates={"test_app.Product": 42},
            )

    def test_smart_indexer_index_queryset_returns_count(self):
        # Add another product to ensure batching works.
        cat = Category.objects.create(name="Tablets")
        Product.objects.create(name="Pixel Tab", description="Big screen.", category=cat)
        cfg = _smart_config()
        store = _Store()
        indexer = SmartIndexer(
            config=cfg, vector_store=store, embedding_backend=_Embedding()
        )
        count = indexer.index_queryset(Product.objects.all(), cfg.models[0])
        self.assertEqual(count, 2)
        self.assertEqual(len(store.docs), 2)

    def test_smart_indexer_delete_removes_doc(self):
        cfg = _smart_config()
        store = _Store()
        indexer = SmartIndexer(
            config=cfg, vector_store=store, embedding_backend=_Embedding()
        )
        indexer.index_instance(self.product, cfg.models[0])
        indexer.delete_instance("test_app.Product", self.product.pk)
        self.assertEqual(store.docs, [])

    # --- Factory ---------------------------------------------------------

    def test_get_indexer_returns_classic_when_disabled(self):
        cfg = _smart_config(enabled=False)
        idx = get_indexer(
            config=cfg, vector_store=_Store(), embedding_backend=_Embedding()
        )
        self.assertIsInstance(idx, Indexer)
        self.assertNotIsInstance(idx, SmartIndexer)

    def test_get_indexer_returns_smart_when_enabled(self):
        cfg = _smart_config(enabled=True)
        idx = get_indexer(
            config=cfg, vector_store=_Store(), embedding_backend=_Embedding()
        )
        self.assertIsInstance(idx, SmartIndexer)
