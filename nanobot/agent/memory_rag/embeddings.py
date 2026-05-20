"""Small offline embedding helpers for memory retrieval.

This intentionally avoids network calls for the first implementation.  The
interface is narrow so a real embedding provider can replace it later.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Tokenize English words and Chinese characters with light normalization."""
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    # Character bigrams help Chinese phrases while remaining dependency-free.
    chinese = "".join(t for t in tokens if len(t) == 1 and "\u4e00" <= t <= "\u9fff")
    tokens.extend(chinese[i : i + 2] for i in range(max(0, len(chinese) - 1)))
    return [t for t in tokens if t.strip()]


class HashEmbeddingProvider:
    """Deterministic local bag-of-token embedding."""

    def __init__(self, dimensions: int = 192) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity for already-normalized or raw vectors."""
    av = list(a)
    bv = list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv, strict=True))
    an = math.sqrt(sum(x * x for x in av))
    bn = math.sqrt(sum(y * y for y in bv))
    if an == 0 or bn == 0:
        return 0.0
    return dot / (an * bn)
