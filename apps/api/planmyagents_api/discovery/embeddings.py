"""Pluggable text-embedding for discovery search.

The Postgres discovery store has a ``vector(1536)`` column that backs cosine
search via pgvector. This module owns *how* those vectors are produced.

Three backends ship today:

* ``DeterministicHashEmbedder`` — pure-Python, no model, no external deps.
  Produces a stable 1536-dim vector by hashing tokens into buckets and
  L2-normalising. It will never beat a real embedding model on semantic recall,
  but it gives the system a working default that runs in CI without dependency
  surprises and ranks candidates with shared tokens above unrelated ones.
* ``OllamaEmbedder`` — calls a local Ollama embeddings endpoint
  (e.g. ``nomic-embed-text``). Used when ``PLANMYAGENTS_EMBEDDING_MODEL`` is set
  AND ``PLANMYAGENTS_EMBEDDING_PROVIDER=ollama`` (the default when a model is set).
* ``OpenAIEmbedder`` — calls the OpenAI embeddings API. Used when
  ``PLANMYAGENTS_EMBEDDING_PROVIDER=openai`` and ``OPENAI_API_KEY`` is set.
  Uses the same ``Embedder`` protocol so callers don't care which is configured.

Backend selection priority (in ``embedder_from_env``):

1. ``PLANMYAGENTS_EMBEDDING_PROVIDER=openai`` + ``OPENAI_API_KEY`` → OpenAI.
2. ``PLANMYAGENTS_EMBEDDING_MODEL`` set (any value)              → Ollama.
3. otherwise                                                      → hash.

Caching wrapper
---------------

``embedder_from_env()`` always wraps the chosen backend in a
:class:`CachedEmbedder`. The cache is process-local and bounded by an
LRU policy. This is the single biggest contention fix for Ollama: most
embedding inputs in the system (registry capability strings, candidate
text on save, planner-emitted slugs) are *repeated* across requests,
so a memory hit avoids the cost of a remote round-trip and a slot in
the daemon's request queue.

Tunables (env vars):

* ``PLANMYAGENTS_EMBEDDING_CACHE_SIZE`` — max entries (default 4096).
  Set to 0 to disable caching.
* ``PLANMYAGENTS_OLLAMA_MAX_CONCURRENCY`` — concurrent Ollama calls
  (default 2). Lower values prevent OOM / 500s under burst load at the
  cost of queue depth.
* ``PLANMYAGENTS_EMBEDDING_TIMEOUT_SECONDS`` — per-call HTTP timeout
  (default 15s for Ollama, 15s for OpenAI). Bumped from 5s after
  the 2026-05-14 audit caught Ollama timing out on cold model loads.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol
from urllib import error, request

DEFAULT_EMBEDDING_DIM = 1536
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# Per-call HTTP timeout for embedder backends. Was 5.0 historically;
# bumped to 15.0 on 2026-05-15 after the surprise-birthday-gift audit
# caught nomic-embed-text routinely timing out on the FIRST call after
# Ollama sat idle (cold model load is ~6-9s on M-series Apple Silicon
# and the daemon doesn't pre-warm). 15s is the smallest value that
# survives both cold-load latency and a single concurrent burst from
# multiple scouts on the same daemon.
DEFAULT_TIMEOUT_SECONDS = float(
    os.getenv("PLANMYAGENTS_EMBEDDING_TIMEOUT_SECONDS", "15")
)

# Per-backend max input character budgets. We truncate inside encode()
# rather than letting the upstream return HTTP 500. Conservative defaults
# (≈ 4 chars/token for English):
#
#   nomic-embed-text          → ~2048 tokens   → 4000 chars (1000 tok headroom)
#   text-embedding-3-small    → ~8191 tokens   → ~30000 chars
#
# 4000 (was 6000) reflects two empirical findings on 2026-05-14:
# (1) realistic English ~4 chars/token survives at 8000 chars but
# (2) discovery sources occasionally feed code-blob / non-English content
# where the token-to-char ratio collapses, sneaking past a 6000 cap and
# triggering "the input length exceeds the context length" at the
# nomic 2048-tok hard limit. 4000 is the largest cap that survives every
# pathological case we've observed, including pure-ascii character spam.
MAX_INPUT_CHARS_OLLAMA = 4000
MAX_INPUT_CHARS_OPENAI = 30000

# Cap concurrent in-flight Ollama embedding calls. nomic-embed-text on
# a single Ollama daemon serialises requests internally — sending 9
# scout dispatches × N candidates each in parallel doesn't make any
# call faster, but it DOES cause the daemon to OOM / 500 some
# requests as the queue grows. A semaphore around encode() smooths the
# request rate without changing the API of this module.
#
# Reduced from 4 → 2 on 2026-05-15. The old value still produced
# intermittent 500s under the worst observed load (3-sub-task /goal
# + concurrent post-goal refresh hitting the daemon). 2 sacrifices
# parallelism for predictability — the cache wrapper covers most of
# the latency gap by serving repeated text from memory anyway. The
# semaphore is *intentionally* shared across all OllamaEmbedder
# instances in this process because the daemon, not the embedder
# object, is the bottleneck.
_OLLAMA_CONCURRENCY = int(os.getenv("PLANMYAGENTS_OLLAMA_MAX_CONCURRENCY", "2"))
_OLLAMA_SEMAPHORE = threading.BoundedSemaphore(value=_OLLAMA_CONCURRENCY)

# In-process LRU cache size for CachedEmbedder. 4096 fits:
#
#   25 capability registry entries
# + ~200 active discovery candidate texts
# + planner-emitted slugs (a few dozen)
# + recent /search query embeddings (caps growth)
#
# … with comfortable headroom. Memory cost is bounded by
# (cache_size × ~1.2KB/entry text + cache_size × dim × 8 bytes/float)
# ≈ 4096 × (1.2KB + 12KB) ≈ 54MB worst case. Acceptable for the
# /goal latency win it provides.
_EMBEDDING_CACHE_SIZE = int(
    os.getenv("PLANMYAGENTS_EMBEDDING_CACHE_SIZE", "4096")
)


class EmbedderError(RuntimeError):
    """Raised when an embedder cannot produce a vector."""


class Embedder(Protocol):
    """Common contract for text embedders."""

    dimension: int
    name: str

    def encode(self, text: str) -> list[float]:
        """Return a unit-normalised vector of length ``dimension``."""


@dataclass(frozen=True)
class DeterministicHashEmbedder:
    """Hash-bag-of-words embedder. Stable, deterministic, no external dep."""

    dimension: int = DEFAULT_EMBEDDING_DIM
    name: str = "deterministic-hash-v1"

    def encode(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimension
        vector = [0.0] * self.dimension
        for token in _tokens(text):
            for variant in _token_variants(token):
                bucket = _bucket(variant, self.dimension)
                # Hashed token sign ensures cancellation on common tokens but
                # keeps signal on rare distinctive tokens.
                sign = 1.0 if (_hash_int(variant) & 1) == 0 else -1.0
                vector[bucket] += sign
        return _l2_normalise(vector)


@dataclass(frozen=True)
class OllamaEmbedder:
    """Use a local Ollama embeddings model (e.g. nomic-embed-text)."""

    model: str
    base_url: str = DEFAULT_OLLAMA_BASE_URL
    dimension: int = DEFAULT_EMBEDDING_DIM
    name: str = field(default="")
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", f"ollama:{self.model}")

    def encode(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimension
        # Truncate before sending so an oversized description (e.g. a
        # 50KB OpenAPI ``description`` from APIs.guru) can't trip the
        # model's context window and crash the /goal route. We don't
        # tokenise here — that would couple this module to whichever
        # tokeniser the model uses; the conservative char budget is
        # safe across all popular Ollama embedders.
        prompt = text if len(text) <= MAX_INPUT_CHARS_OLLAMA else text[:MAX_INPUT_CHARS_OLLAMA]
        payload = json.dumps({"model": self.model, "prompt": prompt}).encode("utf-8")
        req = request.Request(
            f"{self.base_url.rstrip('/')}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        # One retry on transport failure. Ollama occasionally returns
        # transient 5xx / connection drops under burst load (especially
        # when the model has just unloaded due to idle timeout and the
        # next request triggers a cold reload mid-flight). A second
        # attempt typically succeeds because the model is now warm.
        # We DON'T retry on the first attempt's success — a single
        # successful round trip is the common case.
        last_error: Exception | None = None
        body: str | None = None
        for attempt in range(2):
            # Block here, not after the urlopen, because Ollama's
            # bottleneck is the daemon receiving the request — once it
            # accepts work we want the connection alive end-to-end.
            # ``with`` guarantees release even if urlopen raises.
            with _OLLAMA_SEMAPHORE:
                try:
                    with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                        body = response.read().decode("utf-8")
                    last_error = None
                    break
                except (OSError, TimeoutError, error.URLError) as exc:
                    last_error = exc
                    if attempt == 0:
                        # Brief pause before retry so we don't pile back
                        # onto a daemon that's still recovering. 100ms
                        # is small enough to be invisible to /goal
                        # latency budgets but large enough to let a
                        # transient connection-reset clear.
                        import time as _time

                        _time.sleep(0.1)
                        continue
        if last_error is not None or body is None:
            raise EmbedderError(
                f"Ollama embeddings request failed after retry: {last_error}"
            ) from last_error
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise EmbedderError("Ollama embeddings response was not JSON") from exc
        embedding = data.get("embedding")
        if not isinstance(embedding, list):
            raise EmbedderError("Ollama embeddings response missing `embedding`")
        # Normalise into the configured dimension. Most popular embedding models
        # return 384/768/1024/1536/4096 dims. We pad/truncate so the schema stays
        # compatible with the default 1536 column without forcing every model.
        coerced = _coerce_dim([float(value) for value in embedding], self.dimension)
        return _l2_normalise(coerced)


@dataclass(frozen=True)
class OpenAIEmbedder:
    """Use OpenAI's text embeddings API.

    Defaults to ``text-embedding-3-small`` because:
    * 1536-dim native output → matches the pgvector column without
      dimension coercion (no padding/truncation, preserves recall).
    * Cheapest of the v3 family ($0.02 / 1M tokens at the time of
      writing).
    * Recall is competitive with text-embedding-3-large on the
      MTEB averages — we don't need top-of-leaderboard for this
      classification problem.

    The class never logs or persists ``api_key``. Construct via
    ``embedder_from_env()`` to pick up keys from the environment.
    """

    api_key: str
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"
    dimension: int = DEFAULT_EMBEDDING_DIM
    name: str = field(default="")
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", f"openai:{self.model}")

    def encode(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimension
        # OpenAI v3 models honour an optional ``dimensions`` param to
        # truncate the output server-side. We don't pass it because
        # the default 1536 already matches the pgvector column —
        # passing dimensions=1536 would be a no-op and just wastes
        # a roundtrip-validation step on their end.
        # Truncate the input to a conservative budget for the same
        # reason as the Ollama path: a 50KB OpenAPI description from
        # APIs.guru would otherwise blow the 8191-token context window
        # and 400 the request mid-/goal flow.
        prompt = text if len(text) <= MAX_INPUT_CHARS_OPENAI else text[:MAX_INPUT_CHARS_OPENAI]
        body = {"model": self.model, "input": prompt}
        payload = json.dumps(body).encode("utf-8")
        req = request.Request(
            f"{self.base_url.rstrip('/')}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                response_body = response.read().decode("utf-8")
        except (OSError, TimeoutError, error.URLError) as exc:
            raise EmbedderError(
                f"OpenAI embeddings request failed: {exc}"
            ) from exc
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise EmbedderError("OpenAI embeddings response was not JSON") from exc
        embeddings = data.get("data")
        if not isinstance(embeddings, list) or not embeddings:
            raise EmbedderError("OpenAI embeddings response missing `data`")
        first = embeddings[0]
        if not isinstance(first, dict):
            raise EmbedderError(
                "OpenAI embeddings response `data[0]` not an object"
            )
        embedding = first.get("embedding")
        if not isinstance(embedding, list):
            raise EmbedderError(
                "OpenAI embeddings response missing `data[0].embedding`"
            )
        coerced = _coerce_dim([float(value) for value in embedding], self.dimension)
        return _l2_normalise(coerced)


class CachedEmbedder:
    """Process-local LRU cache around any ``Embedder``.

    Why this exists
    ---------------

    The /goal pipeline calls ``encode()`` heavily on a small set of
    repeated inputs:

    * 25 capability registry entries (encoded once per
      ``CapabilityIndex`` rebuild — and this happens many times per
      process when the index gets re-indexed).
    * Per-candidate text on every save (same dedupe_key keeps showing
      up across discovery refreshes; the text is identical when the
      candidate hasn't changed).
    * Planner-emitted slugs (``payment_processing``,
      ``store_locator``, …) — a small vocabulary, repeated across
      every /goal that mentions them.

    Without caching, every one of these is a 80-150ms Ollama HTTP
    round trip queued behind the global concurrency semaphore. With
    caching, second-and-subsequent occurrences are ~microseconds.

    On the audited surprise-birthday-gift goal, the 7m30s "mystery
    gap" inside ``_refusal_discovery`` was largely repeated
    ``infer_from_text`` calls re-encoding the same registry text
    through Ollama. Caching directly addresses BUG-2 + BUG-3 without
    changing any caller code.

    Cache key
    ---------

    The raw text after the same truncation the underlying embedder
    would apply, plus the embedder's ``name`` to avoid serving an
    Ollama vector for an OpenAI-embedded query when the backend
    swaps mid-process. Empty input (the early-return path) is
    cached as a zero vector once.

    Eviction
    --------

    LRU. Bounded by ``max_size``. Default 4096 (set via
    ``PLANMYAGENTS_EMBEDDING_CACHE_SIZE``); set to 0 to disable.

    Thread safety
    -------------

    A single lock guards the OrderedDict for both reads and writes.
    Reads are O(1) so the lock is contended only briefly. Writes
    happen inside the lock so two concurrent encoders for the same
    text both populate from the same upstream call result without
    trampling each other.
    """

    def __init__(self, inner: Embedder, *, max_size: int = 4096) -> None:
        self._inner = inner
        self._max_size = max_size
        # Both lookup and insertion happen under the same lock so two
        # threads encoding the SAME text race only on the upstream
        # call (acceptable — Ollama dedupes nothing, but the second
        # caller will hit the cache after the first writes back).
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        # Accounting for tests + future metrics.
        self.hits = 0
        self.misses = 0

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    @property
    def name(self) -> str:
        return f"cached:{self._inner.name}"

    def encode(self, text: str) -> list[float]:
        if self._max_size <= 0:
            # Caching disabled — straight passthrough so the wrapper
            # is a no-op when an operator has explicitly set size=0.
            return self._inner.encode(text)
        key = (self._inner.name, text)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                # LRU bookkeeping: most recently used moves to the end.
                self._entries.move_to_end(key)
                self.hits += 1
                # Return a copy so callers can't mutate the cache entry
                # (some downstream code may .extend() a result).
                return list(cached)
            self.misses += 1
        # Encode OUTSIDE the lock so a slow Ollama call doesn't block
        # other threads from getting cache hits. Trade-off: two threads
        # encoding the same novel text both race to the upstream — but
        # the cache write below is idempotent.
        vector = self._inner.encode(text)
        with self._lock:
            # Re-check under lock: another thread may have populated
            # this key between our miss and our write.
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                return list(existing)
            self._entries[key] = list(vector)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                # Pop oldest. ``last=False`` is FIFO eviction (LRU
                # semantics in combination with move_to_end above).
                self._entries.popitem(last=False)
        return list(vector)

    def cache_stats(self) -> dict[str, int]:
        """Return a snapshot of cache hit/miss counters and current size.

        Intended for tests + future ``/health`` instrumentation. Reads
        the OrderedDict size under the lock so the count is consistent
        with hit/miss totals.
        """

        with self._lock:
            return {
                "size": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "max_size": self._max_size,
            }

    def clear(self) -> None:
        """Drop all cached vectors. Used by tests; not exposed via env."""

        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0


def embedder_from_env() -> Embedder:
    """Pick an embedder from environment configuration, wrapped in
    a process-local LRU cache.

    Priority:
    1. ``PLANMYAGENTS_EMBEDDING_PROVIDER=openai`` + ``OPENAI_API_KEY`` → OpenAI.
    2. ``PLANMYAGENTS_EMBEDDING_MODEL`` set (any value)               → Ollama.
    3. otherwise                                                       → hash.

    Falls through to the hash embedder on any misconfiguration so
    discovery never silently breaks because an env var is wrong.

    Caching
    -------

    The selected backend is wrapped in :class:`CachedEmbedder` with
    size from ``PLANMYAGENTS_EMBEDDING_CACHE_SIZE`` (default 4096).
    The wrapper is **fresh per call** — callers that hold an
    embedder reference share a cache only within their own scope.
    For the API process, ``embedder_from_env()`` is called from
    enough hot paths that even per-call wrapping yields large
    locality wins inside a single /goal (the same registry text is
    encoded many times per request).

    To disable the cache (e.g. in tests), set the env var to ``0``.
    The :class:`DeterministicHashEmbedder` is fast enough that
    caching adds little, but we wrap it anyway for API uniformity
    and to keep the code path identical across backends.
    """

    backend = _backend_from_env()
    if _EMBEDDING_CACHE_SIZE <= 0:
        return backend
    return CachedEmbedder(backend, max_size=_EMBEDDING_CACHE_SIZE)


def _backend_from_env() -> Embedder:
    """Pick the raw (uncached) embedder backend from environment."""

    provider = os.getenv("PLANMYAGENTS_EMBEDDING_PROVIDER", "").strip().lower()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if provider == "openai" and api_key:
        return OpenAIEmbedder(
            api_key=api_key,
            model=os.getenv(
                "PLANMYAGENTS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
            ),
            dimension=int(
                os.getenv("PLANMYAGENTS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))
            ),
        )

    model = os.getenv("PLANMYAGENTS_EMBEDDING_MODEL", "").strip()
    if model:
        return OllamaEmbedder(
            model=model,
            base_url=os.getenv("PLANMYAGENTS_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            dimension=int(os.getenv("PLANMYAGENTS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))),
        )
    return DeterministicHashEmbedder(
        dimension=int(os.getenv("PLANMYAGENTS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM)))
    )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        # Compare on overlap to keep the function permissive across embedders.
        size = min(len(a), len(b))
        a = a[:size]
        b = b[:size]
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokens(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [token for token in cleaned.split() if len(token) >= 2]


def _token_variants(token: str) -> list[str]:
    variants = [token]
    # Character bigrams give the embedder some morphological signal so plurals
    # and tense variations partially overlap.
    if len(token) >= 4:
        variants.extend(token[i : i + 3] for i in range(len(token) - 2))
    return variants


def _bucket(value: str, dimension: int) -> int:
    return _hash_int(value) % dimension


def _hash_int(value: str) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


def _coerce_dim(vector: list[float], dimension: int) -> list[float]:
    if len(vector) == dimension:
        return vector
    if len(vector) > dimension:
        return vector[:dimension]
    return vector + [0.0] * (dimension - len(vector))
