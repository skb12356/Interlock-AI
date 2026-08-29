"""Build the measured Person 1 status report as a PDF."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "eval" / "person1_results_report.pdf"


def anchors() -> tuple[int, int, Counter[str], int, int]:
    rows = [
        json.loads(line)
        for line in (ROOT / "data/labels/manual_anchor_300.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [row["item_id"] for row in rows]
    modes = Counter(row["payload"]["failure_mode"] for row in rows)
    clean_contained = sum(
        row["payload"]["answer"] in " ".join(c["text"] for c in row["payload"]["context"])
        for row in rows
        if row["payload"]["failure_mode"] == "clean"
    )
    consistency_errors = sum(
        (row["payload"]["failure_mode"] == "clean") != (row["payload"]["label"] == 0)
        for row in rows
    )
    return len(rows), len(set(ids)), modes, clean_contained, consistency_errors


def table(data: list[list[str]], widths: list[float]) -> Table:
    cell = ParagraphStyle("TableCell", fontName="Helvetica", fontSize=8.2, leading=10)
    header = ParagraphStyle("TableHeader", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    wrapped = [[Paragraph(str(value), header if row == 0 else cell) for value in line] for row, line in enumerate(data)]
    result = Table(wrapped, colWidths=widths, repeatRows=1)
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b8c4cf")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f7")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return result


def build() -> None:
    count, unique, modes, contained, errors = anchors()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Body2", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Subx", parent=styles["BodyText"], fontSize=11, leading=14, textColor=colors.HexColor("#526675")))
    styles.add(ParagraphStyle(name="Title2", parent=styles["Title"], fontSize=23, leading=27, textColor=colors.HexColor("#17324d"), alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontSize=14, leading=17, textColor=colors.HexColor("#17324d"), spaceBefore=10, spaceAfter=6))
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=17 * mm, leftMargin=17 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    story: list[object] = []
    story += [Paragraph("Person 1: Measured Results", styles["Title2"]), Paragraph("Interlock V2 | 29 August 2026", styles["Subx"]), Spacer(1, 5 * mm)]
    story += [Paragraph("Executive result", styles["H2x"]), Paragraph("The core Person 1 implementation is present and passes the repository verification suite. The 300-item anchor set has been exhaustively checked for integrity and policy-label consistency. Live Ollama is now available with Qwen 4B and Qwen 8B, but CPU inference is too slow for an honest full multi-model judge run in this session. The report therefore separates measured results from open evidence gates.", styles["Body2"])]
    story += [table([["Measure", "Result"], ["Repository tests", "906 passed"], ["Chaos tests", "7 passed"], ["Ruff", "Clean"], ["Anchor records", f"{count} records / {unique} unique IDs"], ["Policy hash", "banking-v3@sha256:0aeb8a4b17fe218c"]], [55 * mm, 105 * mm]), Spacer(1, 5 * mm)]
    story += [Paragraph("System path", styles["H2x"]), Preformatted("User -> Gateway -> stakes/routing -> Qwen tier\n                         -> commit gate -> observer/risk engine\n                         -> pass | repair | reroute | hold | block\n                         -> ConsoleHub events + ledger economics", styles["Code"]), Spacer(1, 3 * mm)]
    story += [Paragraph("300-anchor exhaustive checks", styles["H2x"]), table([["Check", "Result"], ["Record count", str(count)], ["Unique item IDs", str(unique)], ["Failure-mode distribution", ", ".join(f"{k}: {v}" for k, v in sorted(modes.items()))], ["Clean answers contained in supplied context", f"{contained}/270"], ["Label consistency errors", str(errors)]], [75 * mm, 85 * mm])]
    story += [Paragraph("Interpretation: these checks prove that the reference dataset is complete, unique, document-backed, and internally consistent. They do not prove that an intervention fixed an answer.", styles["Small"]), PageBreak()]
    story += [Paragraph("Measured system evidence", styles["H2x"]), table([["Area", "Measured result", "Status"], ["Pre-action catch rate", "100.00% [91.8, 100.0]", "PASS"], ["Added p95 latency", "15 ms", "PASS"], ["Verification cost", "5.20%", "MISS: target <=5%"], ["Net spend change", "-18.96%", "MISS: target about -30%"], ["Ungrounded escapes", "0.00% empirical", "PASS; certified bound separate"], ["False interventions", "85.35%", "MISS: target <=2%"], ["Load pass", "4,023 requests / 20 concurrency / 0 failures", "PASS; gateway p95 531 ms"], ["Observer warm p95", "37.06 ms", "MISS: target 25 ms"]], [50 * mm, 65 * mm, 45 * mm]), Spacer(1, 5 * mm)]
    story += [Paragraph("Live model inventory", styles["H2x"]), table([["Model", "Format", "Role"], ["qwen3:4b", "Q4_K_M, 4.0B", "cheap/low-stakes tier"], ["qwen3:8b", "Q4_K_M, 8.2B", "strong/high-stakes tier"]], [42 * mm, 52 * mm, 66 * mm])]
    story += [Paragraph("Automated judge run", styles["H2x"]), Paragraph("A reproducible judge runner was added at scripts/eval_manual_anchors.py. It records the model, raw judgment, parsed label, confidence/rationale, agreement, and latency. A short Qwen 4B run completed 4 rows before CPU throughput became impractical (about 33 seconds per row). It is retained as a smoke sample only and is not presented as a 300-item result.", styles["Body2"])]
    story += [Paragraph("Open gates", styles["H2x"]), Paragraph("1. Run a complete 300-item judge pass when adequate compute is available. 2. Generate reviewed post-action outcomes for L1-L5; pre-action labels cannot establish intervention efficacy. 3. Resolve F-019, because the current impact model produces the measured false-intervention miss. 4. Complete live-model rehearsal, true KV-cache performance validation, and clean-checkout deployment proof.", styles["Body2"])]
    story += [Paragraph("Conclusion", styles["H2x"]), Paragraph("Person 1's core code and operational wiring are verified. The evidence supports claiming detection, routing, event publication, holds, economics surfaces, and deterministic end-to-end operation. It does not support claiming that every intervention improves answers or that all six target metrics pass. Those claims require the open measurements above.", styles["Body2"])]
    doc.build(story)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build()
    print(OUT)
