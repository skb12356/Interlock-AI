"""The corpus index: SQLite FTS5 for lexical, sqlite-vec for dense, fused.

Two arms, because each fails where the other works.

* **Lexical (FTS5/BM25).** Exact on the tokens that matter most in this domain --
  ``9.1``, ``prepayment``, ``IFSC`` -- and it is the arm that finds a clause by its
  number. Blind to paraphrase.
* **Dense (sqlite-vec KNN).** Survives paraphrase. With the default hashing embedder
  it is a second lexical view rather than a semantic one; with ``bge-small-en-v1.5``
  configured it becomes the real thing. Either way the plumbing is identical.

Fused with **Reciprocal Rank Fusion** rather than a weighted score sum. BM25 scores and
cosine similarities are on unrelated scales, so any weighted sum needs a normalisation
constant that has to be re-tuned every time either arm changes. RRF only reads ranks,
so it needs no tuning and cannot be silently mis-calibrated -- the same reason the rest
of this system prefers arithmetic it can defend.

**This database is separate from the ledger, and opened read-only on the request path.**
Contract 5's single-writer rule governs the ledger, whose whole point is that concurrent
request handlers must not write to it. The index is built offline by
``scripts/build_index.py``, never written during a request, and mounted ``mode=ro`` so
that is enforced by SQLite rather than by convention.
"""

from __future__ import annotations

import sqlite3
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interlock.core.types import Provenance
from interlock.retrieval.chunker import Chunk
from interlock.retrieval.embedder import Embedder, embed_query, tokenize

__all__ = [
    "DEFAULT_ARM_DEPTH",
    "DOMAIN_OVERFETCH",
    "LEXICAL_STANDIN_WEIGHT",
    "RRF_K",
    "SCHEMA_VERSION",
    "Hit",
    "RetrievalIndex",
]

SCHEMA_VERSION = 1

#: How deep each arm goes before fusion. Wider than k so the fusion has something to
#: disagree about; a top-k-then-fuse would just return the lexical ordering.
DEFAULT_ARM_DEPTH = 20

#: RRF's damping constant. 60 is the value from the original formulation; it is not
#: tuned here, and deliberately so -- see the module docstring.
RRF_K = 60.0

#: How much the dense arm's vote is worth when the embedder is **not** a semantic model.
#: With the hashing stand-in the two arms are both lexical, so an equal vote does not
#: add a second opinion -- it duplicates the first one badly and lets the arm with no
#: IDF drag the arm that has it. Half a vote keeps the dense arm as a tie-breaker.
#: With ``bge-small-en-v1.5`` configured, ``semantic`` is True and this drops out.
LEXICAL_STANDIN_WEIGHT = 0.5

#: Multiplier on the dense arm's fetch depth when a domain filter is in play.
#: See ``_dense`` -- the filter cannot be pushed into vec0's KNN.
DOMAIN_OVERFETCH = 8


@dataclass(frozen=True, slots=True)
class Hit:
    """One retrieved chunk plus how each arm found it -- the console shows both."""

    chunk: Chunk
    score: float
    lexical_rank: int | None = None
    dense_rank: int | None = None

    @property
    def found_by(self) -> str:
        if self.lexical_rank is not None and self.dense_rank is not None:
            return "both"
        return "lexical" if self.lexical_rank is not None else "dense"


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _fts_query(text: str) -> str:
    """Turn a natural-language question into a query FTS5 will actually parse.

    Every token is quoted, because an unquoted ``NEAR``, ``OR``, ``*`` or ``-`` in a
    customer's sentence is an FTS5 operator and raises rather than searching. Joined
    with OR because a conjunctive query over a whole question matches nothing.
    """
    tokens = [t for t in tokenize(text) if len(t) > 1]
    return " OR ".join('"' + token + '"' for token in tokens)


class RetrievalIndex:
    """Read path over the built index. Build with :meth:`build`."""

    def __init__(self, db_path: Path | str, *, embedder: Embedder, read_only: bool = True) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
        self._db = _connect(self.db_path, read_only=read_only)
        self._meta = self._read_meta()
        self._check_compatible()

    # ------------------------------------------------------------------ build --

    @classmethod
    def build(
        cls,
        db_path: Path | str,
        chunks: Iterable[Chunk],
        *,
        embedder: Embedder,
        corpus_version: str = "",
    ) -> RetrievalIndex:
        """(Re)build the index from scratch. Rebuilding is cheap; patching is not."""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        db = _connect(path, read_only=False)
        rows = list(chunks)
        vectors = embedder.encode([chunk.text for chunk in rows]) if rows else []
        if rows and len(vectors[0]) != embedder.dim:
            raise ValueError(
                f"embedder {embedder.name} declares dim={embedder.dim} "
                f"but produced {len(vectors[0])}"
            )

        db.executescript(_schema(embedder.dim))
        with db:
            db.executemany(
                "INSERT INTO chunks(chunk_id, doc_id, title, body, text, domain, "
                "provenance, ordinal) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        c.chunk_id,
                        c.doc_id,
                        c.title,
                        c.body,
                        c.text,
                        c.domain,
                        c.provenance,
                        c.ordinal,
                    )
                    for c in rows
                ],
            )
            ids = [r[0] for r in db.execute("SELECT rowid FROM chunks ORDER BY rowid")]
            db.executemany(
                "INSERT INTO chunks_fts(rowid, text) VALUES (?,?)",
                [(rowid, c.text) for rowid, c in zip(ids, rows, strict=True)],
            )
            db.executemany(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?,?)",
                [(rowid, _pack(v)) for rowid, v in zip(ids, vectors, strict=True)],
            )
            db.executemany(
                "INSERT INTO index_meta(key, value) VALUES (?,?)",
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("embedder", embedder.name),
                    ("dim", str(embedder.dim)),
                    ("chunk_count", str(len(rows))),
                    ("corpus_version", corpus_version),
                ],
            )
        db.close()
        return cls(path, embedder=embedder)

    # ------------------------------------------------------------------- read --

    @property
    def meta(self) -> dict[str, str]:
        return dict(self._meta)

    def __len__(self) -> int:
        return int(self._meta.get("chunk_count", "0"))

    def close(self) -> None:
        self._db.close()

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        depth: int = DEFAULT_ARM_DEPTH,
        domain: str | None = None,
    ) -> list[Hit]:
        """Hybrid search. Returns at most ``k`` hits, best first."""
        if not query.strip() or len(self) == 0:
            return []
        lexical = self._lexical(query, depth=depth, domain=domain)
        dense = self._dense(query, depth=depth, domain=domain)
        return self._fuse(lexical, dense)[:k]

    def get(self, chunk_id: str) -> Chunk | None:
        row = self._db.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        return _to_chunk(row) if row else None

    def all_chunks(self) -> list[Chunk]:
        return [_to_chunk(row) for row in self._db.execute("SELECT * FROM chunks ORDER BY rowid")]

    # --------------------------------------------------------------- internals --

    def _lexical(self, query: str, *, depth: int, domain: str | None) -> list[str]:
        match = _fts_query(query)
        if not match:
            return []
        sql = (
            "SELECT c.chunk_id FROM chunks_fts f JOIN chunks c ON c.rowid = f.rowid "
            "WHERE chunks_fts MATCH ?"
        )
        params: list[Any] = [match]
        if domain:
            sql += " AND c.domain = ?"
            params.append(domain)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        params.append(depth)
        try:
            return [row[0] for row in self._db.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            # A query FTS5 still refuses to parse. Search degrades to the dense arm
            # rather than failing the request: partial context beats none.
            return []

    def _dense(self, query: str, *, depth: int, domain: str | None) -> list[str]:
        vector = embed_query(self.embedder, query)
        # vec0's KNN cannot filter by domain, so the filter is applied after the search.
        # That means a narrow domain must be over-fetched or it comes back empty while
        # matching documents sit just outside k -- the dense arm silently disappearing
        # while the lexical arm still answers, which reads as a quality problem rather
        # than a bug. The corpus is small enough that over-fetching costs nothing.
        fetch = depth * DOMAIN_OVERFETCH if domain else depth
        rows = self._db.execute(
            "SELECT rowid FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_pack(vector), min(fetch, max(len(self), 1))),
        ).fetchall()
        if not rows:
            return []
        rowids = [row[0] for row in rows]
        placeholders = ",".join("?" for _ in rowids)
        by_rowid = {
            r[0]: (r[1], r[2])
            for r in self._db.execute(
                f"SELECT rowid, chunk_id, domain FROM chunks WHERE rowid IN ({placeholders})",
                rowids,
            )
        }
        out: list[str] = []
        for rowid in rowids:
            entry = by_rowid.get(rowid)
            if entry and (domain is None or entry[1] == domain):
                out.append(entry[0])
        return out

    def _fuse(self, lexical: list[str], dense: list[str]) -> list[Hit]:
        lex_rank = {chunk_id: i for i, chunk_id in enumerate(lexical)}
        den_rank = {chunk_id: i for i, chunk_id in enumerate(dense)}
        dense_weight = 1.0 if getattr(self.embedder, "semantic", False) else LEXICAL_STANDIN_WEIGHT
        scores: dict[str, float] = {}
        for ranks, weight in ((lex_rank, 1.0), (den_rank, dense_weight)):
            for chunk_id, rank in ranks.items():
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + rank + 1.0)

        # Ties break on chunk_id, so the same query returns the same order every time.
        # A retrieval layer that reorders under a stable index makes every downstream
        # measurement unreproducible.
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if not ordered:
            return []
        placeholders = ",".join("?" for _ in ordered)
        by_id = {
            row["chunk_id"]: _to_chunk(row)
            for row in self._db.execute(
                f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
                [chunk_id for chunk_id, _ in ordered],
            )
        }
        return [
            Hit(
                chunk=by_id[chunk_id],
                score=score,
                lexical_rank=lex_rank.get(chunk_id),
                dense_rank=den_rank.get(chunk_id),
            )
            for chunk_id, score in ordered
            if chunk_id in by_id
        ]

    def _read_meta(self) -> dict[str, str]:
        return {row[0]: row[1] for row in self._db.execute("SELECT key, value FROM index_meta")}

    def _check_compatible(self) -> None:
        """Refuse a mismatched index rather than returning quietly wrong neighbours.

        A dense arm queried with the wrong embedder does not error -- it returns
        confident nonsense, which is the failure mode this whole project exists to stop.
        """
        stored_dim = self._meta.get("dim")
        if stored_dim is not None and int(stored_dim) != self.embedder.dim:
            raise ValueError(
                f"index at {self.db_path} was built with dim={stored_dim}, but embedder "
                f"{self.embedder.name} has dim={self.embedder.dim}; rebuild it"
            )
        stored_embedder = self._meta.get("embedder")
        if stored_embedder and stored_embedder != self.embedder.name:
            raise ValueError(
                f"index at {self.db_path} was built with embedder '{stored_embedder}', "
                f"not '{self.embedder.name}'; rebuild it"
            )


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    import sqlite_vec

    if read_only:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    else:
        db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def _schema(dim: int) -> str:
    return f"""
    CREATE TABLE chunks (
        chunk_id   TEXT PRIMARY KEY,
        doc_id     TEXT NOT NULL,
        title      TEXT NOT NULL,
        body       TEXT NOT NULL,
        text       TEXT NOT NULL,
        domain     TEXT NOT NULL,
        provenance TEXT NOT NULL,
        ordinal    INTEGER NOT NULL
    );
    CREATE INDEX idx_chunks_doc ON chunks(doc_id);
    CREATE INDEX idx_chunks_domain ON chunks(domain);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='');
    CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[{dim}]);
    CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """


def _to_chunk(row: sqlite3.Row) -> Chunk:
    provenance: Provenance = row["provenance"]
    return Chunk(
        doc_id=row["doc_id"],
        chunk_id=row["chunk_id"],
        title=row["title"],
        body=row["body"],
        text=row["text"],
        domain=row["domain"],
        provenance=provenance,
        ordinal=row["ordinal"],
    )
