from __future__ import annotations

import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


def _load_model():
    from sentence_transformers import SentenceTransformer  # heavy import

    name = settings.EMBEDDING_MODEL_NAME
    logger.info("Loading embedding model: %s", name)
    return SentenceTransformer(name)


def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()
    return _model


def embed(text: str) -> list[float]:
    """Return the embedding vector for `text` as a plain list of floats."""
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    vector = get_model().encode(text, normalize_embeddings=False)
    return vector.tolist()
