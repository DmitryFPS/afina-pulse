from __future__ import annotations

import logging
from functools import lru_cache

import httpx
import numpy as np

from afina_watch.config import LlmCfg

log = logging.getLogger(__name__)

_st_model = None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def embed_ollama(texts: list[str], cfg: LlmCfg) -> list[np.ndarray] | None:
    vecs: list[np.ndarray] = []
    try:
        with httpx.Client(timeout=60) as client:
            for t in texts:
                r = client.post(
                    f"{cfg.host.rstrip('/')}/api/embed",
                    json={"model": cfg.embed_model, "input": t},
                )
                if r.status_code >= 400:
                    # старый endpoint
                    r = client.post(
                        f"{cfg.host.rstrip('/')}/api/embeddings",
                        json={"model": cfg.embed_model, "prompt": t},
                    )
                r.raise_for_status()
                body = r.json()
                if "embeddings" in body:
                    vec = body["embeddings"][0]
                else:
                    vec = body.get("embedding")
                if not vec:
                    return None
                vecs.append(np.asarray(vec, dtype=np.float32))
        return vecs
    except Exception:
        log.debug("ollama embed failed", exc_info=True)
        return None


def embed_st(texts: list[str], model_name: str) -> list[np.ndarray]:
    global _st_model
    from sentence_transformers import SentenceTransformer

    if _st_model is None:
        log.info("loading sentence-transformers %s", model_name)
        _st_model = SentenceTransformer(model_name)
    arr = _st_model.encode(texts, normalize_embeddings=True)
    return [np.asarray(x, dtype=np.float32) for x in arr]


@lru_cache(maxsize=512)
def embed_one_tuple(text: str, provider_key: str) -> tuple[float, ...]:
    raise RuntimeError("use Embedder")


class Embedder:
    def __init__(self, cfg: LlmCfg):
        self.cfg = cfg
        self._phrase_cache: dict[str, np.ndarray] = {}

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        vecs = embed_ollama(texts, self.cfg)
        if vecs is not None:
            return vecs
        return embed_st(texts, self.cfg.embed_fallback)

    def similarity(self, text: str, phrase: str) -> float:
        if phrase not in self._phrase_cache:
            self._phrase_cache[phrase] = self.embed([phrase])[0]
        tv = self.embed([text])[0]
        return _cosine(tv, self._phrase_cache[phrase])

    def best_phrase_score(self, text: str, phrases: list[str]) -> float | None:
        if not text.strip() or not phrases:
            return None
        scores = [self.similarity(text, p) for p in phrases]
        return max(scores) if scores else None
