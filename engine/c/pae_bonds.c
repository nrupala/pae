/* pae_bonds.c - PAE C numerical core: fixed-income analytics.
 *
 * Implements the standard bond mathematics described in pae_bonds.h,
 * following QuantLib's bond methodology (cash-flow discounting; yield as
 * the root of the NPV equation; duration/convexity as weighted measures).
 */

#include "pae_bonds.h"

#include <math.h>

/* Discount factor for a single leg, guarding the (1+y) <= 0 singularity. */
static double df_leg(double y, double t) {
    double base = 1.0 + y;
    if (!(base > 0.0) || !(t > 0.0)) return NAN;
    return pow(base, -t);
}

double pae_bond_npv(const double *cashflows, const double *discount_factors,
                    int n) {
    if (!cashflows || !discount_factors || n < 1) return NAN;
    double npv = 0.0;
    for (int i = 0; i < n; i++) npv += cashflows[i] * discount_factors[i];
    return npv;
}

void pae_flat_discount_factors(double y, const double *times, int n,
                               double *df_out) {
    if (!times || !df_out || n < 1) return;
    for (int i = 0; i < n; i++) df_out[i] = df_leg(y, times[i]);
}

double pae_bond_ytm(const double *times, const double *cashflows, int n,
                    double price) {
    if (!times || !cashflows || n < 1 || !(price > 0.0)) return NAN;
    for (int i = 0; i < n; i++)
        if (!(times[i] > 0.0)) return NAN;

    /* NPV(y) is strictly decreasing in y for positive cash flows with
     * positive times, so bisection on the price is safe. */
    double lo = -0.999999;   /* just above the (1+y) = 0 singularity */
    double hi = 10.0;        /* 1000% - generous upper bracket        */

    /* Check the bracket: NPV(lo) >= price >= NPV(hi) must hold. */
    double npv_lo = 0.0, npv_hi = 0.0;
    for (int i = 0; i < n; i++) {
        double dlo = df_leg(lo, times[i]);
        double dhi = df_leg(hi, times[i]);
        if (!isfinite(dlo) || !isfinite(dhi)) return NAN;
        npv_lo += cashflows[i] * dlo;
        npv_hi += cashflows[i] * dhi;
    }
    if (!(npv_lo >= price) || !(price >= npv_hi)) return NAN;

    double mid = 0.0;
    for (int iter = 0; iter < 200; iter++) {
        mid = 0.5 * (lo + hi);
        double npv = 0.0;
        for (int i = 0; i < n; i++) npv += cashflows[i] * df_leg(mid, times[i]);
        if (!isfinite(npv)) return NAN;
        if (fabs(npv - price) <= 1e-12 * (1.0 + fabs(price))) break;
        if (npv > price)
            lo = mid;   /* need a higher yield to lower the price */
        else
            hi = mid;
    }
    return mid;
}

/* Shared helper: sum of discounted cash flows and the weighted sums. */
static double weighted_sums(const double *times, const double *cashflows,
                            int n, double y, double *w1, double *w2) {
    double npv = 0.0, s1 = 0.0, s2 = 0.0;
    for (int i = 0; i < n; i++) {
        double d = df_leg(y, times[i]);
        if (!isfinite(d)) return NAN;
        double pv = cashflows[i] * d;
        npv += pv;
        s1 += times[i] * pv;
        s2 += times[i] * (times[i] + 1.0) * pv;
    }
    *w1 = s1;
    *w2 = s2;
    return npv;
}

static int valid_legs(const double *times, const double *cashflows, int n,
                      double y) {
    if (!times || !cashflows || n < 1) return 0;
    if (!(y > -1.0) || !isfinite(y)) return 0;
    for (int i = 0; i < n; i++)
        if (!(times[i] > 0.0)) return 0;
    return 1;
}

double pae_bond_macaulay_duration(const double *times, const double *cashflows,
                                  int n, double y) {
    if (!valid_legs(times, cashflows, n, y)) return NAN;
    double s1, s2;
    double npv = weighted_sums(times, cashflows, n, y, &s1, &s2);
    if (!isfinite(npv) || !(npv > 0.0)) return NAN;
    return s1 / npv;
}

double pae_bond_modified_duration(const double *times, const double *cashflows,
                                  int n, double y) {
    double mac = pae_bond_macaulay_duration(times, cashflows, n, y);
    if (!isfinite(mac)) return NAN;
    return mac / (1.0 + y);
}

double pae_bond_convexity(const double *times, const double *cashflows, int n,
                          double y) {
    if (!valid_legs(times, cashflows, n, y)) return NAN;
    double s1, s2;
    double npv = weighted_sums(times, cashflows, n, y, &s1, &s2);
    if (!isfinite(npv) || !(npv > 0.0)) return NAN;
    double base = 1.0 + y;
    return s2 / (npv * base * base);
}
