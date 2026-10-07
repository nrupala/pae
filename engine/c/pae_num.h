// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/* pae_num.h - PAE C numerical core: BLAS/LAPACK-backed kernels.
 *
 * Part of the PAE risk engine. These kernels implement the matrix
 * primitives the engine's risk layer needs; safe Rust wrappers live in
 * engine/src/num_ffi.rs. All matrices are COLUMN-MAJOR (Fortran order),
 * matching the BLAS/LAPACK ABI. BLAS/LAPACK symbols are declared manually
 * (no <cblas.h>/<lapacke.h> required) so the code builds against the
 * runtime .so.3 libraries as well as -dev packages.
 *
 * Conventions:
 *   - Functions return an int status: 0 on success, negative on bad input.
 *   - A non-finite or degenerate variance is mapped to 0.0 correlation,
 *     matching the engine's pure-Rust contract (see risk/correlation.rs).
 *   - NaN is returned for undefined bond analytics (never a silent 0.0).
 */

#ifndef PAE_NUM_H
#define PAE_NUM_H

#ifdef __cplusplus
extern "C" {
#endif

#define PAE_NUM_OK     0
#define PAE_NUM_EINVAL (-1)

/* ------------------------------------------------------------------ */
/* BLAS wrappers                                                       */
/* ------------------------------------------------------------------ */

/* C = alpha * op(A) * op(B) + beta * C, via BLAS dgemm_.
 *
 * Column-major. ta/tb: 0 = NoTrans, 1 = Trans.
 * A is m x k (or k x m if ta), B is k x n (or n x k if tb), C is m x n.
 * lda/ldb/ldc are the leading dimensions (>= max(1, rows of the operand)).
 */
int pae_dgemm(int ta, int tb, int m, int n, int k, double alpha,
              const double *a, int lda,
              const double *b, int ldb,
              double beta, double *c, int ldc);

/* y = alpha * op(A) * x + beta * y, via BLAS dgemv_.
 *
 * Column-major. ta: 0 = NoTrans (A is m x n), 1 = Trans (A is n x m).
 * incx/incy are the vector strides (normally 1).
 */
int pae_dgemv(int ta, int m, int n, double alpha,
              const double *a, int lda,
              const double *x, int incx,
              double beta, double *y, int incy);

/* ------------------------------------------------------------------ */
/* Covariance / correlation                                            */
/* ------------------------------------------------------------------ */

/* Sample covariance matrix of m observations of n series.
 *
 * x:   m x n column-major observation matrix (series in columns).
 * cov: n x n column-major output; cov = Xc' * Xc / (m - 1) where Xc is the
 *      column-centered copy of x (computed via dgemm_).
 *
 * Requires m >= 2 and non-null pointers; the input x is never modified.
 */
int pae_covariance(const double *x, int m, int n, double *cov);

/* Pearson correlation matrix from a covariance matrix.
 *
 * cov:  n x n column-major covariance matrix.
 * corr: n x n column-major output.
 *
 * corr[i,j] = cov[i,j] / sqrt(cov[i,i] * cov[j,j]), clamped to [-1, 1].
 * corr[i,i] = 1.0 when the i-th variance is positive and finite.
 * Any pair involving a non-positive, non-finite, or numerically-constant
 * series (variance <= 1e-12 * max variance -- BLAS cancellation residue on
 * exactly-constant inputs) yields 0.0.
 */
int pae_correlation(const double *cov, int n, double *corr);

/* ------------------------------------------------------------------ */
/* LAPACK wrappers                                                     */
/* ------------------------------------------------------------------ */

/* In-place Cholesky factorization via LAPACK dpotrf_.
 *
 * a: n x n column-major symmetric positive-definite matrix; on success
 *    holds the lower-triangular factor L with A = L * L'.
 * Returns PAE_NUM_EINVAL if the matrix is not positive definite
 * (dpotrf info > 0) or on bad input.
 */
int pae_cholesky(double *a, int n);

/* Symmetric eigendecomposition via LAPACK dsyev_.
 *
 * a: n x n column-major symmetric matrix; destroyed on return.
 * w: length-n output; eigenvalues in ascending order.
 * Returns PAE_NUM_EINVAL on bad input or dsyev_ failure.
 */
int pae_eigen_sym(double *a, int n, double *w);

#ifdef __cplusplus
}
#endif

#endif /* PAE_NUM_H */
