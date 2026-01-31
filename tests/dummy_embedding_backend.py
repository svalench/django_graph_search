class DummyEmbeddingBackend:
    def __init__(self, model_name: str, **options):
        self.model_name = model_name
        self.options = options

    def embed(self, text: str):
        return [0.0]

    def embed_batch(self, texts):
        return [[0.0] for _ in texts]

