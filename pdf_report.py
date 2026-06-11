from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ModuleNotFoundError:
    colors = None
    TA_LEFT = 0
    A4 = None
    landscape = None
    ParagraphStyle = None
    getSampleStyleSheet = None
    mm = 1
    PageBreak = None
    Paragraph = None
    SimpleDocTemplate = None
    Spacer = None
    Table = None
    TableStyle = None


def _fmt_num(value: Any, decimals: int = 2, blank: str = "-") -> str:
    if pd.isna(value):
        return blank
    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return str(value)


def _fmt_int(value: Any, blank: str = "-") -> str:
    if pd.isna(value):
        return blank
    try:
        return f"{int(round(float(value))):,}"
    except Exception:
        return str(value)


def _fmt_dt(value: Any) -> str:
    if pd.isna(value):
        return "-"
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return str(value)
    return ts.strftime("%d %b %H:%M")


def _table_style(header_bg=None, font_size: int = 7):
    if header_bg is None:
        header_bg = colors.HexColor("#17324d")
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("LEADING", (0, 0), (-1, -1), font_size + 1),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fb")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _add_page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.drawRightString(282 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _points(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    side = df.get("side", pd.Series(dtype=str)).astype(str).str.upper()
    entry = pd.to_numeric(df.get("entry_price"), errors="coerce")
    exit_ = pd.to_numeric(df.get("exit_price"), errors="coerce")
    return pd.Series(
        [
            (x - e) if s == "BUY" else (e - x) if s == "SELL" else pd.NA
            for s, e, x in zip(side, entry, exit_, strict=False)
        ],
        index=df.index,
        dtype="float64",
    )


def _profit_factor(points: pd.Series) -> float:
    if points.empty:
        return 0.0
    wins = points[points > 0].sum()
    losses = points[points < 0].sum()
    if losses == 0:
        return 999999.0 if wins > 0 else 0.0
    return float(wins / abs(losses))


def _display_name(filename: Any, fallback: str) -> str:
    text = str(filename or "").strip()
    if not text:
        return fallback
    return Path(text).name


def _with_aud_cost(matched: pd.DataFrame, aud_rate_map: dict[str, float]) -> pd.DataFrame:
    out = matched.copy()
    if out.empty:
        return out
    tgt_month = pd.to_datetime(out.get("mt5_entry_time_utc"), errors="coerce").dt.to_period("M").astype("string")
    out["tgt_month"] = tgt_month
    if aud_rate_map:
        out["aud_monthly_rate"] = pd.to_numeric(tgt_month.map(aud_rate_map), errors="coerce")
        out["mt5_actual_cost_aud"] = (
            pd.to_numeric(out.get("points_delta"), errors="coerce")
            * pd.to_numeric(out.get("mt5_qty"), errors="coerce")
            * pd.to_numeric(out.get("aud_monthly_rate"), errors="coerce")
        )
    return out


def generate_post_trade_pdf(data: dict, mt5_timezone: str) -> tuple[str, bytes]:
    if colors is None:
        raise RuntimeError("PDF export requires the 'reportlab' package. Install dependencies from requirements.txt.")

    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    matched = _with_aud_cost(data.get("matched", pd.DataFrame()), settings.get("aud_rate_map", {}))
    source = data.get("source_norm", data.get("nt_norm", pd.DataFrame()))
    target = data.get("target_norm", data.get("mt5_norm", pd.DataFrame()))
    unmatched_source = data.get("unmatched_source", data.get("unmatched_nt", pd.DataFrame()))
    unmatched_target = data.get("unmatched_target", data.get("unmatched_mt5", pd.DataFrame()))

    left_name = _display_name(settings.get("left_filename"), "source")
    right_name = _display_name(settings.get("right_filename"), "target")
    start = settings.get("date_filter_start", "-")
    end = settings.get("date_filter_end", "-")
    symbol_filter = settings.get("symbol_filter") or []
    if isinstance(symbol_filter, str):
        symbol_filter = [symbol_filter]
    symbol_text = ", ".join(symbol_filter) if symbol_filter else "All instruments"

    source_points = _points(source)
    target_points = _points(target)
    matched_source_points = pd.to_numeric(matched.get("nt_points"), errors="coerce")
    matched_target_points = pd.to_numeric(matched.get("mt5_points"), errors="coerce")
    points_delta = pd.to_numeric(matched.get("points_delta"), errors="coerce")
    entry_slip = pd.to_numeric(matched.get("model_to_live_entry_difference_pts"), errors="coerce")
    exit_slip = pd.to_numeric(matched.get("model_to_live_exit_difference_pts"), errors="coerce")
    net_profit_delta = pd.to_numeric(matched.get("net_profit_delta"), errors="coerce")
    aud_cost = pd.to_numeric(matched.get("mt5_actual_cost_aud"), errors="coerce")
    aud_populated = int(aud_cost.notna().sum()) if not matched.empty else 0

    symbols = sorted(matched.get("symbol_norm", pd.Series(dtype=str)).dropna().astype(str).str.upper().unique().tolist())
    buys = int((matched.get("side", pd.Series(dtype=str)).astype(str).str.upper() == "BUY").sum()) if not matched.empty else 0
    sells = int((matched.get("side", pd.Series(dtype=str)).astype(str).str.upper() == "SELL").sum()) if not matched.empty else 0

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
        title="PTA Post-Trade Comparison Report",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            alignment=TA_LEFT,
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#102a43"),
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#344054"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontSize=10,
            leading=12,
            textColor=colors.HexColor("#17324d"),
            spaceBefore=8,
            spaceAfter=4,
        )
    )

    source_total = float(source_points.sum()) if not source_points.empty else 0.0
    target_total = float(target_points.sum()) if not target_points.empty else 0.0
    full_avg_diff = float(target_points.mean() - source_points.mean()) if not source_points.empty and not target_points.empty else 0.0
    pf_diff = _profit_factor(target_points) - _profit_factor(source_points)

    story = [
        Paragraph("PTA Post-Trade Comparison Report", styles["ReportTitle"]),
        Paragraph(f"Backtest vs real trades | Source: {left_name} | Target: {right_name}", styles["Small"]),
        Paragraph(f"Date range: {start} .. {end} | Instrument filter: {symbol_text} | Matched symbols: {', '.join(symbols) or '-'}", styles["Small"]),
        Spacer(1, 5),
    ]

    kpi_rows = [
        ["Metric", "Value", "Read"],
        ["Source trades", _fmt_int(len(source)), "Backtest / model trades after filters"],
        ["Target trades", _fmt_int(len(target)), "Real/live trades after filters"],
        ["Matched trades", _fmt_int(len(matched)), f"{buys} BUY / {sells} SELL"],
        ["Missed trades", _fmt_int(len(unmatched_source) + len(unmatched_target)), "Unmatched trades requiring review"],
        ["Matched points delta", _fmt_num(points_delta.sum()), "Target minus source on matched trades"],
        ["Full-period avg point diff", _fmt_num(full_avg_diff, 4), "Target avg points minus source avg points"],
        ["Profit factor difference", _fmt_num(pf_diff, 4), "Target PF minus source PF"],
        ["Matched-only PnL delta", _fmt_num(net_profit_delta.sum()), "Target PnL less source PnL on matched pairs"],
        ["AUD actual cost", _fmt_num(aud_cost.sum()), f"Calculated for {aud_populated}/{len(matched)} matched trades"],
    ]
    kpi_table = Table(kpi_rows, colWidths=[45 * mm, 32 * mm, 102 * mm], repeatRows=1)
    kpi_table.setStyle(_table_style(font_size=8))

    pnl_rows = [
        ["Measure", "Source", "Target", "Target - Source"],
        ["Total points after filters", _fmt_num(source_total), _fmt_num(target_total), _fmt_num(target_total - source_total)],
        ["Total points on matched rows", _fmt_num(matched_source_points.sum()), _fmt_num(matched_target_points.sum()), _fmt_num(points_delta.sum())],
        ["Average entry slip (pts)", "", "", _fmt_num(entry_slip.mean(), 4)],
        ["Average exit slip (pts)", "", "", _fmt_num(exit_slip.mean(), 4)],
    ]
    pnl_table = Table(pnl_rows, colWidths=[55 * mm, 30 * mm, 30 * mm, 35 * mm], repeatRows=1)
    pnl_table.setStyle(_table_style(colors.HexColor("#325d42"), font_size=8))

    def movers_table(df: pd.DataFrame, title: str) -> Table:
        rows = [[title, "Side", "Entry", "Src pts", "Tgt pts", "Delta", "Entry slip", "Exit slip"]]
        for _, row in df.iterrows():
            rows.append(
                [
                    str(row.get("symbol_norm", "")),
                    str(row.get("side", "")),
                    _fmt_dt(row.get("nt_entry_time_utc")),
                    _fmt_num(row.get("nt_points")),
                    _fmt_num(row.get("mt5_points")),
                    _fmt_num(row.get("points_delta")),
                    _fmt_num(row.get("model_to_live_entry_difference_pts")),
                    _fmt_num(row.get("model_to_live_exit_difference_pts")),
                ]
            )
        table = Table(rows, colWidths=[22 * mm, 15 * mm, 26 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm, 20 * mm], repeatRows=1)
        table.setStyle(_table_style(colors.HexColor("#5b2b2b") if "Worst" in title else colors.HexColor("#29415e"), font_size=7))
        return table

    worst = matched.nsmallest(5, "points_delta") if "points_delta" in matched.columns and not matched.empty else matched.head(0)
    best = matched.nlargest(5, "points_delta") if "points_delta" in matched.columns and not matched.empty else matched.head(0)

    notes = [
        "Positive slip means the target/live fill was better than the source/backtest fill.",
        "AUD actual cost uses matched trade points delta x target quantity x configured monthly AUD rate.",
        f"Timezone note: target timestamps use {mt5_timezone}; source timezone depends on importer.",
    ]

    story.extend(
        [
            Paragraph("Executive Summary", styles["Section"]),
            kpi_table,
            Spacer(1, 5),
            pnl_table,
            Spacer(1, 5),
            Paragraph("Largest Positive / Negative Matched Differences", styles["Section"]),
            Table([[movers_table(worst, "Worst deltas"), movers_table(best, "Best deltas")]], colWidths=[135 * mm, 135 * mm]),
            Spacer(1, 5),
            Paragraph("Notes", styles["Section"]),
            *[Paragraph(f"- {note}", styles["Small"]) for note in notes],
            PageBreak(),
            Paragraph("All Matched Trades", styles["ReportTitle"]),
            Paragraph("Compact row-level view of matched trades. Delta is target points minus source points.", styles["Small"]),
            Spacer(1, 5),
        ]
    )

    trade_rows = [["#", "Side", "Src entry", "Tgt entry", "Src pts", "Tgt pts", "Delta", "Entry slip", "Exit slip", "Entry dt", "Exit dt"]]
    compact = matched.sort_values("nt_entry_time_utc").reset_index(drop=True) if "nt_entry_time_utc" in matched.columns else matched.reset_index(drop=True)
    for idx, row in compact.iterrows():
        trade_rows.append(
            [
                str(idx + 1),
                str(row.get("side", "")),
                _fmt_dt(row.get("nt_entry_time_utc")),
                _fmt_dt(row.get("mt5_entry_time_utc")),
                _fmt_num(row.get("nt_points")),
                _fmt_num(row.get("mt5_points")),
                _fmt_num(row.get("points_delta")),
                _fmt_num(row.get("model_to_live_entry_difference_pts")),
                _fmt_num(row.get("model_to_live_exit_difference_pts")),
                _fmt_int(row.get("entry_time_delta_seconds")),
                _fmt_int(row.get("exit_time_delta_seconds")),
            ]
        )
    trade_table = Table(
        trade_rows,
        colWidths=[8 * mm, 13 * mm, 24 * mm, 24 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm, 16 * mm, 16 * mm],
        repeatRows=1,
    )
    trade_table.setStyle(_table_style(font_size=6))
    story.append(trade_table)

    doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
    pdf_name = f"{Path(left_name).stem}_vs_{Path(right_name).stem}_pta_report.pdf"
    return pdf_name, buffer.getvalue()
