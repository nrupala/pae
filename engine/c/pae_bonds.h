// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/* pae_bonds.h - PAE C numerical core: fixed-income analytics.
 *
 * Honest alternative to the originally planned "QuantLib via FFI" linkage
 * (see the probe note in the Phase 4 report): linking the full QuantLib C++
 * library (~2,400 files / ~436k LOC, Boost dependency, hours-long build,
 * C++ ABI and shared_ptr-ownership pitfalls across an FFI boundary) is
 * disproportionate for the engine's bond-pricing needs. Instead this module
 * implements the standard bond mathematics directly, following QuantLib's
 * methodology (QuantLib docs: "Bond" / "BondFunctions" - discounting of
 * scheduled cash flows, yield solved from the NPV equation, duration and
 * convexity as cash-flow-weighted time measures):
 *
 *   NPV(y)       = sum_i CF_i / (1 + y)^t_i
 *   YTM          = y solving NPV(y) = price          (bisection)
 *   Macaulay D   = sum_i t_i * PV(CF_i) / NPV        (weighted avg time)
 *   Modified D   = Macaulay D / (1 + y)
 *   Convexity    = sum_i t_i*(t_i+1) * PV(CF_i) / (NPV * (1+y)^2)
 *
 * Cash flows are passed as (time_in_years, amount) pairs so callers can use
 * any compounding/payment frequency and any day-count convention upstream;
 * the module itself is convention-agnostic. Times must be > 0.
 *
 * Undefined analytics return NaN (never a silent 0.0).
 */

#ifndef PAE_BONDS_H
#define PAE_BONDS_H

#ifdef __cplusplus
extern "C" {
#endif

/* Present value of cash flows: sum_i cf[i] * df[i]. */
double pae_bond_npv(const double *cashflows, const double *discount_factors,
                    int n);

/* Flat discount curve: df[i] = 1 / (1 + y)^t[i]. Annual compounding. */
void pae_flat_discount_factors(double y, const double *times, int n,
                               double *df_out);

/* Yield to maturity: y in (lo, hi) solving NPV(y) = price.
 *
 * times/cashflows: n cash-flow legs (times in years, must be > 0).
 * price: target present value (> 0).
 * Bisection with 1e-12 NPV tolerance, 200 iterations max.
 * Returns NaN when the inputs are invalid or the root is not bracketed.
 */
double pae_bond_ytm(const double *times, const double *cashflows, int n,
                    double price);

/* Macaulay duration (years): sum t_i * PV(CF_i) / NPV. NaN if NPV <= 0. */
double pae_bond_macaulay_duration(const double *times, const double *cashflows,
                                  int n, double y);

/* Modified duration: Macaulay / (1 + y). */
double pae_bond_modified_duration(const double *times, const double *cashflows,
                                  int n, double y);

/* Convexity: sum t_i*(t_i+1) * PV(CF_i) / (NPV * (1+y)^2). */
double pae_bond_convexity(const double *times, const double *cashflows, int n,
                          double y);

#ifdef __cplusplus
}
#endif

#endif /* PAE_BONDS_H */
