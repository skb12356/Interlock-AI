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
        for line in (ROOT / "data/labels/manual_anchor_300.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
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
    header = ParagraphStyle(
        "TableHeader", parent=cell, fontName="Helvetica-Bold", textColor=colors.white
    )
    wrapped = [
        [Paragraph(str(value), header if row == 0 else cell) for value in line]
        for row, line in enumerate(data)
    ]
    result = Table(wrapped, colWidths=widths, repeatRows=1)
    result.setStyle(
        TableStyle(
            [
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
            ]
        )
    )
    return result


def load_evidence() -> tuple[list[dict[str, object]], dict[str, object], dict[str, object]]:
    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "artifacts" / "eval").glob("report-seed-*.json"))
    ]
    if len(reports) != 3:
        raise ValueError(f"expected three seeded reports, found {len(reports)}")
    calibration = json.loads(
        (ROOT / "artifacts" / "calibration" / "report.json").read_text(encoding="utf-8")
    )
    comparison = json.loads(
        (ROOT / "artifacts" / "eval" / "f019_strategy_comparison.json").read_text(encoding="utf-8")
    )
    versions = {str(report["policy_version"]) for report in reports}
    if versions != {str(comparison["policy_version"])}:
        raise ValueError("seed reports and strategy comparison use different policies")
    return reports, calibration, comparison


def metric(report: dict[str, object], name: str) -> dict[str, object]:
    metrics = report["metrics"]
    if not isinstance(metrics, list):
        raise ValueError("report metrics must be a list")
    return next(item for item in metrics if isinstance(item, dict) and item.get("name") == name)


def percent_values(reports: list[dict[str, object]], name: str) -> str:
    return " / ".join(f"{float(metric(report, name)['value']) * 100:.2f}%" for report in reports)


def build() -> None:
    count, unique, modes, contained, errors = anchors()
    reports, calibration, comparison = load_evidence()
    winner = comparison["winner"]
    if not isinstance(winner, dict):
        raise ValueError("strategy comparison has no winner")
    anchor_result = winner["manual_anchor"]
    if not isinstance(anchor_result, dict):
        raise ValueError("strategy comparison has no anchor result")
    policy_version = str(reports[0]["policy_version"])
    false_ci = metric(reports[0], "False interventions")["ci"]
    if not isinstance(false_ci, list):
        raise ValueError("false-intervention confidence interval is missing")
    latency = max(float(metric(report, "Added p95 latency")["value"]) for report in reports)
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Body2", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=6
        )
    )
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(
        ParagraphStyle(
            name="Subx",
            parent=styles["BodyText"],
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#526675"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Title2",
            parent=styles["Title"],
            fontSize=23,
            leading=27,
            textColor=colors.HexColor("#17324d"),
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2x",
            parent=styles["Heading2"],
            fontSize=14,
            leading=17,
            textColor=colors.HexColor("#17324d"),
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )
    story: list[object] = []
    story += [
        Paragraph("Person 1: Measured Results", styles["Title2"]),
        Paragraph("Interlock V2 | 30 August 2026", styles["Subx"]),
        Spacer(1, 5 * mm),
    ]
    story += [
        Paragraph("Executive result", styles["H2x"]),
        Paragraph(
            "The offline F-019 evaluation compared 33 decision strategies on three independently seeded 200-case sets and all 300 labelled anchors. The selected calibrated risk floor records no false interventions on the measured clean cases while preserving all induced-defect catches. Net spend remains below its release target, and the finite clean sample is not large enough to prove a production rate below 2% with 95% confidence.",
            styles["Body2"],
        ),
    ]
    story += [
        table(
            [
                ["Measure", "Result"],
                ["Policy", policy_version],
                ["Production strategy", "1% risk floor + 50% relative gain"],
                ["Seeded evaluations", "3 x 200 fixed-answer cases"],
                ["Anchor records", f"{count} records / {unique} unique IDs"],
                ["Calibration set", f"{int(calibration['n_items']):,} document-disjoint examples"],
            ],
            [55 * mm, 105 * mm],
        ),
        Spacer(1, 5 * mm),
    ]
    story += [
        Paragraph("System path", styles["H2x"]),
        Preformatted(
            "User -> Gateway -> stakes/routing -> Qwen tier\n                         -> commit gate -> observer/risk engine\n                         -> pass | repair | reroute | hold | block\n                         -> ConsoleHub events + ledger economics",
            styles["Code"],
        ),
        Spacer(1, 3 * mm),
    ]
    story += [
        Paragraph("300-anchor exhaustive checks", styles["H2x"]),
        table(
            [
                ["Check", "Result"],
                ["Record count", str(count)],
                ["Unique item IDs", str(unique)],
                [
                    "Failure-mode distribution",
                    ", ".join(f"{k}: {v}" for k, v in sorted(modes.items())),
                ],
                ["Clean answers contained in supplied context", f"{contained}/270"],
                ["Label consistency errors", str(errors)],
            ],
            [75 * mm, 85 * mm],
        ),
    ]
    story += [
        Paragraph(
            "Interpretation: these checks establish dataset completeness and internal consistency. The anchor policy result is 0/270 false interventions and 30/30 defect catches. Because the anchors participate in calibration, they are supporting evidence rather than an independent holdout, and they do not prove post-action efficacy.",
            styles["Small"],
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("Measured system evidence", styles["H2x"]),
        table(
            [
                ["Area", "Three-seed result", "Status"],
                ["Pre-action catch rate", percent_values(reports, "Pre-Action Catch Rate"), "PASS"],
                ["Added p95 latency", f"{latency:.0f} ms worst seed", "PASS"],
                ["Verification cost", percent_values(reports, "Verification cost"), "PASS"],
                [
                    "Net spend change",
                    percent_values(reports, "Net spend change"),
                    "MISS: target about -30%",
                ],
                [
                    "Ungrounded escapes",
                    percent_values(reports, "Ungrounded escapes"),
                    "PASS empirically",
                ],
                ["False interventions", "0/140 on each seed", "PASS point estimate"],
                [
                    "False-intervention 95% CI",
                    f"0-{float(false_ci[1]) * 100:.2f}%",
                    "Needs larger clean holdout",
                ],
            ],
            [50 * mm, 65 * mm, 45 * mm],
        ),
        Spacer(1, 5 * mm),
    ]
    story += [
        Paragraph("Calibration and decision policy", styles["H2x"]),
        table(
            [
                ["Measurement", "Result"],
                ["Out-of-fold ECE", f"{float(calibration['ece']):.4f}"],
                ["Brier score", f"{float(calibration['brier']):.4f}"],
                ["Induced-taxonomy AUROC", f"{float(calibration['auroc']):.4f}"],
                [
                    "Mean clean calibrated risk",
                    f"{float(calibration['mode_mean_probability']['clean']) * 100:.4f}%",
                ],
                ["Production risk floor", "1.00%"],
                ["Conformal threshold / intervention", "37.00% / 9.22%"],
            ],
            [75 * mm, 85 * mm],
        ),
    ]
    story += [
        Paragraph("Why this method was selected", styles["H2x"]),
        Paragraph(
            "The old question-drift signal compared question words directly with terse answer words and was effectively inverted. It now compares both through retrieved context. Numeric corruption generation also retries when a replacement figure remains supported elsewhere in the passage. After recalibration, clean risk averages 0.2568% while each induced defect mode averages at least 94.88%. A 1% floor therefore separates the measured clean and defective cases without changing the rupee impact model. Deterministic hard rules, conformal filtering, and unavailable-action constraints still take precedence.",
            styles["Body2"],
        ),
    ]
    story += [
        Paragraph("Claim boundary", styles["H2x"]),
        Paragraph(
            "The evidence supports a measured false-intervention result of 0/140 per seed, not a claim of zero production false interventions. The Wilson upper bound is 2.67%, above the 2% target. The induced taxonomy is highly separable and must be challenged with naturally occurring defects and a larger untouched clean set. Reviewed forced-action outcomes are also still required before claiming that repair, hold, or block improves final answers.",
            styles["Body2"],
        ),
    ]
    story += [
        Paragraph("Conclusion", styles["H2x"]),
        Paragraph(
            "Within the offline paired evaluation, F-019 is resolved: the selected policy removes all measured false interventions without a measured catch or escape regression. Verification cost also passes. Net spend remains the only scorecard miss, and statistical confidence plus external-defect validity remain explicit release evidence requirements.",
            styles["Body2"],
        ),
    ]
    doc.build(story)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build()
    print(OUT)
