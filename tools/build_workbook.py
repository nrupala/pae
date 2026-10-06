#!/usr/bin/env python3
# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Generate pae_portfolio_analytics.xlsx from scratch.

Idempotent: deletes the existing workbook (if any) and regenerates it.
All metrics are REAL Excel formulas (Google-Sheets compatible), cross-checked
against the PAE engine:

  * risk metrics  -> engine/src/risk/metrics.rs
  * stress shocks -> engine/src/risk/stress.rs
  * factor model  -> analytics/pae/models/factor.py (simplified attribution;
                     full OLS lives in the Python engine)

Because openpyxl does not evaluate formulas, this script computes the expected
result of every formula cell with pure-Python mirrors of the engine formulas
and injects them as cached <v> values into the .xlsx XML. Excel shows correct
numbers on first open; Google Sheets recomputes everything on import anyway.

Run:  python3 tools/build_workbook.py
Verify: python3 tools/verify_workbook.py
"""

from __future__ import annotations

import math
import os
import statistics
import zipfile
import xml.etree.ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "pae_portfolio_analytics.xlsx")

# ---------------------------------------------------------------------------
# Sample data (fictional, clearly labeled SAMPLE in the workbook)
# ---------------------------------------------------------------------------

# (symbol, asset_class, quantity, price_cad, cost_basis_cad, yield_pct)
SAMPLE_HOLDINGS = [
    ("RY",    "Equity",        100,   150.00, 12000.0, 0.038),
    ("VCN",   "Equity",        200,    45.00,  8000.0, 0.029),
    ("XEF",   "Equity",        150,    38.50,  5200.0, 0.027),
    ("VAB",   "Fixed Income",  300,    26.00,  8100.0, 0.034),
    ("ZPR",   "Preferred",     250,    10.50,  2800.0, 0.051),
    ("CASH",  "Cash",            1, 12000.00, 12000.0, 0.045),
    ("VRE",   "Real Estate",   180,    22.00,  3600.0, 0.042),
    ("CGL.C", "Commodity",     120,    28.00,  3000.0, 0.000),
]

# 24 fictional monthly portfolio returns, Jan 2024 - Dec 2025
SAMPLE_RETURNS = [
    ("2024-01",  0.021), ("2024-02", -0.014), ("2024-03",  0.032),
    ("2024-04",  0.008), ("2024-05", -0.027), ("2024-06",  0.015),
    ("2024-07",  0.044), ("2024-08", -0.009), ("2024-09",  0.012),
    ("2024-10", -0.038), ("2024-11",  0.025), ("2024-12",  0.018),
    ("2025-01", -0.012), ("2025-02",  0.029), ("2025-03",  0.007),
    ("2025-04", -0.021), ("2025-05",  0.036), ("2025-06",  0.011),
    ("2025-07", -0.005), ("2025-08",  0.023), ("2025-09", -0.031),
    ("2025-10",  0.017), ("2025-11",  0.009), ("2025-12",  0.014),
]

RISK_FREE_ANNUAL = 0.04  # 4% annual, input cell on RISK sheet

# (factor, exposure beta [manual input], factor return [manual input])
SAMPLE_FACTORS = [
    ("Market",    0.95, 0.080),
    ("Size",      0.20, 0.020),
    ("Value",     0.35, 0.035),
    ("Momentum", -0.10, 0.040),
    ("Quality",   0.25, 0.025),
]
SAMPLE_ALPHA = 0.005  # manual residual/alpha input

# Stress shocks per asset class, mirrored from engine/src/risk/stress.rs
# (get_scenario_shocks: gfc_2008, covid_2020, rate_shock_2022).
# Cash has no shock in the engine; the sheet models it as 0% (documented).
SHOCK_CLASSES = ["Equity", "Fixed Income", "Commodity",
                 "Real Estate", "Preferred", "Cash"]
SHOCKS = {
    "GFC 2008 (2008-style)":     [-0.50,  0.05, -0.35, -0.30, -0.25, 0.00],
    "COVID 2020 (2020-style)":   [-0.34,  0.02, -0.25, -0.15, -0.20, 0.00],
    "Rate shock (2022-style)":   [-0.25, -0.15,  0.10, -0.10, -0.15, 0.00],
}


# ---------------------------------------------------------------------------
# Pure-Python mirrors of engine/src/risk/metrics.rs (for cached values +
# independent verification). Keep these EXACTLY aligned with metrics.rs.
# ---------------------------------------------------------------------------

def m_volatility(returns):
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns)  # Bessel's correction, like metrics.rs


def m_sharpe(returns, risk_free_annual):
    # metrics.rs: (mean - rf_annual/12) / volatility  (monthly, not annualized)
    if len(returns) < 2:
        return 0.0
    vol = m_volatility(returns)
    if vol == 0.0:
        return 0.0
    return (statistics.mean(returns) - risk_free_annual / 12.0) / vol


def m_sortino(returns, risk_free_annual):
    # metrics.rs: downside = RMS of (r - rf/12)^2 over r < rf/12 ONLY
    if len(returns) < 2:
        return 0.0
    rf_p = risk_free_annual / 12.0
    mean = statistics.mean(returns)
    dd = [(r - rf_p) ** 2 for r in returns if r < rf_p]
    if not dd:
        return 0.0
    ddev = math.sqrt(sum(dd) / len(dd))
    if ddev == 0.0:
        return 0.0
    return (mean - rf_p) / ddev


def m_max_drawdown(returns):
    # metrics.rs: peak-to-trough on cumulative wealth starting at 1.0
    if not returns:
        return 0.0
    cum = [1.0]
    for r in returns:
        cum.append(cum[-1] * (1.0 + r))
    peak, mdd = cum[0], 0.0
    for v in cum:
        peak = max(peak, v)
        if peak > 0.0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def m_var(returns, alpha=0.05):
    # metrics.rs: -sorted[floor(alpha * n)] (rank-based, NOT interpolated)
    if not returns:
        return 0.0
    s = sorted(returns)
    idx = min(int(math.floor(alpha * len(s))), len(s) - 1)
    return -s[idx]


def m_cvar(returns, alpha=0.05):
    # metrics.rs: -mean(sorted[:max(1, floor(alpha*n))])
    if not returns:
        return 0.0
    s = sorted(returns)
    cut = max(1, int(math.floor(alpha * len(s))))
    cut = min(cut, len(s))
    tail = s[:cut]
    return -sum(tail) / len(tail)


def m_total_return(returns):
    tot = 1.0
    for r in returns:
        tot *= (1.0 + r)
    return tot - 1.0


def m_annualized_return(returns, periods_per_year=12):
    if not returns or periods_per_year == 0:
        return 0.0
    years = len(returns) / periods_per_year
    return (1.0 + m_total_return(returns)) ** (1.0 / years) - 1.0


# ---------------------------------------------------------------------------
# Workbook construction helpers
# ---------------------------------------------------------------------------

TITLE_FONT = Font(size=16, bold=True, color="1F4E5F")
SUB_FONT = Font(size=10, italic=True, color="5A6C7D")
HDR_FONT = Font(size=11, bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="1F4E5F")
INPUT_FILL = PatternFill("solid", fgColor="FFF8E1")  # light amber = user input
NOTE_FONT = Font(size=9, italic=True, color="5A6C7D")
WRAP = Alignment(wrap_text=True, vertical="top")

FMT_CAD = '#,##0.00'
FMT_PCT = '0.00%'
FMT_NUM = '0.00'
FMT_QTY = '#,##0'

# cache[sheet_name][cell_coord] = expected numeric value of the formula cell
cache: dict[str, dict[str, float]] = {}


def fcell(ws, coord, formula, value, number_format=None):
    """Write a formula cell and record its expected (cached) value."""
    c = ws[coord]
    c.value = formula
    if number_format:
        c.number_format = number_format
    cache.setdefault(ws.title, {})[coord] = value
    return c


def input_cell(ws, coord, value, number_format=None):
    c = ws[coord]
    c.value = value
    c.fill = INPUT_FILL
    if number_format:
        c.number_format = number_format
    return c


def header_row(ws, row, cols):
    for col, text in cols:
        c = ws.cell(row=row, column=col, value=text)
        c.font = HDR_FONT
        c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[row].height = 30


def title_block(ws, title, subtitle, width_cols):
    ws.merge_cells(start_row=1, start_column=1, end_row=1,
                   end_column=width_cols)
    t = ws.cell(row=1, column=1, value=title)
    t.font = TITLE_FONT
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2,
                   end_column=width_cols)
    s = ws.cell(row=2, column=1, value=subtitle)
    s.font = SUB_FONT
    s.alignment = WRAP
    ws.row_dimensions[2].height = 30


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------

def build_disclosure(wb):
    ws = wb.active
    ws.title = "DISCLOSURE"
    ws.sheet_properties.tabColor = "C0392B"
    ws.column_dimensions["A"].width = 110

    ws.merge_cells("A1:A1")
    t = ws["A1"]
    t.value = "PAE Portfolio Analytics \u2014 Spreadsheet Companion"
    t.font = Font(size=18, bold=True, color="1F4E5F")
    ws.row_dimensions[1].height = 30

    paras = [
        ("What this is",
         "A standalone educational companion to the PAE (Portfolio Analytics "
         "Engine) framework. It implements PAE's core analytics \u2014 risk "
         "metrics, factor attribution, scenario stress tests \u2014 as "
         "transparent spreadsheet formulas you can inspect, extend, and "
         "import into Google Sheets."),
        ("NO ZERO-KNOWLEDGE ENCRYPTION \u2014 READ THIS FIRST",
         "Unlike the PAE application, this spreadsheet is NOT encrypted and "
         "provides NO zero-knowledge guarantees. Anything you type into this "
         "workbook (holdings, returns, notes) is stored in PLAIN TEXT inside "
         "this file. Do NOT enter passwords, account numbers, or any data you "
         "would not want readable by anyone who obtains this file."),
        ("Educational analytics only",
         "This tool calculates; YOU decide. Nothing in this workbook is "
         "investment advice, and nothing here is a buy or sell "
         "recommendation. Past returns do not predict future results."),
        ("Sample data is fictional",
         "All pre-filled holdings and returns are fictional SAMPLE data, "
         "clearly labeled as such. Replace them with your own figures before "
         "drawing any conclusions."),
        ("Formula provenance",
         "Risk formulas implement engine/src/risk/metrics.rs. Stress shocks "
         "mirror engine/src/risk/stress.rs. The FACTORS sheet is a simplified "
         "attribution (exposure \u00d7 factor return); the full OLS factor "
         "decomposition (betas, t-stats, R\u00b2) runs in the PAE Python "
         "engine at analytics/pae/models/factor.py."),
        ("Regeneration",
         "Generated 2026-10-05 by tools/build_workbook.py (idempotent \u2014 "
         "safe to delete and regenerate at any time). Formulas use only "
         "Google-Sheets-compatible functions."),
    ]
    row = 3
    for heading, body in paras:
        h = ws.cell(row=row, column=1, value=heading)
        h.font = Font(size=12, bold=True,
                      color="C0392B" if "ENCRYPTION" in heading else "1F4E5F")
        h.alignment = WRAP
        ws.row_dimensions[row].height = 22
        row += 1
        b = ws.cell(row=row, column=1, value=body)
        b.font = Font(size=11, color="2C3E50")
        b.alignment = WRAP
        ws.row_dimensions[row].height = 52
        row += 2
    ws.freeze_panes = "A3"
    ws.sheet_view.showGridLines = False


def build_holdings(wb):
    ws = wb.create_sheet("INPUT_Holdings")
    ws.sheet_properties.tabColor = "1F4E5F"
    title_block(ws, "Holdings \u2014 inputs (SAMPLE data)",
                "Amber cells are inputs. Replace the sample holdings with "
                "your own. Market Value is calculated (Quantity \u00d7 Price).",
                7)
    for col, w in zip("ABCDEFG", [10, 15, 12, 13, 17, 17, 10]):
        ws.column_dimensions[col].width = w
    header_row(ws, 3, [(1, "Symbol"), (2, "Asset Class"), (3, "Quantity"),
                       (4, "Price (CAD)"), (5, "Market Value (CAD)"),
                       (6, "Cost Basis (CAD)"), (7, "Yield %")])
    r = 4
    for sym, cls, qty, price, cost, yld in SAMPLE_HOLDINGS:
        ws.cell(row=r, column=1, value=sym)
        ws.cell(row=r, column=2, value=cls)
        input_cell(ws, f"C{r}", qty, FMT_QTY)
        input_cell(ws, f"D{r}", price, FMT_CAD)
        fcell(ws, f"E{r}", f"=C{r}*D{r}", qty * price, FMT_CAD)
        input_cell(ws, f"F{r}", cost, FMT_CAD)
        input_cell(ws, f"G{r}", yld, FMT_PCT)
        r += 1
    # totals row
    ws.cell(row=r, column=1, value="TOTAL").font = Font(bold=True)
    tot_mv = sum(q * p for _, _, q, p, _, _ in SAMPLE_HOLDINGS)
    tot_cb = sum(c for _, _, _, _, c, _ in SAMPLE_HOLDINGS)
    fcell(ws, f"E{r}", f"=SUM(E4:E{r - 1})", tot_mv, FMT_CAD)
    fcell(ws, f"F{r}", f"=SUM(F4:F{r - 1})", tot_cb, FMT_CAD)
    w_yield = sum(q * p * y for _, _, q, p, _, y in SAMPLE_HOLDINGS) / tot_mv
    fcell(ws, f"G{r}",
          f"=SUMPRODUCT(E4:E{r - 1},G4:G{r - 1})/E{r}", w_yield, FMT_PCT)
    for col in "EFG":
        ws[f"{col}{r}"].font = Font(bold=True)
    ws.cell(row=r + 2, column=1,
            value="Note: asset-class names must match the SCENARIOS sheet "
                  "exactly (Equity / Fixed Income / Commodity / Real Estate "
                  "/ Preferred / Cash).").font = NOTE_FONT
    ws.merge_cells(f"A{r + 2}:G{r + 2}")
    ws.freeze_panes = "A4"


def build_returns(wb):
    ws = wb.create_sheet("RETURNS")
    ws.sheet_properties.tabColor = "2980B9"
    title_block(ws, "Monthly portfolio returns \u2014 SAMPLE data",
                "Fictional 24-month return series (Jan 2024 \u2013 Dec 2025). "
                "Columns D\u2013G are calculation helpers for the RISK sheet. "
                "Replace column C with your own monthly returns.",
                7)
    for col, w in zip("ABCDEFG", [8, 12, 12, 14, 14, 12, 15]):
        ws.column_dimensions[col].width = w
    header_row(ws, 3, [(1, "Mo"), (2, "Date"), (3, "Return"),
                       (4, "Wealth index"), (5, "Running peak"),
                       (6, "Drawdown"), (7, "Sorted returns")])
    rets = [x[1] for x in SAMPLE_RETURNS]
    # helper-column expected values (mirror of the Excel formulas)
    wealth, peak, dd = [1.0], [], []
    cum = 1.0
    pk = 0.0
    for x in rets:
        cum *= (1.0 + x)
        wealth.append(cum)
        pk = max(pk, cum)
        dd.append((pk - cum) / pk if pk > 0 else 0.0)
    srt = sorted(rets)
    r = 4
    for i, (label, x) in enumerate(SAMPLE_RETURNS):
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=label)
        input_cell(ws, f"C{r}", x, FMT_PCT)
        if r == 4:
            fcell(ws, f"D{r}", f"=1*(1+C{r})", wealth[i + 1], FMT_NUM)
            fcell(ws, f"E{r}", f"=D{r}", wealth[i + 1], FMT_NUM)
        else:
            fcell(ws, f"D{r}", f"=D{r - 1}*(1+C{r})", wealth[i + 1], FMT_NUM)
            fcell(ws, f"E{r}", f"=MAX(E{r - 1},D{r})", max(wealth[i + 1],
                  max(wealth[1:i + 2])), FMT_NUM)
        fcell(ws, f"F{r}", f"=IF(E{r}=0,0,(E{r}-D{r})/E{r})",
              dd[i], FMT_PCT)
        fcell(ws, f"G{r}", f"=SMALL($C$4:$C$27,ROWS($G$4:G{r}))",
              srt[i], FMT_PCT)
        r += 1
    ws.cell(row=r + 1, column=1,
            value="Helpers: Wealth compounds (1+r). Peak is the running "
                  "maximum of Wealth. Drawdown = (Peak \u2212 Wealth)/Peak. "
                  "Sorted returns feed the rank-based VaR/CVaR (PAE engine "
                  "definition: NOT interpolated).").font = NOTE_FONT
    ws.merge_cells(f"A{r + 1}:G{r + 1}")
    ws[f"A{r + 1}"].alignment = WRAP
    ws.row_dimensions[r + 1].height = 44
    ws.freeze_panes = "A4"


def build_risk(wb):
    ws = wb.create_sheet("RISK")
    ws.sheet_properties.tabColor = "8E44AD"
    title_block(ws, "Risk metrics",
                "Implements engine/src/risk/metrics.rs as spreadsheet "
                "formulas over RETURNS!C4:C27. Sharpe/Sortino use the PAE "
                "engine definitions (monthly excess return over monthly "
                "volatility/downside deviation).",
                3)
    for col, w in zip("ABC", [26, 18, 62]):
        ws.column_dimensions[col].width = w
    ws["A3"] = "Risk-free rate (annual)"
    ws["A3"].font = Font(bold=True)
    input_cell(ws, "B3", RISK_FREE_ANNUAL, FMT_PCT)
    ws["C3"] = "Input cell for the risk-free rate."
    ws["C3"].font = NOTE_FONT

    rets = [x[1] for x in SAMPLE_RETURNS]
    rf = RISK_FREE_ANNUAL
    rows = [
        ("Monthly volatility",
         "=STDEV(RETURNS!C4:C27)",
         m_volatility(rets), FMT_PCT,
         "STDEV.S equivalent \u2014 sample stdev (Bessel N\u22121), metrics.rs volatility()"),
        ("Annualized volatility",
         "=B5*SQRT(12)",
         m_volatility(rets) * math.sqrt(12), FMT_PCT,
         "Monthly vol \u00d7 \u221a12"),
        ("Mean monthly return",
         "=AVERAGE(RETURNS!C4:C27)",
         statistics.mean(rets), FMT_PCT,
         "Arithmetic mean of monthly returns"),
        ("Sharpe ratio",
         "=IF(B5=0,0,(B7-$B$3/12)/B5)",
         m_sharpe(rets, rf), FMT_NUM,
         "metrics.rs sharpe_ratio(): (mean \u2212 rf/12) / vol \u2014 monthly, NOT annualized"),
        ("Downside deviation",
         '=SQRT(SUMPRODUCT((RETURNS!C4:C27<$B$3/12)*(RETURNS!C4:C27-$B$3/12)^2)'
         '/COUNTIF(RETURNS!C4:C27,"<"&$B$3/12))',
         math.sqrt(sum((x - rf / 12) ** 2 for x in rets if x < rf / 12)
                   / sum(1 for x in rets if x < rf / 12)), FMT_PCT,
         "RMS of (r \u2212 rf/12) over months BELOW rf/12 only \u2014 metrics.rs sortino_ratio()"),
        ("Sortino ratio",
         '=IF(COUNTIF(RETURNS!C4:C27,"<"&$B$3/12)=0,0,(B7-$B$3/12)/B9)',
         m_sortino(rets, rf), FMT_NUM,
         "metrics.rs sortino_ratio(): (mean \u2212 rf/12) / downside dev; 0 if no downside months"),
        ("Max drawdown",
         "=MAX(RETURNS!F4:F27)",
         m_max_drawdown(rets), FMT_PCT,
         "Largest peak-to-trough fall of the wealth index \u2014 metrics.rs max_drawdown()"),
        ("VaR 95% (monthly)",
         "=-INDEX(RETURNS!G4:G27,INT(0.05*COUNT(RETURNS!C4:C27))+1)",
         m_var(rets), FMT_PCT,
         "metrics.rs value_at_risk(): rank-based \u2212sorted[\u230a0.05\u00b7n\u230b]; "
         "NOT the interpolated PERCENTILE \u2014 reported as positive loss"),
        ("CVaR 95% (monthly)",
         "=-AVERAGE(INDEX(RETURNS!G4:G27,2):INDEX(RETURNS!G4:G27,"
         "1+MAX(1,INT(0.05*COUNT(RETURNS!C4:C27)))))",
         m_cvar(rets), FMT_PCT,
         "metrics.rs conditional_var(): mean loss beyond the VaR rank, positive"),
        ("Total return (24 mo)",
         "=INDEX(RETURNS!D4:D27,COUNT(RETURNS!C4:C27))-1",
         m_total_return(rets), FMT_PCT,
         "Compounded: \u220f(1+r) \u2212 1 \u2014 metrics.rs total_return()"),
        ("Annualized return",
         "=(1+B14)^(12/COUNT(RETURNS!C4:C27))-1",
         m_annualized_return(rets), FMT_PCT,
         "(1+total)^(12/n) \u2212 1 \u2014 metrics.rs annualized_return()"),
    ]
    header_row(ws, 4, [(1, "Metric"), (2, "Value"), (3, "Definition (PAE engine)")])
    r = 5
    for label, formula, value, fmt, note in rows:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        fcell(ws, f"B{r}", formula, value, fmt)
        n = ws.cell(row=r, column=3, value=note)
        n.font = NOTE_FONT
        n.alignment = WRAP
        ws.row_dimensions[r].height = 30
        r += 1
    ws.freeze_panes = "A5"


def build_factors(wb):
    ws = wb.create_sheet("FACTORS")
    ws.sheet_properties.tabColor = "16A085"
    title_block(ws, "Factor attribution (simplified)",
                "Contribution = exposure \u00d7 factor return. Exposures and "
                "factor returns are MANUAL inputs (amber). The full OLS "
                "regression (betas, t-stats, R\u00b2, variance decomposition) "
                "runs in the PAE Python engine: analytics/pae/models/factor.py.",
                4)
    for col, w in zip("ABCD", [16, 16, 22, 18]):
        ws.column_dimensions[col].width = w
    header_row(ws, 3, [(1, "Factor"), (2, "Exposure \u03b2"),
                       (3, "Factor return (annual)"),
                       (4, "Contribution")])
    r = 4
    for name, beta, fret in SAMPLE_FACTORS:
        ws.cell(row=r, column=1, value=name)
        input_cell(ws, f"B{r}", beta, FMT_NUM)
        input_cell(ws, f"C{r}", fret, FMT_PCT)
        fcell(ws, f"D{r}", f"=B{r}*C{r}", beta * fret, FMT_PCT)
        r += 1
    ws.cell(row=r, column=1, value="Alpha (manual residual)")
    input_cell(ws, f"B{r}", SAMPLE_ALPHA, FMT_PCT)
    ws.cell(row=r, column=3, value="\u2014")
    fcell(ws, f"D{r}", f"=B{r}", SAMPLE_ALPHA, FMT_PCT)
    r += 1
    ws.cell(row=r, column=1, value="TOTAL explained").font = Font(bold=True)
    tot = sum(b * f for _, b, f in SAMPLE_FACTORS) + SAMPLE_ALPHA
    fcell(ws, f"D{r}", f"=SUM(D4:D{r - 1})", tot, FMT_PCT)
    ws[f"D{r}"].font = Font(bold=True)
    note = ("Note: this is a top-down attribution, not the engine's variance "
            "decomposition. In analytics/pae/models/factor.py, each factor's "
            "contribution is \u03b2\u00b2\u00b7Var(factor)/Var(portfolio) from "
            "an OLS regression with intercept; residual risk = 1 \u2212 "
            "\u03a3 contributions. With only 24 monthly observations the "
            "engine requires n \u2265 k+2 (k = number of factors).")
    ws.cell(row=r + 2, column=1, value=note).font = NOTE_FONT
    ws.merge_cells(f"A{r + 2}:D{r + 2}")
    ws[f"A{r + 2}"].alignment = WRAP
    ws.row_dimensions[r + 2].height = 60
    ws.freeze_panes = "A4"


def build_scenarios(wb):
    ws = wb.create_sheet("SCENARIOS")
    ws.sheet_properties.tabColor = "D35400"
    title_block(ws, "Scenario stress tests",
                "Shock profiles mirror engine/src/risk/stress.rs "
                "(get_scenario_shocks). Allocation comes from INPUT_Holdings. "
                "Portfolio impact = \u03a3 (allocation \u00d7 shock). Shock "
                "cells are editable inputs (amber). Cash is modeled at 0% "
                "shock (the engine has no cash profile).",
                5)
    for col, w in zip("ABCDE", [15, 14, 16, 16, 18]):
        ws.column_dimensions[col].width = w
    header_row(ws, 3, [(1, "Asset Class"), (2, "Allocation"),
                       (3, "GFC 2008"), (4, "COVID 2020"), (5, "Rate shock")])
    nav = sum(q * p for _, _, q, p, _, _ in SAMPLE_HOLDINGS)
    scen_names = list(SHOCKS.keys())
    r = 4
    for i, cls in enumerate(SHOCK_CLASSES):
        ws.cell(row=r, column=1, value=cls)
        wgt = sum(q * p for _, c, q, p, _, _ in SAMPLE_HOLDINGS
                  if c == cls) / nav
        fcell(ws, f"B{r}",
              f"=SUMIF(INPUT_Holdings!$B$4:$B$11,$A{r},"
              f"INPUT_Holdings!$E$4:$E$11)/DASHBOARD!$B$4",
              wgt, FMT_PCT)
        for j, sname in enumerate(scen_names):
            col = chr(ord("C") + j)
            input_cell(ws, f"{col}{r}", SHOCKS[sname][i], FMT_PCT)
        r += 1
    # impact rows
    header_row(ws, r + 1, [(1, "Scenario"), (2, "Portfolio impact"),
                           (3, "Impact (CAD)")])
    rr = r + 2
    for j, sname in enumerate(scen_names):
        col = chr(ord("C") + j)
        ws.cell(row=rr, column=1, value=sname)
        imp = sum(
            sum(q * p for _, c, q, p, _, _ in SAMPLE_HOLDINGS if c == cls)
            / nav * SHOCKS[sname][i]
            for i, cls in enumerate(SHOCK_CLASSES))
        fcell(ws, f"B{rr}",
              f"=SUMPRODUCT($B$4:$B${r - 1},{col}$4:{col}${r - 1})",
              imp, FMT_PCT)
        fcell(ws, f"C{rr}", f"=B{rr}*DASHBOARD!$B$4", imp * nav, FMT_CAD)
        rr += 1
    ws.cell(row=rr + 1, column=1,
            value="Engine reference \u2014 stress.rs shocks: GFC 2008 "
                  "equity \u221250% / fixed income +5%; COVID 2020 equity "
                  "\u221234% / fixed income +2%; Rate shock 2022 equity "
                  "\u221225% / fixed income \u221215% / commodity +10%. The "
                  "engine's classify_asset() is a stub (all holdings "
                  "\u2192 equity); this sheet maps each holding by its "
                  "INPUT_Holdings asset class instead.").font = NOTE_FONT
    ws.merge_cells(f"A{rr + 1}:E{rr + 1}")
    ws[f"A{rr + 1}"].alignment = WRAP
    ws.row_dimensions[rr + 1].height = 60
    ws.freeze_panes = "A4"


def build_dashboard(wb):
    ws = wb.create_sheet("DASHBOARD")
    ws.sheet_properties.tabColor = "1F4E5F"
    title_block(ws, "Portfolio dashboard",
                "Summary cards pull live from the calculation sheets. "
                "Below: a plain-text interpretation guide (educational).",
                3)
    for col, w in zip("ABC", [26, 20, 64]):
        ws.column_dimensions[col].width = w
    header_row(ws, 3, [(1, "Card"), (2, "Value"), (3, "Source")])
    nav = sum(q * p for _, _, q, p, _, _ in SAMPLE_HOLDINGS)
    tot_cb = sum(c for _, _, _, _, c, _ in SAMPLE_HOLDINGS)
    rets = [x[1] for x in SAMPLE_RETURNS]
    rf = RISK_FREE_ANNUAL
    scen_impacts = {
        s: sum(sum(q * p for _, c, q, p, _, _ in SAMPLE_HOLDINGS if c == cls)
               / nav * SHOCKS[s][i] for i, cls in enumerate(SHOCK_CLASSES))
        for s in SHOCKS}
    cards = [
        ("NAV (CAD)", "=SUM(INPUT_Holdings!E4:E11)", nav, FMT_CAD,
         "INPUT_Holdings"),
        ("Total cost basis (CAD)", "=SUM(INPUT_Holdings!F4:F11)", tot_cb,
         FMT_CAD, "INPUT_Holdings"),
        ("P&L (CAD)", "=B4-B5", nav - tot_cb, FMT_CAD, "This sheet"),
        ("P&L %", "=IF(B5=0,0,B6/B5)", (nav - tot_cb) / tot_cb, FMT_PCT,
         "This sheet"),
        ("Annualized volatility", "=RISK!B6",
         m_volatility(rets) * math.sqrt(12), FMT_PCT, "RISK"),
        ("Sharpe ratio", "=RISK!B8", m_sharpe(rets, rf), FMT_NUM, "RISK"),
        ("Sortino ratio", "=RISK!B10", m_sortino(rets, rf), FMT_NUM, "RISK"),
        ("Max drawdown", "=RISK!B11", m_max_drawdown(rets), FMT_PCT, "RISK"),
        ("VaR 95% (monthly)", "=RISK!B12", m_var(rets), FMT_PCT, "RISK"),
        ("CVaR 95% (monthly)", "=RISK!B13", m_cvar(rets), FMT_PCT, "RISK"),
        ("GFC 2008 impact", "=SCENARIOS!B12",
         scen_impacts["GFC 2008 (2008-style)"], FMT_PCT, "SCENARIOS"),
        ("COVID 2020 impact", "=SCENARIOS!B13",
         scen_impacts["COVID 2020 (2020-style)"], FMT_PCT, "SCENARIOS"),
        ("Rate shock impact", "=SCENARIOS!B14",
         scen_impacts["Rate shock (2022-style)"], FMT_PCT, "SCENARIOS"),
    ]
    r = 4
    for label, formula, value, fmt, src in cards:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        fcell(ws, f"B{r}", formula, value, fmt)
        ws.cell(row=r, column=3, value=src).font = NOTE_FONT
        r += 1
    # interpretation guide
    gr = r + 2
    ws.cell(row=gr, column=1,
            value="Interpretation guide (educational)").font = TITLE_FONT
    ws.merge_cells(f"A{gr}:C{gr}")
    gr += 1
    header_row(ws, gr, [(1, "Metric"), (2, "What it means"),
                        (3, "How to read it")])
    gr += 1
    guide = [
        ("Volatility",
         "How much monthly returns typically wobble around their average.",
         "Higher = bumpier ride. Annualized \u00d7\u221a12 for comparability."),
        ("Sharpe ratio",
         "Excess return per unit of total volatility (PAE engine: monthly).",
         "Above 1 is strong, below 0 means you trailed the risk-free rate."),
        ("Sortino ratio",
         "Like Sharpe, but only downside wobbles count as risk.",
         "Higher = returns came with less painful downside."),
        ("Max drawdown",
         "Worst peak-to-trough fall the portfolio suffered in the window.",
         "A 20% drawdown needs a 25% gain just to get back to even."),
        ("VaR 95%",
         "On the 2nd-worst month out of 24, the loss was at most this.",
         "A planning number, not a worst case \u2014 tail losses exceed it."),
        ("CVaR 95%",
         "Average loss in months worse than the VaR cutoff.",
         "More conservative than VaR; answers 'how bad is bad?'."),
        ("Factor contribution",
         "How much of expected return each style factor explains.",
         "Simplified here; the engine estimates exposures by regression."),
        ("Scenario impact",
         "What the portfolio would lose if a historical shock repeated.",
         "Shocks are stylized history, not forecasts."),
    ]
    for metric, meaning, reading in guide:
        ws.cell(row=gr, column=1, value=metric).font = Font(bold=True)
        for col, text in ((2, meaning), (3, reading)):
            c = ws.cell(row=gr, column=col, value=text)
            c.font = Font(size=10, color="2C3E50")
            c.alignment = WRAP
        ws.row_dimensions[gr].height = 34
        gr += 1
    ws.cell(row=gr + 1, column=1,
            value="This tool calculates; you decide. Nothing here is "
                  "investment advice or a buy/sell recommendation.").font = NOTE_FONT
    ws.merge_cells(f"A{gr + 1}:C{gr + 1}")
    ws.freeze_panes = "A4"


# ---------------------------------------------------------------------------
# Cached-value injection (openpyxl writes no <v> for formula cells)
# ---------------------------------------------------------------------------

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELNS = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
         "relationships}")


def inject_cached_values(xlsx_path):
    """Patch each sheet's XML so every formula cell carries its cached <v>."""
    tmp = xlsx_path + ".tmp"
    with zipfile.ZipFile(xlsx_path, "r") as zin:
        wb_root = ET.fromstring(zin.read("xl/workbook.xml"))
        rels = {rel.get("Id"): rel.get("Target") for rel in
                ET.fromstring(zin.read("xl/_rels/workbook.xml.rels"))}
        name2file = {}
        for sh in wb_root.find(f"{NS}sheets"):
            target = rels[sh.get(f"{RELNS}id")].lstrip("/")
            name2file[sh.get("name")] = (target if target.startswith("xl/")
                                         else "xl/" + target)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                sname = next((n for n, f in name2file.items()
                              if f == item.filename), None)
                if sname and sname in cache:
                    root = ET.fromstring(data)
                    for cell in root.iter(f"{NS}c"):
                        if cell.find(f"{NS}f") is not None:
                            coord = cell.get("r")
                            if coord in cache[sname]:
                                v = cell.find(f"{NS}v")
                                if v is None:
                                    v = ET.SubElement(cell, f"{NS}v")
                                v.text = repr(cache[sname][coord])
                    data = ET.tostring(root, encoding="utf-8",
                                        xml_declaration=True)
                zout.writestr(item, data)
    os.replace(tmp, xlsx_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.path.exists(OUT):
        os.remove(OUT)  # idempotent: delete-and-regenerate
    wb = Workbook()
    build_disclosure(wb)
    build_holdings(wb)
    build_returns(wb)
    build_risk(wb)
    build_factors(wb)
    build_scenarios(wb)
    build_dashboard(wb)
    wb.save(OUT)
    inject_cached_values(OUT)
    n_formulas = sum(len(v) for v in cache.values())
    print(f"Wrote {OUT}")
    print(f"Sheets: {wb.sheetnames}")
    print(f"Formula cells with cached values: {n_formulas}")


if __name__ == "__main__":
    main()
