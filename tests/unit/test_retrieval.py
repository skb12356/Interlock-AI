"""Retrieval tests.

The assertions worth having here are not "search returns something". They are the
properties the rest of the system is entitled to assume: that every result carries the
labels the stakes model and the tool interlock read, that the poisoned document is
*retrieved* rather than quietly filtered, that a repair is never handed an untrusted
passage as ground truth, and that a stale index is refused rather than answered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.types import Fragment
from interlock.retrieval import (
    HashingEmbedder,
    RetrievalIndex,
    Retriever,
    chunk_markdown,
    corpus_chunks,
    load_corpus,
)
from interlock.retrieval.embedder import SentenceTransformerEmbedder, load_embedder, tokenize
from interlock.retrieval.retriever import NullRetriever
from interlock.retrieval.store import _fts_query

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "corpus" / "manifest.json"


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> RetrievalIndex:
    documents = load_corpus(MANIFEST, root=REPO_ROOT)
    path = tmp_path_factory.mktemp("index") / "corpus.db"
    return RetrievalIndex.build(
        path, corpus_chunks(documents), embedder=HashingEmbedder(), corpus_version="test"
    )


@pytest.fixture(scope="module")
def retriever(index: RetrievalIndex) -> Retriever:
    return Retriever(index=index)


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #


def test_every_chunk_carries_its_heading() -> None:
    """The clause number lives in the heading. A chunk without it cannot be cited."""
    chunks = chunk_markdown(
        "# Home Loan Agreement - Clause 9.1\n\nFirst para.\n\n" + "Second para. " * 40,
        doc_id="d001",
        domain="prepayment",
        provenance="retrieved_verified",
        max_chars=200,
    )
    assert len(chunks) > 1, "the fixture was meant to split"
    for chunk in chunks:
        assert chunk.text.startswith("Home Loan Agreement - Clause 9.1")
        assert chunk.doc_id == "d001"
        assert chunk.chunk_id.startswith("d001#")


def test_a_document_that_is_only_a_heading_still_indexes() -> None:
    chunks = chunk_markdown(
        "# Branch Directory - Mumbai\n",
        doc_id="d013",
        domain="branch_info",
        provenance="retrieved_verified",
    )
    assert len(chunks) == 1
    assert "Mumbai" in chunks[0].text


def test_oversized_paragraphs_break_on_sentence_boundaries() -> None:
    """A chunk cut mid-sentence retrieves on half a claim and cites the half kept."""
    body = " ".join(f"Sentence number {i} says something specific." for i in range(40))
    chunks = chunk_markdown(
        f"# T\n\n{body}",
        doc_id="d",
        domain="general",
        provenance="retrieved_verified",
        max_chars=300,
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.body.rstrip().endswith("."), chunk.body[-40:]


def test_chunk_to_fragment_populates_the_contract_fields() -> None:
    chunk = chunk_markdown(
        "# T\n\nBody.", doc_id="d044", domain="claims", provenance="retrieved_untrusted"
    )[0]
    fragment = chunk.to_fragment(score=0.5)
    assert isinstance(fragment, Fragment)
    assert fragment.provenance == "retrieved_untrusted"
    assert fragment.domain == "claims"
    assert fragment.doc_id == "d044#0"
    assert fragment.role == "retrieved"


# --------------------------------------------------------------------------- #
# The corpus manifest is the authority for the labels
# --------------------------------------------------------------------------- #


def test_the_whole_manifest_loads_and_nothing_on_disk_is_orphaned() -> None:
    """Strict both ways: a corpus quietly one document short changes every number."""
    documents = load_corpus(MANIFEST, root=REPO_ROOT)
    assert len(documents) == 45


def test_the_poisoned_pdf_is_labelled_untrusted_at_ingestion(index: RetrievalIndex) -> None:
    """Not re-derived downstream. The tool interlock joins on this label."""
    untrusted = {c.doc_id for c in index.all_chunks() if c.provenance == "retrieved_untrusted"}
    assert untrusted == {"d044", "d045"}


def test_a_missing_file_is_an_error_not_a_gap(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"documents": [{"doc_id": "x", "path": "corpus/nope.md", "domain": "general"}]}',
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match=r"nope\.md"):
        load_corpus(manifest, root=tmp_path)


def test_an_unknown_provenance_label_is_refused(tmp_path: Path) -> None:
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "a.md").write_text("# A\n\nBody.", encoding="utf-8")
    manifest = tmp_path / "corpus" / "manifest.json"
    manifest.write_text(
        '{"documents": [{"doc_id": "a", "path": "corpus/a.md", "domain": "general",'
        ' "provenance": "trust_me"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown provenance"):
        load_corpus(manifest, root=tmp_path)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_the_scene_one_question_retrieves_the_authoritative_clause(retriever: Retriever) -> None:
    """Clause 9.1 is what the model gets wrong, so it is what retrieval must find."""
    hits = retriever.search("Can I prepay my home loan on a floating rate? Is there a charge?", k=5)
    assert hits[0].chunk.doc_id == "d001"
    assert "9.1" in hits[0].chunk.title


def test_both_halves_of_a_contradictory_pair_are_reachable(retriever: Retriever) -> None:
    """The 'contradicted' defect is only demonstrable if the contradiction is retrieved.

    Suppressing d002 would make the demo cleaner and the claim false.
    """
    doc_ids = {hit.chunk.doc_id for hit in retriever.search("prepayment foreclosure charge", k=10)}
    assert {"d001", "d002"} <= doc_ids


def test_the_poisoned_document_reaches_the_context_window(retriever: Retriever) -> None:
    """Deliberate. Filtering here would hide the mechanism the product claims.

    d044 is caught by the per-chunk injection scan and stopped at the tool boundary by
    provenance -- neither of which is exercised if retrieval silently drops it.
    """
    hits = retriever.search("my claim was rejected, how long do I have to appeal", k=5)
    assert any(hit.chunk.doc_id == "d044" for hit in hits)


def test_results_are_ordered_and_capped(retriever: Retriever) -> None:
    hits = retriever.search("fees and charges", k=3)
    assert len(hits) == 3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_the_same_query_returns_the_same_order(retriever: Retriever) -> None:
    """Ties break deterministically, or every downstream measurement is unreproducible."""
    query = "annual fee credit card"
    first = [h.chunk.chunk_id for h in retriever.search(query, k=6)]
    for _ in range(3):
        assert [h.chunk.chunk_id for h in retriever.search(query, k=6)] == first


def test_domain_filter_restricts_results(retriever: Retriever) -> None:
    hits = retriever.search(
        "where is the nearest branch and when does it open", k=8, domain="branch_info"
    )
    assert hits
    assert {h.chunk.domain for h in hits} == {"branch_info"}


def test_a_domain_filter_does_not_silently_kill_the_dense_arm(retriever: Retriever) -> None:
    """vec0 KNN cannot filter, so a narrow domain must be over-fetched before the
    filter is applied -- otherwise the dense arm returns nothing while the lexical
    arm still answers, which reads as a quality problem rather than a bug."""
    hits = retriever.search("prepay the loan early", k=5, domain="prepayment")
    assert any(h.dense_rank is not None for h in hits)


def test_an_empty_query_returns_nothing_rather_than_everything(retriever: Retriever) -> None:
    assert retriever.search("   ", k=5) == []


@pytest.mark.parametrize(
    "query",
    [
        "NEAR fees",  # bare FTS5 operators
        "what about OR AND NOT",
        'charge* "unbalanced',
        "-fees",
        "?!?!",
        "₹3,000 fee -- is that right?",
    ],
)
def test_operator_shaped_questions_do_not_raise(retriever: Retriever, query: str) -> None:
    """Customers type FTS5 operators without knowing it. Unquoted, they raise."""
    retriever.search(query, k=3)


def test_fts_query_quotes_every_token() -> None:
    assert _fts_query("NEAR fees?") == '"near" OR "fees"'
    assert _fts_query("!") == ""


# --------------------------------------------------------------------------- #
# Evidence for repair
# --------------------------------------------------------------------------- #


def test_repair_evidence_never_includes_an_untrusted_passage(retriever: Retriever) -> None:
    """This text is fed to a model as ground truth to rewrite against.

    A repair that corrects an answer into agreement with a poisoned document is worse
    than no repair at all.
    """
    question = "my claim was rejected, how long do I have to appeal"
    assert any(h.chunk.doc_id == "d044" for h in retriever.search(question, k=5)), (
        "fixture assumption: the poisoned doc is retrievable for this question"
    )
    evidence = retriever.evidence_for(question, k=3)
    assert evidence
    poisoned = {c.text for c in retriever.index.all_chunks() if c.doc_id == "d044"}
    assert not (set(evidence) & poisoned)


async def test_retrieve_returns_fragments_and_measures_itself(retriever: Retriever) -> None:
    result = await retriever.retrieve("annual fee on the credit card")
    assert result.fragments
    assert all(isinstance(f, Fragment) for f in result.fragments)
    assert all(f.domain for f in result.fragments)
    assert result.latency_ms >= 0.0
    assert result.query == "annual fee on the credit card"


async def test_the_null_retriever_degrades_instead_of_failing() -> None:
    """A missing index degrades the answer; it does not fail the request."""
    null = NullRetriever()
    assert (await null.retrieve("anything")).fragments == []
    assert null.evidence_for("anything") == []


# --------------------------------------------------------------------------- #
# The index refuses to answer when it is stale
# --------------------------------------------------------------------------- #


def test_a_dimension_mismatch_is_refused_not_coerced(index: RetrievalIndex) -> None:
    """A dense arm queried with the wrong embedder returns confident nonsense."""
    with pytest.raises(ValueError, match="rebuild it"):
        RetrievalIndex(index.db_path, embedder=HashingEmbedder(dim=64))


def test_a_different_embedder_of_the_same_width_is_also_refused(index: RetrievalIndex) -> None:
    other = HashingEmbedder(dim=256)
    other.name = "some-other-model"
    with pytest.raises(ValueError, match="rebuild it"):
        RetrievalIndex(index.db_path, embedder=other)


def test_the_index_records_what_built_it(index: RetrievalIndex) -> None:
    assert index.meta["embedder"] == "hashing-v1"
    assert index.meta["dim"] == "256"
    assert int(index.meta["chunk_count"]) == len(index.all_chunks())


def test_the_request_path_opens_the_index_read_only(index: RetrievalIndex) -> None:
    reader = RetrievalIndex(index.db_path, embedder=HashingEmbedder())
    with pytest.raises(Exception, match=r"readonly|read-only|attempt to write"):
        reader._db.execute("DELETE FROM chunks")


# --------------------------------------------------------------------------- #
# Embedder
# --------------------------------------------------------------------------- #


def test_hashing_is_stable_across_processes() -> None:
    """Python salts hash() per process. An index built in one would not match queries
    embedded in the next -- and would look like a slow quality regression, not a bug."""
    import subprocess
    import sys

    code = (
        "from interlock.retrieval.embedder import HashingEmbedder;"
        "print(HashingEmbedder().encode(['prepayment charge'])[0][:4])"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT, check=True
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(runs) == 1


def test_vectors_are_unit_length() -> None:
    vector = HashingEmbedder().encode(["prepayment charge on a floating rate loan"])[0]
    assert abs(sum(v * v for v in vector) - 1.0) < 1e-9


def test_an_all_stopword_input_does_not_divide_by_zero() -> None:
    assert HashingEmbedder().encode(["the a of and"])[0] == [0.0] * 256


def test_clause_numbers_survive_tokenisation() -> None:
    assert "9.1" in tokenize("Clause 9.1 applies")


def test_the_default_embedder_does_not_claim_to_be_semantic() -> None:
    """Honest accounting (CLAUDE.md s9): the stand-in must not overstate itself."""
    assert HashingEmbedder().semantic is False
    assert SentenceTransformerEmbedder().semantic is True


def test_load_embedder_resolves_the_default_without_torch() -> None:
    embedder = load_embedder()
    assert isinstance(embedder, HashingEmbedder)
    assert embedder.dim == 256
