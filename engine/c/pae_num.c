/* pae_num.c - PAE C numerical core: BLAS/LAPACK-backed kernels.
 *
 * The BLAS/LAPACK Fortran symbols are declared manually below (standard
 * practice; no <cblas.h>/<lapacke.h> needed). All arguments are passed by
 * reference per the Fortran ABI. Character options are passed as pointers
 * to a single char; the reference BLAS/LAPACK implementations inspect only
 * the first character (via LSAME), so the hidden string-length arguments
 * gfortran appends are unused.
 *
 * Column-major layout is used throughout, matching the Fortran ABI.
 */

#include "pae_num.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Manual Fortran symbol declarations                                   */
/* ------------------------------------------------------------------ */

extern void dgemm_(char *transa, char *transb,
                   const int *m, const int *n, const int *k,
                   const double *alpha,
                   const double *a, const int *lda,
                   const double *b, const int *ldb,
                   const double *beta,
                   double *c, const int *ldc);

extern void dgemv_(char *trans,
                   const int *m, const int *n,
                   const double *alpha,
                   const double *a, const int *lda,
                   const double *x, const int *incx,
                   const double *beta,
                   double *y, const int *incy);

extern void dpotrf_(char *uplo, const int *n,
                    double *a, const int *lda, int *info);

extern void dsyev_(char *jobz, char *uplo, const int *n,
                   double *a, const int *lda, double *w,
                   double *work, const int *lwork, int *info);

/* ------------------------------------------------------------------ */
/* BLAS wrappers                                                       */
/* ------------------------------------------------------------------ */

int pae_dgemm(int ta, int tb, int m, int n, int k, double alpha,
              const double *a, int lda,
              const double *b, int ldb,
              double beta, double *c, int ldc) {
    if (!a || !b || !c || m < 0 || n < 0 || k < 0) return PAE_NUM_EINVAL;
    if (m == 0 || n == 0) return PAE_NUM_OK;

    int ra = ta ? k : m;   /* rows of op(A) */
    int rb = tb ? n : k;   /* rows of op(B) */
    if (lda < (ra > 1 ? ra : 1) || ldb < (rb > 1 ? rb : 1) || ldc < (m > 1 ? m : 1))
        return PAE_NUM_EINVAL;

    char ca = ta ? 'T' : 'N';
    char cb = tb ? 'T' : 'N';
    dgemm_(&ca, &cb, &m, &n, &k, &alpha, a, &lda, b, &ldb, &beta, c, &ldc);
    return PAE_NUM_OK;
}

int pae_dgemv(int ta, int m, int n, double alpha,
              const double *a, int lda,
              const double *x, int incx,
              double beta, double *y, int incy) {
    if (!a || !x || !y || m < 0 || n < 0 || incx <= 0 || incy <= 0)
        return PAE_NUM_EINVAL;
    if (m == 0 || n == 0) return PAE_NUM_OK;

    int rows = ta ? n : m;
    int cols = ta ? m : n;
    if (lda < (rows > 1 ? rows : 1)) return PAE_NUM_EINVAL;
    (void)cols;

    char ct = ta ? 'T' : 'N';
    dgemv_(&ct, &m, &n, &alpha, a, &lda, x, &incx, &beta, y, &incy);
    return PAE_NUM_OK;
}

/* ------------------------------------------------------------------ */
/* Covariance / correlation                                            */
/* ------------------------------------------------------------------ */

int pae_covariance(const double *x, int m, int n, double *cov) {
    if (!x || !cov || m < 2 || n < 1) return PAE_NUM_EINVAL;

    /* Centered copy: BLAS works on the centered matrix; never touch x. */
    double *xc = (double *)malloc((size_t)m * (size_t)n * sizeof(double));
    if (!xc) return PAE_NUM_EINVAL;

    for (int j = 0; j < n; j++) {
        double mean = 0.0;
        for (int i = 0; i < m; i++) mean += x[(size_t)i + (size_t)j * m];
        mean /= (double)m;
        for (int i = 0; i < m; i++)
            xc[(size_t)i + (size_t)j * m] = x[(size_t)i + (size_t)j * m] - mean;
    }

    /* cov = Xc' * Xc / (m - 1): the sample covariance via dgemm_. */
    double one = 1.0, zero = 0.0;
    char ca = 'T', cb = 'N';
    dgemm_(&ca, &cb, &n, &n, &m, &one, xc, &m, xc, &m, &zero, cov, &n);

    double scale = 1.0 / (double)(m - 1);
    for (int i = 0; i < n * n; i++) cov[i] *= scale;

    free(xc);
    return PAE_NUM_OK;
}

int pae_correlation(const double *cov, int n, double *corr) {
    if (!cov || !corr || n < 1) return PAE_NUM_EINVAL;

    /* Largest finite variance: the scale for the degeneracy tolerance. */
    double vmax = 0.0;
    for (int i = 0; i < n; i++) {
        double vi = cov[(size_t)i + (size_t)i * n];
        if (isfinite(vi) && vi > vmax) vmax = vi;
    }
    /* A series whose variance is 12 orders of magnitude below the largest
     * is numerically constant: dgemm_ leaves cancellation residue (~1e-16
     * relative) on exactly-constant inputs instead of a true 0.0. Treat it
     * as degenerate so the correlation comes out 0.0, not residue noise. */
    double vtol = vmax * 1e-12;

    for (int j = 0; j < n; j++) {
        for (int i = 0; i < n; i++) {
            double vi = cov[(size_t)i + (size_t)i * n];
            double vj = cov[(size_t)j + (size_t)j * n];
            int di = !(isfinite(vi) && vi > vtol);
            int dj = !(isfinite(vj) && vj > vtol);
            double out;
            if (i == j) {
                out = (isfinite(vi) && vi > 0.0) ? 1.0 : 0.0;
            } else if (di || dj) {
                /* Zero/near-zero/non-finite variance: undefined -> 0.0 */
                out = 0.0;
            } else {
                double denom = sqrt(vi * vj);
                out = isfinite(denom) && denom > 0.0
                          ? cov[(size_t)i + (size_t)j * n] / denom
                          : 0.0;
                if (!isfinite(out)) out = 0.0;
                if (out > 1.0) out = 1.0;
                else if (out < -1.0) out = -1.0;
            }
            corr[(size_t)i + (size_t)j * n] = out;
        }
    }
    return PAE_NUM_OK;
}

/* ------------------------------------------------------------------ */
/* LAPACK wrappers                                                     */
/* ------------------------------------------------------------------ */

int pae_cholesky(double *a, int n) {
    if (!a || n < 1) return PAE_NUM_EINVAL;
    char uplo = 'L';
    int info = 0;
    dpotrf_(&uplo, &n, a, &n, &info);
    if (info != 0) return PAE_NUM_EINVAL; /* not positive definite */
    /* Zero the strictly-upper triangle so callers see a clean L. */
    for (int j = 0; j < n; j++)
        for (int i = 0; i < j; i++)
            a[(size_t)i + (size_t)j * n] = 0.0;
    return PAE_NUM_OK;
}

int pae_eigen_sym(double *a, int n, double *w) {
    if (!a || !w || n < 1) return PAE_NUM_EINVAL;
    char jobz = 'N'; /* eigenvalues only */
    char uplo = 'U';
    int info = 0;

    /* Workspace query for the optimal lwork. */
    int lwork = -1;
    double wkopt = 0.0;
    dsyev_(&jobz, &uplo, &n, a, &n, w, &wkopt, &lwork, &info);
    if (info != 0) return PAE_NUM_EINVAL;

    lwork = (int)wkopt;
    if (lwork < 1) lwork = 1;
    double *work = (double *)malloc((size_t)lwork * sizeof(double));
    if (!work) return PAE_NUM_EINVAL;

    dsyev_(&jobz, &uplo, &n, a, &n, w, work, &lwork, &info);
    free(work);
    return info == 0 ? PAE_NUM_OK : PAE_NUM_EINVAL;
}
