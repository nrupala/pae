#!/usr/bin/env python3
# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Independently verify pae_portfolio_analytics.xlsx.

Reads the workbook's cached formula results (data_only=True) and its raw
inputs, recomputes every metric in pure Python using mirrors of
engine/src/risk/metrics.rs, and asserts workbook == recomputation within
tolerance. Also spot-checks that key cells actually contain formulas (not
pasted values) and that no Google-Sheets-incompatible functions are used.

Run:  python3 tools/verify_workbook.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_workbook import (  # noqa: E402
    m_volatility, m_sharpe, m_sortino, m_max_drawdown, m_var, m_cvar,
    m_total_return, m_annualized_return, SHOCK_CLASSES, SHOCKS,
)

from openpyxl import load_workbook

XLSX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "pae_portfolio_analytics.xlsx")
TOL = 1e-6

failures: list[str] = []
checks = 0


def close(name, got, expected):
    """Record a comparison; tolerance scales with magnitude."""
    global checks
    checks += 1
    tol = TOL * max(1.0, abs(expected))
    if abs(got - expected) > tol:
        failures.append(
            f"{name}: workbook={got!r} recomputed={expected!r} "
            f"diff={abs(got - expected):.3e}")
    else:
        print(f"  ok  {name:38s} = {got:.8f}")


def main():
    global checks
    print("== PAE workbook verification ==")
    if not os.path.exists(XLSX):
        print(f"MISSING: {XLSX} -- run build_workbook.py first")
        sys.exit(2)

    vals = load_workbook(XLSX, data_only=True)
    fmls = load_workbook(XLSX, data_only=False)

    # ---- 1. sheet order / names -------------------------------------
    print("[1] sheet inventory")
    expected_sheets = ["DISCLOSURE", "INPUT_Holdings", "RETURNS", "RISK",
                       "FACTORS", "SCENARIOS", "DASHBOARD"]
    checks += 1
    if vals.sheetnames != expected_sheets:
        failures.append(f"sheet order: {vals.sheetnames}")
    else:
        print(f"  ok  sheets in order: {vals.sheetnames}")

    inp = vals["INPUT_Holdings"]
    ret = vals["RETURNS"]
    risk = vals["RISK"]
    fac = vals["FACTORS"]
    scn = vals["SCENARIOS"]
    dash = vals["DASHBOARD"]

    # ---- 2. read raw inputs from the workbook ------------------------
    qtys = [inp[f"C{r}"].value for r in range(4, 12)]
    prices = [inp[f"D{r}"].value for r in range(4, 12)]
    costs = [inp[f"F{r}"].value for r in range(4, 12)]
    yields = [inp[f"G{r}"].value for r in range(4, 12)]
    classes = [inp[f"B{r}"].value for r in range(4, 12)]
    rets = [ret[f"C{r}"].value for r in range(4, 28)]
    rf = vals["RISK"]["B3"].value
    fbetas = [fac[f"B{r}"].value for r in range(4, 9)]
    ffrets = [fac[f"C{r}"].value for r in range(4, 9)]
    alpha = fac["B9"].value
    shocks = [[scn[f"{c}{r}"].value for c in "CDE"] for r in range(4, 10)]

    # ---- 3. holdings -------------------------------------------------
    print("[2] INPUT_Holdings formulas")
    for i, r in enumerate(range(4, 12)):
        close(f"market value row {r}", inp[f"E{r}"].value, qtys[i] * prices[i])
    nav = sum(q * p for q, p in zip(qtys, prices))
    tot_cb = sum(costs)
    close("NAV total", inp["E12"].value, nav)
    close("cost basis total", inp["F12"].value, tot_cb)
    close("weighted yield", inp["G12"].value,
          sum(q * p * y for q, p, y in zip(qtys, prices, yields)) / nav)

    # ---- 4. risk metrics (mirror metrics.rs) --------------------------
    print("[3] RISK formulas vs engine mirrors")
    close("monthly volatility", risk["B5"].value, m_volatility(rets))
    close("annualized volatility", risk["B6"].value,
          m_volatility(rets) * math.sqrt(12))
    close("mean monthly return", risk["B7"].value, statistics.mean(rets))
    close("Sharpe", risk["B8"].value, m_sharpe(rets, rf))
    dd_obs = [(x - rf / 12) ** 2 for x in rets if x < rf / 12]
    close("downside deviation", risk["B9"].value,
          math.sqrt(sum(dd_obs) / len(dd_obs)))
    close("Sortino", risk["B10"].value, m_sortino(rets, rf))
    close("max drawdown", risk["B11"].value, m_max_drawdown(rets))
    close("VaR 95%", risk["B12"].value, m_var(rets))
    close("CVaR 95%", risk["B13"].value, m_cvar(rets))
    close("total return", risk["B14"].value, m_total_return(rets))
    close("annualized return", risk["B15"].value,
          m_annualized_return(rets))

    # ---- 5. returns helpers -------------------------------------------
    print("[4] RETURNS helper columns")
    cum, pk = 1.0, 0.0
    for i, r in enumerate(range(4, 28)):
        cum *= (1.0 + rets[i])
        pk = max(pk, cum)
        close(f"wealth row {r}", ret[f"D{r}"].value, cum)
        close(f"peak row {r}", ret[f"E{r}"].value, pk)
        close(f"drawdown row {r}", ret[f"F{r}"].value,
              (pk - cum) / pk if pk else 0.0)
    for i, r in enumerate(range(4, 28)):
        close(f"sorted row {r}", ret[f"G{r}"].value, sorted(rets)[i])

    # ---- 6. factors ----------------------------------------------------
    print("[5] FACTORS formulas")
    for i, r in enumerate(range(4, 9)):
        close(f"contribution row {r}", fac[f"D{r}"].value,
              fbetas[i] * ffrets[i])
    close("alpha passthrough", fac["D9"].value, alpha)
    close("total explained", fac["D10"].value,
          sum(b * f for b, f in zip(fbetas, ffrets)) + alpha)

    # ---- 7. scenarios ---------------------------------------------------
    print("[6] SCENARIOS formulas")
    for i, cls in enumerate(SHOCK_CLASSES):
        r = 4 + i
        w = sum(q * p for q, p, c in zip(qtys, prices, classes)
                if c == cls) / nav
        close(f"allocation {cls}", scn[f"B{r}"].value, w)
    for j, sname in enumerate(SHOCKS):
        r = 12 + j
        imp = sum(sum(q * p for q, p, c in zip(qtys, prices, classes)
                      if c == cls) / nav * SHOCKS[sname][i]
                  for i, cls in enumerate(SHOCK_CLASSES))
        close(f"scenario impact {sname}", scn[f"B{r}"].value, imp)
        close(f"scenario CAD {sname}", scn[f"C{r}"].value, imp * nav)

    # ---- 8. dashboard ----------------------------------------------------
    print("[7] DASHBOARD formulas")
    close("dashboard NAV", dash["B4"].value, nav)
    close("dashboard cost", dash["B5"].value, tot_cb)
    close("dashboard P&L", dash["B6"].value, nav - tot_cb)
    close("dashboard P&L %", dash["B7"].value, (nav - tot_cb) / tot_cb)
    close("dashboard ann vol", dash["B8"].value,
          m_volatility(rets) * math.sqrt(12))
    close("dashboard Sharpe", dash["B9"].value, m_sharpe(rets, rf))
    close("dashboard Sortino", dash["B10"].value, m_sortino(rets, rf))
    close("dashboard max DD", dash["B11"].value, m_max_drawdown(rets))
    close("dashboard VaR", dash["B12"].value, m_var(rets))
    close("dashboard CVaR", dash["B13"].value, m_cvar(rets))

    # ---- 9. formulas present (not pasted values) -------------------------
    print("[8] formula presence spot-checks")
    f_risk = fmls["RISK"]
    f_dash = fmls["DASHBOARD"]
    f_scn = fmls["SCENARIOS"]
    f_ret = fmls["RETURNS"]
    f_inp = fmls["INPUT_Holdings"]
    f_fac = fmls["FACTORS"]
    expected_formulas = {
        ("RISK", "B5"): "=STDEV(RETURNS!C4:C27)",
        ("RISK", "B8"): "=IF(B5=0,0,(B7-$B$3/12)/B5)",
        ("RISK", "B12"): "=-INDEX(RETURNS!G4:G27,INT(0.05*COUNT(RETURNS!C4:C27))+1)",
        ("DASHBOARD", "B4"): "=SUM(INPUT_Holdings!E4:E11)",
        ("DASHBOARD", "B9"): "=RISK!B8",
        ("SCENARIOS", "B12"): "=SUMPRODUCT($B$4:$B$9,C$4:C$9)",
        ("RETURNS", "G4"): "=SMALL($C$4:$C$27,ROWS($G$4:G4))",
        ("INPUT_Holdings", "E4"): "=C4*D4",
        ("FACTORS", "D4"): "=B4*C4",
    }
    sheets_f = {"RISK": f_risk, "DASHBOARD": f_dash, "SCENARIOS": f_scn,
                "RETURNS": f_ret, "INPUT_Holdings": f_inp, "FACTORS": f_fac}
    for (sname, coord), want in expected_formulas.items():
        checks += 1
        got = sheets_f[sname][coord].value
        if got != want:
            failures.append(f"formula {sname}!{coord}: got {got!r}, "
                            f"want {want!r}")
        else:
            print(f"  ok  {sname}!{coord} = {got}")

    # ---- 10. Sheets-compatibility scan ------------------------------------
    print("[9] Sheets-compatibility scan (no LET/LAMBDA/XLOOKUP/etc.)")
    banned = ["LET(", "LAMBDA(", "XLOOKUP(", "FILTER(", "SORT(",
              "UNIQUE(", "SEQUENCE(", "MAP(", "REDUCE(", "SCAN(",
              "BYROW(", "BYCOL(", "MAKEARRAY("]
    checks += 1
    bad = []
    for ws in fmls.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("="):
                    up = v.upper()
                    for b in banned:
                        if b in up:
                            bad.append(f"{ws.title}!{c.coordinate}: {b}")
    if bad:
        failures.append("banned functions: " + "; ".join(bad))
    else:
        print("  ok  no banned functions in any formula")

    # ---- 11. disclosure present --------------------------------------------
    print("[10] disclosure sheet")
    checks += 1
    disc_text = " ".join(
        str(vals["DISCLOSURE"][f"A{r}"].value or "")
        for r in range(1, 40))
    if "NOT encrypted" not in disc_text or "PLAIN TEXT" not in disc_text:
        failures.append("DISCLOSURE missing the no-encryption statement")
    else:
        print("  ok  no-encryption disclosure present on DISCLOSURE sheet")

    print()
    print(f"checks: {checks}, failures: {len(failures)}")
    for f in failures:
        print("FAIL:", f)
    if failures:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
