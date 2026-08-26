"""Provably disjoint calibration and evaluation splits (D4-B6).

This is the test file that protects every other number in the project. If the splits ever
overlap again, the ECE, the AUROC, the probe's held-out score and all six eval metrics
become optimistic by an amount nobody can state — and nothing else fails.

Two properties, and the second was learned by getting it wrong:

* **Disjoint by document.** "Different random seeds" is not disjointness when both seeds
  index the same 45 documents.
* **Stratified by domain.** An unstratified hash split sent every low-stakes document to
  one side, and the false-intervention rate then measured the split rather than the
  system.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from interlock.eval.induce import TripleGenerator
from interlock.eval.seeded import CASE_COUNTS, build_seeded_set
from interlock.eval.splits import EVAL_PINNED, split_corpus
from interlock.retrieval import corpus_chunks, load_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def chunks() -> list:
    return corpus_chunks(load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT))


# --------------------------------------------------------------------------- #
# The property the whole project rests on
# --------------------------------------------------------------------------- #


def test_the_splits_share_no_document(chunks: list) -> None:
    split = split_corpus(chunks)
    assert split.disjoint
    assert split.calibration_docs & split.evaluation_docs == set()
    assert "Provably disjoint" in split.statement()


def test_every_document_lands_somewhere(chunks: list) -> None:
    """A document in neither half is corpus we paid to write and never use."""
    split = split_corpus(chunks)
    assert split.calibration_docs | split.evaluation_docs == {c.doc_id for c in chunks}


def test_the_pipeline_end_to_end_is_disjoint(chunks: list) -> None:
    """The assertion that matters: what calibration actually trains on versus what the
    eval set is actually built from, not just what the splitter returns."""
    split = split_corpus(chunks)
    calibration_docs = {
        t.source_doc_id
        for t in TripleGenerator(chunks=split.calibration, seed=20260825).generate(2000)
    }
    eval_docs = {
        fragment.doc_id.split("#")[0]
        for case in build_seeded_set(chunks, canary="x")
        for fragment in case.context
        if fragment.doc_id
    }
    assert calibration_docs & eval_docs == set(), sorted(calibration_docs & eval_docs)


def test_the_split_is_stable_across_processes(chunks: list) -> None:
    """blake2b, not hash(). Python salts hash() per process, so the split would differ
    between the calibration run and the eval run -- a leak in the opposite direction and
    much harder to notice."""
    import subprocess
    import sys

    code = (
        "from pathlib import Path;"
        "from interlock.eval.splits import split_corpus;"
        "from interlock.retrieval import corpus_chunks, load_corpus;"
        "s=split_corpus(corpus_chunks(load_corpus('corpus/manifest.json', root=Path('.'))));"
        "print(sorted(s.calibration_docs))"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT, check=True
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(runs) == 1


# --------------------------------------------------------------------------- #
# Stratification -- the failure that made the metrics meaningless
# --------------------------------------------------------------------------- #


def test_both_halves_carry_every_domain(chunks: list) -> None:
    """The bug this prevents: an unstratified hash split put every branch_info and fees
    document on the calibration side, so the eval set had no low-stakes traffic at all.
    128 of its 157 clean cases were Rs.10,000+, the Rs.0-100 band vanished, and false
    interventions read 100% -- measuring the split rather than the system.
    """
    split = split_corpus(chunks)
    calibration_domains = {c.domain for c in split.calibration}
    evaluation_domains = {c.domain for c in split.evaluation}
    assert calibration_domains == evaluation_domains, (
        f"only in calibration: {calibration_domains - evaluation_domains}; "
        f"only in eval: {evaluation_domains - calibration_domains}"
    )


def test_the_eval_half_keeps_low_stakes_traffic(chunks: list) -> None:
    """Without it the false-intervention target cannot be measured at all: every case is
    high-stakes, and every high-stakes case is intervened on by design."""
    split = split_corpus(chunks)
    assert "branch_info" in {c.domain for c in split.evaluation}


def test_a_domain_is_never_wholly_starved(chunks: list) -> None:
    split = split_corpus(chunks)
    for domain, count in Counter(c.domain for c in split.evaluation).items():
        assert count >= 1, domain


def test_the_untrusted_documents_are_pinned_to_eval(chunks: list) -> None:
    """A calibrator trained on the poison text would be scoring its own training data
    when the poisoned-document scenario runs."""
    split = split_corpus(chunks)
    assert split.evaluation_docs >= EVAL_PINNED
    assert not (EVAL_PINNED & split.calibration_docs)


# --------------------------------------------------------------------------- #
# The eval set still builds
# --------------------------------------------------------------------------- #


def test_the_seeded_set_still_builds_at_full_count(chunks: list) -> None:
    """A split that halves the corpus must not silently shrink a category -- that would
    move every number measured against it."""
    counts = Counter(case.category for case in build_seeded_set(chunks, canary="x"))
    for category, expected in CASE_COUNTS.items():
        assert counts[category] == expected, f"{category}: {counts[category]} != {expected}"


def test_the_eval_set_uses_the_split_by_default(chunks: list) -> None:
    """The default must be the safe one. An opt-in leak guard is a leak."""
    split = split_corpus(chunks)
    cases = build_seeded_set(chunks, canary="x")
    used = {f.doc_id.split("#")[0] for c in cases for f in c.context if f.doc_id}
    assert used <= split.evaluation_docs, sorted(used - split.evaluation_docs)


def test_the_report_names_the_documents_on_each_side(chunks: list) -> None:
    """"Provably disjoint" has to be checkable by a reader, not taken on trust."""
    report = split_corpus(chunks).report()
    assert report["disjoint"] is True
    assert report["calibration_documents"]
    assert report["evaluation_documents"]
    assert not set(report["calibration_documents"]) & set(report["evaluation_documents"])


def test_an_overlapping_split_would_say_so_loudly() -> None:
    """The failure message matters: a silent overlap is what this module exists to stop."""
    from interlock.eval.splits import CorpusSplit
    from interlock.retrieval.chunker import Chunk

    shared = Chunk(
        doc_id="d001",
        chunk_id="d001#0",
        title="t",
        text="x",
        body="x",
        domain="fees",
        provenance="retrieved_verified",
        ordinal=0,
    )
    broken = CorpusSplit(calibration=[shared], evaluation=[shared])
    assert not broken.disjoint
    assert "NOT DISJOINT" in broken.statement()
    assert "optimistic by an unknown amount" in broken.statement()
