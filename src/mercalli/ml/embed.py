"""Sentence embeddings for incident text, with a cache keyed by the text itself.

The model is all-MiniLM-L6-v2 run through ONNX Runtime (via fastembed), so it needs no GPU and no
PyTorch, and gives the same vectors on a laptop and on a CI runner. Vectors are L2 normalized, so a
dot product is a cosine similarity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384


def _key(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=12).hexdigest()


class Embedder:
    def __init__(self, cache_path: Path | None = None, *, model_dir: Path | None = None) -> None:
        self.cache_path = cache_path
        self.model_dir = model_dir
        self._model = None
        self._cache: dict[str, np.ndarray] = {}
        if cache_path and cache_path.exists():
            data = np.load(cache_path, allow_pickle=False)
            for k, v in zip(data["keys"], data["vecs"], strict=True):
                self._cache[str(k)] = v

    def _load(self):  # type: ignore[no-untyped-def]
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs = {"cache_dir": str(self.model_dir)} if self.model_dir else {}
            self._model = TextEmbedding(MODEL_NAME, **kwargs)
        return self._model

    def embed(self, texts: Sequence[str], batch_size: int = 256) -> np.ndarray:
        """Embed texts, reusing cached vectors. Returns float32 array of shape (len(texts), 384)."""
        clean = [(t or "").strip()[:2000] for t in texts]
        keys = [_key(t) for t in clean]
        missing = list(dict.fromkeys(k for k in keys if k not in self._cache))
        if missing:
            by_key = dict(zip(keys, clean, strict=True))
            model = self._load()
            todo = [by_key[k] or "(empty)" for k in missing]
            for k, v in zip(missing, model.embed(todo, batch_size=batch_size), strict=True):
                self._cache[k] = np.asarray(v, dtype=np.float16)
        if not keys:
            return np.zeros((0, DIM), dtype=np.float32)
        return np.stack([self._cache[k] for k in keys]).astype(np.float32)

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        keys = np.array(list(self._cache.keys()))
        vecs = np.stack(list(self._cache.values())) if self._cache else np.zeros((0, DIM), np.float16)
        tmp = self.cache_path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, keys=keys, vecs=vecs)
        tmp.replace(self.cache_path)
