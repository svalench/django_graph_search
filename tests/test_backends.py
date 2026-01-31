from django_graph_search.backends.base import Document, SearchResult


def test_document_dataclass():
    doc = Document(id="x", embedding=[0.1, 0.2], metadata={"a": 1}, text="hi")
    assert doc.id == "x"
    assert doc.embedding == [0.1, 0.2]


def test_search_result_dataclass():
    res = SearchResult(id="x", score=0.5, metadata={"a": 1})
    assert res.score == 0.5

