// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Build script: compile the C numerical core and link BLAS/LAPACK.
//!
//! `engine/c/pae_num.c` (+ `pae_bonds.c`) declare the BLAS/LAPACK Fortran
//! symbols manually, so no CBLAS/LAPACKE dev headers are required at compile
//! time. At link time we need `libblas`/`liblapack`:
//!
//! - On CI (and any machine with `libblas-dev`/`liblapack-dev`) the linker
//!   finds the unversioned `libblas.so`/`liblapack.so` via the default
//!   search path.
//! - On machines with only the runtime libraries (`.so.3`, no dev
//!   symlinks) we create unversioned symlinks inside OUT_DIR and add it to
//!   the link search path. No sudo, no system mutation.
//!
//! The Fortran ABI is stable across BLAS providers (reference BLAS,
//! OpenBLAS, MKL all export the same `dgemm_`/`dpotrf_`/`dsyev_` symbols),
//! so the compiled core works with whichever provider the host resolves.

fn main() {
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR not set");

    // Dev-symlink shim for hosts that only ship libblas.so.3/liblapack.so.3.
    #[cfg(unix)]
    for (name, so3) in [("blas", "libblas.so.3"), ("lapack", "liblapack.so.3")] {
        let target = format!("/usr/lib/x86_64-linux-gnu/{}", so3);
        let link = format!("{}/lib{}.so", out_dir, name);
        if std::path::Path::new(&target).exists() {
            let _ = std::fs::remove_file(&link);
            // Best effort: harmless if it fails (dev package may provide it).
            let _ = std::os::unix::fs::symlink(&target, &link);
        }
    }
    println!("cargo:rustc-link-search=native={}", out_dir);
    println!("cargo:rustc-link-lib=dylib=blas");
    println!("cargo:rustc-link-lib=dylib=lapack");

    cc::Build::new()
        .file("c/pae_num.c")
        .file("c/pae_bonds.c")
        .include("c")
        .opt_level(3)
        .flag_if_supported("-std=c11")
        .warnings(true)
        .compile("pae_num");

    println!("cargo:rerun-if-changed=c/pae_num.c");
    println!("cargo:rerun-if-changed=c/pae_num.h");
    println!("cargo:rerun-if-changed=c/pae_bonds.c");
    println!("cargo:rerun-if-changed=c/pae_bonds.h");
    println!("cargo:rerun-if-env-changed=PAE_BLAS_DIR");
}
