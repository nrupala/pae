// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Safe Rust wrappers over the C numerical core (`engine/c/`).
//!
//! The C core implements BLAS/LAPACK-backed kernels -- covariance via
//! `dgemm_`, general matrix multiply/vector ops, Cholesky via `dpotrf_`,
//! symmetric eigendecomposition via `dsyev_` -- plus fixed-income analytics
//! (discount-curve NPV, yield to maturity, duration, convexity) following
//! QuantLib's bond methodology (see `engine/c/pae_bonds.h`).
//!
//! All `unsafe` FFI is confined to this module. Public functions take and
//! return ordinary Rust types; matrices are row-major `Vec<Vec<f64>>` on
//! the Rust side and converted to the column-major layout the Fortran ABI
//! expects inside the wrappers.
//!
//! The default risk path routes through here: see
//! [`crate::risk::correlation::compute_matrix`], which computes the
//! correlation matrix via the BLAS covariance kernel below.

use std::fmt;
use std::os::raw::c_int;

mod ffi {
    use std::os::raw::{c_double, c_int};

    extern "C" {
        pub fn pae_dgemm(
            ta: c_int,
            tb: c_int,
            m: c_int,
            n: c_int,
            k: c_int,
            alpha: c_double,
            a: *const c_double,
            lda: c_int,
            b: *const c_double,
            ldb: c_int,
            beta: c_double,
            c: *mut c_double,
            ldc: c_int,
        ) -> c_int;
        pub fn pae_dgemv(
            ta: c_int,
            m: c_int,
            n: c_int,
            alpha: c_double,
            a: *const c_double,
            lda: c_int,
            x: *const c_double,
            incx: c_int,
            beta: c_double,
            y: *mut c_double,
            incy: c_int,
        ) -> c_int;
        pub fn pae_covariance(
            x: *const c_double,
            m: c_int,
            n: c_int,
            cov: *mut c_double,
        ) -> c_int;
        pub fn pae_correlation(
            cov: *const c_double,
            n: c_int,
            corr: *mut c_double,
        ) -> c_int;
        pub fn pae_cholesky(a: *mut c_double, n: c_int) -> c_int;
        pub fn pae_eigen_sym(a: *mut c_double, n: c_int, w: *mut c_double)
            -> c_int;

        pub fn pae_bond_npv(
            cashflows: *const c_double,
            discount_factors: *const c_double,
            n: c_int,
        ) -> c_double;
        pub fn pae_flat_discount_factors(
            y: c_double,
            times: *const c_double,
            n: c_int,
            df_out: *mut c_double,
        );
        pub fn pae_bond_ytm(
            times: *const c_double,
            cashflows: *const c_double,
            n: c_int,
            price: c_double,
        ) -> c_double;
        pub fn pae_bond_macaulay_duration(
            times: *const c_double,
            cashflows: *const c_double,
            n: c_int,
            y: c_double,
        ) -> c_double;
        pub fn pae_bond_modified_duration(
            times: *const c_double,
            cashflows: *const c_double,
            n: c_int,
            y: c_double,
        ) -> c_double;
        pub fn pae_bond_convexity(
            times: *const c_double,
            cashflows: *const c_double,
            n: c_int,
            y: c_double,
        ) -> c_double;
    }
}

/// Error from a native numerical kernel call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NumError {
    /// Bad input (empty matrix, ragged rows, m < 2 observations, ...).
    InvalidInput,
    /// The native call itself failed (e.g. Cholesky of a non-PD matrix).
    NativeCallFailed,
}

impl fmt::Display for NumError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NumError::InvalidInput => write!(f, "invalid input to numerical kernel"),
            NumError::NativeCallFailed => write!(f, "native numerical kernel failed"),
        }
    }
}

impl std::error::Error for NumError {}

fn check_rc(rc: c_int) -> Result<(), NumError> {
    if rc == 0 {
        Ok(())
    } else {
        Err(NumError::NativeCallFailed)
    }
}

/// Convert a row-major matrix to column-major. Returns `None` on ragged/empty input.
fn to_col_major(m: &[Vec<f64>]) -> Option<(Vec<f64>, usize, usize)> {
    if m.is_empty() || m[0].is_empty() {
        return None;
    }
    let rows = m.len();
    let cols = m[0].len();
    if m.iter().any(|r| r.len() != cols) {
        return None;
    }
    let mut col = vec![0.0; rows * cols];
    for (i, row) in m.iter().enumerate() {
        for (j, &v) in row.iter().enumerate() {
            col[j * rows + i] = v;
        }
    }
    Some((col, rows, cols))
}

/// Convert a column-major matrix to row-major.
fn from_col_major(col: &[f64], rows: usize, cols: usize) -> Vec<Vec<f64>> {
    (0..rows)
        .map(|i| (0..cols).map(|j| col[j * rows + i]).collect())
        .collect()
}

/// General matrix multiply via BLAS `dgemm_`.
///
/// Computes `C = op(A) * op(B)` for row-major inputs; `trans_a`/`trans_b`
/// select the transpose of each operand.
/// Kernel API: behavior-tested below; its in-engine callers are the planned
/// factor-model / correlated-simulation work, so it is not wired yet.
#[allow(dead_code)]
pub fn matmul(
    a: &[Vec<f64>],
    b: &[Vec<f64>],
    trans_a: bool,
    trans_b: bool,
) -> Result<Vec<Vec<f64>>, NumError> {
    let (a_col, a_rows, a_cols) = to_col_major(a).ok_or(NumError::InvalidInput)?;
    let (b_col, b_rows, b_cols) = to_col_major(b).ok_or(NumError::InvalidInput)?;

    // In column-major terms, op(A) has (m x k) with m,k from the transpose flags.
    let (m, k) = if trans_a {
        (a_cols, a_rows)
    } else {
        (a_rows, a_cols)
    };
    let (k2, n) = if trans_b {
        (b_cols, b_rows)
    } else {
        (b_rows, b_cols)
    };
    if k != k2 {
        return Err(NumError::InvalidInput);
    }

    let mut c_col = vec![0.0; m * n];
    let (m, n, k) = (m as c_int, n as c_int, k as c_int);
    // SAFETY: pointers/lengths derived from live Vecs; the C kernel only
    // reads a/b and writes exactly m*n doubles into c.
    let rc = unsafe {
        ffi::pae_dgemm(
            trans_a as c_int,
            trans_b as c_int,
            m,
            n,
            k,
            1.0,
            a_col.as_ptr(),
            a_rows as c_int,
            b_col.as_ptr(),
            b_rows as c_int,
            0.0,
            c_col.as_mut_ptr(),
            m,
        )
    };
    check_rc(rc)?;
    Ok(from_col_major(&c_col, m as usize, n as usize))
}

/// Matrix-vector product via BLAS `dgemv_`: `y = op(A) * x`.
/// Kernel API: behavior-tested below; its in-engine callers are the planned
/// factor-model / correlated-simulation work, so it is not wired yet.
#[allow(dead_code)]
pub fn matvec(a: &[Vec<f64>], x: &[f64], trans: bool) -> Result<Vec<f64>, NumError> {
    let (a_col, a_rows, a_cols) = to_col_major(a).ok_or(NumError::InvalidInput)?;
    let (m, n) = if trans {
        (a_cols, a_rows)
    } else {
        (a_rows, a_cols)
    };
    if x.len() != n {
        return Err(NumError::InvalidInput);
    }
    let mut y = vec![0.0; m];
    // SAFETY: as in matmul.
    let rc = unsafe {
        ffi::pae_dgemv(
            trans as c_int,
            a_rows as c_int,
            a_cols as c_int,
            1.0,
            a_col.as_ptr(),
            a_rows as c_int,
            x.as_ptr(),
            1,
            0.0,
            y.as_mut_ptr(),
            1,
        )
    };
    check_rc(rc)?;
    Ok(y)
}

/// Sample covariance matrix of the series via BLAS `dgemm_`.
///
/// `series[i]` is the i-th series (observations in order). Each series must
/// have the same length (>= 2). Returns the n x n row-major covariance
/// matrix with Bessel's correction (divisor m - 1).
pub fn covariance_matrix(series: &[&[f64]]) -> Result<Vec<Vec<f64>>, NumError> {
    let n = series.len();
    if n == 0 {
        return Err(NumError::InvalidInput);
    }
    let m = series[0].len();
    if m < 2 || series.iter().any(|s| s.len() != m) {
        return Err(NumError::InvalidInput);
    }
    // Column-major observation matrix: series in columns.
    let mut x_col = vec![0.0; m * n];
    for (j, s) in series.iter().enumerate() {
        for (i, &v) in s.iter().enumerate() {
            x_col[j * m + i] = v;
        }
    }
    let mut cov_col = vec![0.0; n * n];
    // SAFETY: x_col has m*n live doubles, cov_col has n*n; the kernel reads
    // the former and writes exactly the latter.
    let rc = unsafe {
        ffi::pae_covariance(
            x_col.as_ptr(),
            m as c_int,
            n as c_int,
            cov_col.as_mut_ptr(),
        )
    };
    check_rc(rc)?;
    Ok(from_col_major(&cov_col, n, n))
}

/// Pearson correlation matrix of the series via the BLAS covariance kernel.
///
/// Contract mirrors the pure-Rust implementation: diagonal is 1.0;
/// off-diagonal entries are clamped to [-1, 1]; a pair involving a
/// zero-variance or non-finite series yields 0.0.
///
/// Degenerate series (any non-finite value, or all observations bitwise
/// equal) are detected exactly on the Rust side and their rows/columns are
/// zeroed after the native call: this keeps the contract exact even though
/// the BLAS path leaves ~1e-16 cancellation residue on constant inputs
/// instead of a true 0.0 variance.
pub fn correlation_matrix(series: &[&[f64]]) -> Result<Vec<Vec<f64>>, NumError> {
    let n = series.len();
    if n == 0 {
        return Err(NumError::InvalidInput);
    }
    let m = series[0].len();
    if m < 2 || series.iter().any(|s| s.len() != m) {
        return Err(NumError::InvalidInput);
    }
    let degenerate: Vec<bool> = series
        .iter()
        .map(|s| s.iter().any(|v| !v.is_finite()) || s.iter().all(|&v| v == s[0]))
        .collect();
    // Covariance via the BLAS dgemm_ kernel (shared with covariance_matrix).
    let cov = covariance_matrix(series)?;
    let (cov_col, _, _) = to_col_major(&cov).ok_or(NumError::InvalidInput)?;
    let mut corr_col = vec![0.0; n * n];
    // SAFETY: cov_col holds n*n live doubles; the kernel writes exactly n*n.
    let rc = unsafe {
        ffi::pae_correlation(cov_col.as_ptr(), n as c_int, corr_col.as_mut_ptr())
    };
    check_rc(rc)?;
    let mut corr = from_col_major(&corr_col, n, n);
    for (i, &deg) in degenerate.iter().enumerate() {
        if deg {
            for row in corr.iter_mut() {
                row[i] = 0.0;
            }
            for v in corr[i].iter_mut() {
                *v = 0.0;
            }
            // Self-correlation stays 1.0, matching the engine contract.
            corr[i][i] = 1.0;
        }
    }
    Ok(corr)
}

/// Cholesky factorization via LAPACK `dpotrf_`.
///
/// Takes a symmetric positive-definite matrix (row-major) and returns the
/// lower-triangular factor L with A = L * L' (row-major, strict upper
/// triangle zeroed). Errors when A is not positive definite.
/// Kernel API: behavior-tested below; its in-engine callers are the planned
/// factor-model / correlated-simulation work, so it is not wired yet.
#[allow(dead_code)]
pub fn cholesky(a: &[Vec<f64>]) -> Result<Vec<Vec<f64>>, NumError> {
    let (mut a_col, rows, cols) = to_col_major(a).ok_or(NumError::InvalidInput)?;
    if rows != cols {
        return Err(NumError::InvalidInput);
    }
    // SAFETY: a_col holds rows*cols live doubles; dpotrf_ factors in place.
    let rc = unsafe { ffi::pae_cholesky(a_col.as_mut_ptr(), rows as c_int) };
    check_rc(rc)?;
    Ok(from_col_major(&a_col, rows, cols))
}

/// Eigenvalues of a symmetric matrix via LAPACK `dsyev_`, ascending.
/// Kernel API: behavior-tested below; its in-engine callers are the planned
/// factor-model / correlated-simulation work, so it is not wired yet.
#[allow(dead_code)]
pub fn eigen_sym(a: &[Vec<f64>]) -> Result<Vec<f64>, NumError> {
    let (mut a_col, rows, cols) = to_col_major(a).ok_or(NumError::InvalidInput)?;
    if rows != cols {
        return Err(NumError::InvalidInput);
    }
    let mut w = vec![0.0; rows];
    // SAFETY: a_col is a live rows*rows buffer (destroyed by dsyev_);
    // w holds rows live doubles for the eigenvalues.
    let rc = unsafe {
        ffi::pae_eigen_sym(a_col.as_mut_ptr(), rows as c_int, w.as_mut_ptr())
    };
    check_rc(rc)?;
    Ok(w)
}

/// Fixed-income analytics via the C bond core (`engine/c/pae_bonds.c`).
///
/// Methodology follows QuantLib's bond treatment: cash-flow discounting,
/// yield as the root of the NPV equation, duration/convexity as discounted
/// cash-flow-weighted measures. Times are in years; the module is
/// compounding/day-count agnostic (callers supply the schedule).
pub mod bonds {
    use super::ffi;
    use std::os::raw::c_int;

    fn check_len(times: &[f64], cashflows: &[f64]) -> Option<c_int> {
        if times.len() != cashflows.len() || times.is_empty() {
            return None;
        }
        if times.len() > c_int::MAX as usize {
            return None;
        }
        Some(times.len() as c_int)
    }

    /// Present value: sum of `cashflows[i] * discount_factors[i]`.
    pub fn npv(cashflows: &[f64], discount_factors: &[f64]) -> Option<f64> {
        let n = check_len(cashflows, discount_factors)?;
        // SAFETY: both slices have n live doubles; kernel only reads.
        let v = unsafe { ffi::pae_bond_npv(cashflows.as_ptr(), discount_factors.as_ptr(), n) };
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    }

    /// Flat discount curve: `df[i] = 1 / (1 + y)^times[i]`.
    pub fn flat_discount_factors(y: f64, times: &[f64]) -> Vec<f64> {
        let mut df = vec![0.0; times.len()];
        if times.is_empty() || times.len() > c_int::MAX as usize {
            return df;
        }
        // SAFETY: times/df have n live doubles; kernel reads/writes exactly n.
        unsafe {
            ffi::pae_flat_discount_factors(
                y,
                times.as_ptr(),
                times.len() as c_int,
                df.as_mut_ptr(),
            );
        }
        df
    }

    /// Yield to maturity: the `y` with `NPV(y) == price`. `None` when the
    /// inputs are invalid or the root is not bracketed.
    pub fn ytm(times: &[f64], cashflows: &[f64], price: f64) -> Option<f64> {
        let n = check_len(times, cashflows)?;
        // SAFETY: as in npv.
        let v = unsafe {
            ffi::pae_bond_ytm(times.as_ptr(), cashflows.as_ptr(), n, price)
        };
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    }

    /// Macaulay duration in years.
    pub fn macaulay_duration(times: &[f64], cashflows: &[f64], y: f64) -> Option<f64> {
        let n = check_len(times, cashflows)?;
        let v = unsafe {
            ffi::pae_bond_macaulay_duration(times.as_ptr(), cashflows.as_ptr(), n, y)
        };
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    }

    /// Modified duration: Macaulay / (1 + y).
    pub fn modified_duration(times: &[f64], cashflows: &[f64], y: f64) -> Option<f64> {
        let n = check_len(times, cashflows)?;
        let v = unsafe {
            ffi::pae_bond_modified_duration(times.as_ptr(), cashflows.as_ptr(), n, y)
        };
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    }

    /// Convexity.
    pub fn convexity(times: &[f64], cashflows: &[f64], y: f64) -> Option<f64> {
        let n = check_len(times, cashflows)?;
        let v = unsafe {
            ffi::pae_bond_convexity(times.as_ptr(), cashflows.as_ptr(), n, y)
        };
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Deterministic pseudo-random-ish series (no RNG in tests).
    fn sample_series(n_series: usize, m: usize) -> Vec<Vec<f64>> {
        (0..n_series)
            .map(|j| {
                (0..m)
                    .map(|i| {
                        (i as f64 * 0.37 + j as f64 * 1.7).sin()
                            + 0.5 * ((i * (j + 3)) as f64 * 0.11).cos()
                    })
                    .collect()
            })
            .collect()
    }

    /// Pure-Rust sample covariance (reference for cross-checks).
    fn rust_covariance(series: &[Vec<f64>]) -> Vec<Vec<f64>> {
        let n = series.len();
        let m = series[0].len();
        let means: Vec<f64> = series
            .iter()
            .map(|s| s.iter().sum::<f64>() / m as f64)
            .collect();
        (0..n)
            .map(|i| {
                (0..n)
                    .map(|j| {
                        series[i]
                            .iter()
                            .zip(series[j].iter())
                            .map(|(a, b)| (a - means[i]) * (b - means[j]))
                            .sum::<f64>()
                            / (m - 1) as f64
                    })
                    .collect()
            })
            .collect()
    }

    /// Pure-Rust Pearson correlation (reference for cross-checks).
    fn rust_correlation(series: &[Vec<f64>]) -> Vec<Vec<f64>> {
        let cov = rust_covariance(series);
        let n = series.len();
        (0..n)
            .map(|i| {
                (0..n)
                    .map(|j| {
                        if i == j {
                            1.0
                        } else {
                            let denom = (cov[i][i] * cov[j][j]).sqrt();
                            if denom == 0.0 {
                                0.0
                            } else {
                                (cov[i][j] / denom).clamp(-1.0, 1.0)
                            }
                        }
                    })
                    .collect()
            })
            .collect()
    }

    fn max_abs_diff(a: &[Vec<f64>], b: &[Vec<f64>]) -> f64 {
        a.iter()
            .zip(b.iter())
            .flat_map(|(ra, rb)| ra.iter().zip(rb.iter()).map(|(x, y)| (x - y).abs()))
            .fold(0.0, f64::max)
    }

    #[test]
    fn test_covariance_matches_pure_rust() {
        let data = sample_series(6, 60);
        let refs: Vec<&[f64]> = data.iter().map(|s| s.as_slice()).collect();
        let native = covariance_matrix(&refs).unwrap();
        let expected = rust_covariance(&data);
        assert!(
            max_abs_diff(&native, &expected) < 1e-12,
            "C covariance kernel disagrees with pure-Rust reference"
        );
    }

    #[test]
    fn test_correlation_matches_pure_rust() {
        let data = sample_series(6, 60);
        let refs: Vec<&[f64]> = data.iter().map(|s| s.as_slice()).collect();
        let native = correlation_matrix(&refs).unwrap();
        let expected = rust_correlation(&data);
        assert!(
            max_abs_diff(&native, &expected) < 1e-12,
            "C correlation kernel disagrees with pure-Rust reference"
        );
    }

    #[test]
    fn test_correlation_constant_series_is_zero() {
        let data = [vec![0.01; 20], vec![0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.05]];
        let refs: Vec<&[f64]> = data.iter().map(|s| s.as_slice()).collect();
        let corr = correlation_matrix(&refs).unwrap();
        assert_eq!(corr[0][0], 1.0);
        assert_eq!(corr[0][1], 0.0);
        assert_eq!(corr[1][0], 0.0);
    }

    #[test]
    fn test_native_kernels_are_not_stubs() {
        // A stubbed C layer (returning zeros / identity) cannot pass these:
        // they assert exact, non-trivial values computed by the real kernels.
        let a = vec![vec![1.0, 2.0], vec![3.0, 4.0]];
        let b = vec![vec![5.0, 6.0], vec![7.0, 8.0]];
        let c = matmul(&a, &b, false, false).unwrap();
        assert_eq!(c, vec![vec![19.0, 22.0], vec![43.0, 50.0]]);

        let y = matvec(&a, &[1.0, 1.0], false).unwrap();
        assert_eq!(y, vec![3.0, 7.0]);

        // Covariance of a non-degenerate 3-series set must be non-zero and
        // match the pure-Rust reference: zeros would fail here.
        let data = sample_series(3, 40);
        let refs: Vec<&[f64]> = data.iter().map(|s| s.as_slice()).collect();
        let cov = covariance_matrix(&refs).unwrap();
        assert!(cov.iter().flatten().any(|&v| v.abs() > 1e-9));
        assert!(max_abs_diff(&cov, &rust_covariance(&data)) < 1e-12);

        // Cholesky of a known SPD matrix.
        let l = cholesky(&[vec![4.0, 2.0], vec![2.0, 3.0]]).unwrap();
        assert!((l[0][0] - 2.0).abs() < 1e-12);
        assert!((l[1][0] - 1.0).abs() < 1e-12);
        assert!((l[1][1] - 2.0_f64.sqrt()).abs() < 1e-12);
        assert_eq!(l[0][1], 0.0);

        // Eigenvalues of diag(2, 5).
        let w = eigen_sym(&[vec![2.0, 0.0], vec![0.0, 5.0]]).unwrap();
        assert!((w[0] - 2.0).abs() < 1e-12 && (w[1] - 5.0).abs() < 1e-12);
    }

    #[test]
    fn test_cholesky_rejects_non_positive_definite() {
        // [[1, 2], [2, 1]] has eigenvalues 3 and -1: not PD.
        let r = cholesky(&[vec![1.0, 2.0], vec![2.0, 1.0]]);
        assert_eq!(r, Err(NumError::NativeCallFailed));
    }

    #[test]
    fn test_covariance_rejects_bad_input() {
        assert_eq!(covariance_matrix(&[]), Err(NumError::InvalidInput));
        assert_eq!(
            covariance_matrix(&[&[1.0]]),
            Err(NumError::InvalidInput)
        ); // m < 2
        let ragged: Vec<&[f64]> = vec![&[1.0, 2.0, 3.0], &[1.0, 2.0]];
        assert_eq!(covariance_matrix(&ragged), Err(NumError::InvalidInput));
    }

    // ---- Bond analytics: textbook values --------------------------------
    //
    // Reference bond: 10-year, 5% annual coupon, face 100, priced at par.
    // Standard textbook values (annual compounding):
    //   YTM = 5%, Macaulay duration = 8.1078y, modified = 7.7217y,
    //   convexity = 75.00. Verified against the closed-form par-bond
    //   identities and an independent hand computation.

    fn coupon_bond_10y() -> (Vec<f64>, Vec<f64>) {
        let times: Vec<f64> = (1..=10).map(|t| t as f64).collect();
        let cfs: Vec<f64> = (1..=10)
            .map(|t| if t == 10 { 105.0 } else { 5.0 })
            .collect();
        (times, cfs)
    }

    #[test]
    fn test_bond_par_ytm_is_coupon_rate() {
        let (t, cf) = coupon_bond_10y();
        let ytm = bonds::ytm(&t, &cf, 100.0).unwrap();
        assert!((ytm - 0.05).abs() < 1e-9, "YTM at par must equal coupon");
    }

    #[test]
    fn test_bond_ytm_round_trip() {
        // Price at 4%, solve YTM, reprice at solved YTM: must recover price.
        let (t, cf) = coupon_bond_10y();
        let df = bonds::flat_discount_factors(0.04, &t);
        let price = bonds::npv(&cf, &df).unwrap();
        let ytm = bonds::ytm(&t, &cf, price).unwrap();
        assert!((ytm - 0.04).abs() < 1e-9);
        let df2 = bonds::flat_discount_factors(ytm, &t);
        let price2 = bonds::npv(&cf, &df2).unwrap();
        assert!((price2 - price).abs() < 1e-9);
    }

    #[test]
    fn test_bond_duration_convexity_textbook() {
        let (t, cf) = coupon_bond_10y();
        let mac = bonds::macaulay_duration(&t, &cf, 0.05).unwrap();
        let modd = bonds::modified_duration(&t, &cf, 0.05).unwrap();
        let cvx = bonds::convexity(&t, &cf, 0.05).unwrap();
        assert!((mac - 8.107_822).abs() < 1e-4, "mac={}", mac);
        assert!((modd - 7.721_735).abs() < 1e-4, "mod={}", modd);
        assert!((cvx - 74.997_7).abs() < 1e-3, "cvx={}", cvx);
        // Modified = Macaulay / (1 + y) identity.
        assert!((modd - mac / 1.05).abs() < 1e-12);
    }

    #[test]
    fn test_bond_zero_coupon_identities() {
        // Zero-coupon: duration is exactly the maturity; NPV = 100/(1+y)^n.
        let (t, cf) = (vec![3.0], vec![100.0]);
        let mac = bonds::macaulay_duration(&t, &cf, 0.05).unwrap();
        assert!((mac - 3.0).abs() < 1e-12);
        let df = bonds::flat_discount_factors(0.05, &t);
        let price = bonds::npv(&cf, &df).unwrap();
        assert!((price - 100.0 / 1.05_f64.powi(3)).abs() < 1e-9);
        let ytm = bonds::ytm(&t, &cf, price).unwrap();
        assert!((ytm - 0.05).abs() < 1e-9);
    }

    #[test]
    fn test_bond_invalid_inputs_are_none() {
        let (t, cf) = coupon_bond_10y();
        assert!(bonds::ytm(&t, &cf, -5.0).is_none());
        assert!(bonds::ytm(&t, &cf, 0.0).is_none());
        assert!(bonds::macaulay_duration(&t, &cf, -1.0).is_none());
        assert!(bonds::convexity(&[], &[], 0.05).is_none());
        assert!(bonds::npv(&cf, &[1.0; 9]).is_none()); // length mismatch
    }
}
