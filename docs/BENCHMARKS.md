# PAE Engine — Measured Benchmarks

Measured 2026-10-26 against the Phase 2 build (v0.2.0), release profile
(`cargo build --release`, LTO on), on the development sandbox VM
(4 vCPU, x86_64, shared host — treat figures as indicative, not a
hardware guarantee). Timings are end-to-end HTTP round-trips on loopback
(`127.0.0.1`), median of the stated runs, measured from the client.

| Operation | Workload | Median | Runs |
| --- | --- | --- | --- |
| Portfolio risk (VaR/CVaR/Sharpe/Sortino/vol/drawdown) | 2 holdings × 100 daily returns each | **0.65 ms** | 20 |
| Monte Carlo | 10,000 simulations × 120 months, 1 holding | **119 ms** | 5 |
| Point-in-time snapshot | 2 entities, 3 versions in store | served in the same request class as risk (not separately timed) | — |

For contrast, the debug (unoptimized) build measured on the same VM:
risk 1.70 ms median; Monte Carlo 10k ≈ 4,180 ms median.

## What this replaces

Earlier copy claimed "sub-millisecond latency" for the risk engine with
no measurement behind it. The claim is now: **risk metrics on a small
portfolio return in well under a millisecond of server time over
loopback** in release builds, per the table above. Re-run this bench
before quoting any figure publicly, and state the hardware when you do.

Reproduce: start the engine (`PAE_ENGINE_PORT=3001
./target/release/pae-engine`), then time `POST /api/v1/portfolio/risk`
and `POST /api/v1/portfolio/montecarlo` with representative payloads.
