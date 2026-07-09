"""Memory chunk indexer with real embeddings and sqlite-vec vector search.

Uses ``sentence-transformers`` for local embeddings. Falls back to an
``EmbedFn`` (API-based embedding via the ai provider) when the local
model is unavailable. Uses ``sqlite-vec`` for efficient cosine-distance
vector search inside SQLite.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Any

import logging

from agent.core import EmbedFn
from claw.core.memory.types import MemoryChunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding model (lazy singleton)
# ---------------------------------------------------------------------------

_embedding_model_instance: EmbeddingModel | None = None


class EmbeddingModel:
    """Embedding model with local-first, API-fallback strategy.

    Tries ``sentence-transformers`` (local, free, fast). If unavailable,
    falls back to an ``EmbedFn`` (API-based, e.g. from OpenAI or
    Anthropic via the ai provider system).

    The underlying model is loaded lazily on first call to :meth:`encode`.
    """

    DIMENSIONS = 384
    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(
        self,
        cache_dir: Path | None = None,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._embed_fn = embed_fn
        self._model: Any = None
        self._use_api = False

    def _load(self) -> None:
        if self._model is not None or self._use_api:
            return
        try:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {"device": "cpu"}
            if self._cache_dir is not None:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                kwargs["cache_folder"] = str(self._cache_dir)
            self._model = SentenceTransformer(self.MODEL_NAME, **kwargs)
            logger.info("Loaded local embedding model %s", self.MODEL_NAME)
        except Exception as exc:
            if self._embed_fn is not None:
                logger.info("Local embedding unavailable, using API fallback: %s", exc)
                self._use_api = True
            else:
                logger.warning("Failed to load embedding model and no API fallback: %s", exc)
                raise

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Batch-encode *texts* into float vectors."""
        self._load()
        if self._use_api:
            return [await self._embed_fn(t) for t in texts]
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return [row.tolist() for row in embeddings]

    async def encode_query(self, query: str) -> list[float]:
        """Encode a single query string."""
        result = await self.encode([query])
        return result[0]


def get_embedding_model(
    cache_dir: Path | None = None,
    embed_fn: EmbedFn | None = None,
) -> EmbeddingModel:
    """Return a singleton :class:`EmbeddingModel`."""
    global _embedding_model_instance
    if _embedding_model_instance is None:
        _embedding_model_instance = EmbeddingModel(cache_dir=cache_dir, embed_fn=embed_fn)
    return _embedding_model_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _floats_to_blob(vec: list[float]) -> bytes:
    """Pack a float list into a little-endian binary blob for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_floats(blob: bytes) -> list[float]:
    """Unpack a binary blob back into a list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class MemoryIndexer:
    """Index markdown files into SQLite with real vector embeddings.

    Uses ``sqlite-vec`` for vector distance queries when available.
    """

    def __init__(
        self,
        db_path: Path,
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self.db_path = db_path
        self._embedding_model = embedding_model
        self._conn: Any = None
        self._has_vec: bool = False

    async def _init_db(self) -> None:
        if self._conn is not None:
            return

        import aiosqlite

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))

        # Try to load sqlite-vec extension
        try:
            import sqlite_vec

            await self._conn.execute("SELECT 1")  # ensure connection is live
            conn_raw = self._conn._connection  # underlying sqlite3.Connection
            conn_raw.enable_load_extension(True)
            sqlite_vec.load(conn_raw)
            conn_raw.enable_load_extension(False)
            self._has_vec = True
            logger.info("sqlite-vec extension loaded")
        except Exception as exc:
            logger.warning("sqlite-vec not available, vector search disabled: %s", exc)
            self._has_vec = False

        # Core chunks table
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                text TEXT,
                source TEXT,
                timestamp REAL,
                embedding BLOB
            )
        """)

        # File tracking for incremental indexing
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS indexed_files (
                path TEXT PRIMARY KEY,
                last_indexed REAL
            )
        """)

        # Create virtual table for vector search if sqlite-vec is available
        if self._has_vec:
            try:
                await self._conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
                    USING vec0(embedding float[{EmbeddingModel.DIMENSIONS}])
                """)
            except Exception as exc:
                logger.warning("Failed to create vec_chunks table: %s", exc)
                self._has_vec = False

        await self._conn.commit()

    def chunk_markdown(self, text: str) -> list[str]:
        """Split markdown text into chunks of roughly 1600 characters."""
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if current_len + len(p) > 1600 and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p) + 2
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
        return chunks

    async def index_file(self, file_path: Path, source_label: str) -> None:
        """Index a single markdown file, generating real embeddings."""
        await self._init_db()

        try:
            text = file_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return

        # Check if file needs re-indexing
        mtime = file_path.stat().st_mtime
        async with self._conn.execute(
            "SELECT last_indexed FROM indexed_files WHERE path = ?",
            (str(file_path),),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] >= mtime:
                return  # already up-to-date

        # Remove old chunks for this source
        await self._conn.execute(
            "DELETE FROM chunks WHERE source = ?", (source_label,)
        )
        if self._has_vec:
            # Remove old vector entries for this source's chunk IDs
            await self._conn.execute("""
                DELETE FROM vec_chunks WHERE rowid NOT IN (
                    SELECT id FROM chunks
                )
            """)

        chunks = self.chunk_markdown(text)
        if not chunks:
            return

        ts = time.time()

        # Generate embeddings
        if self._embedding_model is not None:
            try:
                embeddings = await self._embedding_model.encode(chunks)
            except Exception as exc:
                logger.warning("Embedding failed, using empty: %s", exc)
                embeddings = [[0.0] * EmbeddingModel.DIMENSIONS] * len(chunks)
        else:
            embeddings = [[0.0] * EmbeddingModel.DIMENSIONS] * len(chunks)

        for chunk_text, emb in zip(chunks, embeddings):
            blob = _floats_to_blob(emb)
            cursor = await self._conn.execute(
                "INSERT INTO chunks (text, source, timestamp, embedding) VALUES (?, ?, ?, ?)",
                (chunk_text, source_label, ts, blob),
            )
            chunk_id = cursor.lastrowid

            # Insert into vector index
            if self._has_vec and chunk_id is not None:
                try:
                    await self._conn.execute(
                        "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                        (chunk_id, blob),
                    )
                except Exception as exc:
                    logger.warning("vec_chunks insert failed for chunk %s: %s", chunk_id, exc)

        # Update file tracking
        await self._conn.execute(
            "INSERT OR REPLACE INTO indexed_files (path, last_indexed) VALUES (?, ?)",
            (str(file_path), mtime),
        )
        await self._conn.commit()

    async def index_directory(self, dir_path: Path) -> None:
        """Index all markdown files in a directory."""
        if not dir_path.exists():
            return
        for file_path in sorted(dir_path.glob("*.md")):
            await self.index_file(file_path, file_path.name)

    async def get_chunks(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[MemoryChunk]:
        """Retrieve the closest chunks by cosine distance.

        Uses sqlite-vec when available, otherwise falls back to scanning
        all rows in Python.
        """
        await self._init_db()

        if self._has_vec:
            return await self._vec_search(query_embedding, top_k)
        return await self._fallback_search(query_embedding, top_k)

    async def _vec_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[MemoryChunk]:
        """Use sqlite-vec virtual table for fast vector search."""
        query_blob = _floats_to_blob(query_embedding)
        sql = """
            SELECT c.text, c.source, c.timestamp, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """
        async with self._conn.execute(sql, (query_blob, top_k)) as cursor:
            rows = await cursor.fetchall()

        return [
            MemoryChunk(
                text=row[0],
                source=row[1],
                timestamp=row[2],
                # Convert distance to similarity score (1 - cosine_distance)
                score=max(0.0, 1.0 - row[3]),
            )
            for row in rows
        ]

    async def _fallback_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[MemoryChunk]:
        """Brute-force cosine similarity when sqlite-vec is unavailable."""
        async with self._conn.execute(
            "SELECT text, source, timestamp, embedding FROM chunks"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return []

        scored: list[tuple[float, Any]] = []
        for row in rows:
            emb = _blob_to_floats(row[3])
            sim = _cosine_similarity(query_embedding, emb)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            MemoryChunk(
                text=row[0],
                source=row[1],
                timestamp=row[2],
                score=sim,
            )
            for sim, row in scored[:top_k]
        ]

    async def get_all_chunks(self) -> list[MemoryChunk]:
        """Return all indexed chunks (used for BM25 scoring)."""
        await self._init_db()
        async with self._conn.execute(
            "SELECT text, source, timestamp FROM chunks"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            MemoryChunk(text=r[0], source=r[1], timestamp=r[2], score=0.0)
            for r in rows
        ]

    async def reindex(self, workspace_dir: Path) -> None:
        """Rebuild all embeddings (e.g. after model change)."""
        await self._init_db()

        # Clear everything
        await self._conn.execute("DELETE FROM chunks")
        await self._conn.execute("DELETE FROM indexed_files")
        if self._has_vec:
            await self._conn.execute("DELETE FROM vec_chunks")
        await self._conn.commit()

        # Re-index MEMORY.md
        memory_file = workspace_dir / "MEMORY.md"
        if memory_file.exists():
            await self.index_file(memory_file, "MEMORY.md")

        # Re-index daily logs
        await self.index_directory(workspace_dir / "memory")

        # Re-index conversation archives
        await self.index_directory(workspace_dir / "conversations")

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
