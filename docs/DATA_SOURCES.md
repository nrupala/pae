# PAE data sources — the build record behind pae/data/registry.py

# PAE — Data-Source Register under Decision 6

**Ruling (owner, main chat, 2026-10-26 ~13:58 MDT):** the data source is the first source, and it must be free. Operational form: (1) FIRST SOURCE ONLY — the originator, not an aggregator or scraper of someone else's feed; (2) FREE USE VERIFIED — from the source's own published terms, commercial product use permitted free, evidence recorded here; unclear terms = OUT, no stretching, no paid fallback without returning to the owner; (3) a class with no first+free source WAITS — the gap is reported, not filled against the rule.

**Compiled:** 2026-10-26, Wright (chat 60). Terms evidence was read from the sources' own pages on this date; quotes are from those pages. This register governs Phase 3 wiring: the data adapter gets a source registry with per-series provenance stamps, and any source not marked ADOPT here stays disconnected in product builds.

---

## 1. Equity prices & price history (US/CA) — **GAP: class waits**

- **In code today:** Yahoo via `yfinance` (`analytics/pae/data/yahoo.py`; quotes, history, search) — **FAILS the rule twice**: Yahoo is an aggregator of exchange data, not the first source; and Yahoo's terms reserve the data for personal, non-commercial use with redistribution barred (Yahoo Terms of Service; yfinance's own legal disclaimer states the Yahoo API "is intended for personal use only" and the library grants no rights over the data).
- **First source:** the exchanges and the CTA/UTP consolidated plans. **Free? No — decisively paid.** SEC rule filings for Nasdaq/NYSE/Cboe data products document the fee structure: redistribution fees (e.g., NYSE Best Quotes & Trades redistribution $2,500/mo; Cboe One external distribution $5,000/mo), per-user professional/non-professional fees, and enterprise licences running to $100,000–$500,000/mo.
- **Disposition:** no first+free source exists for exchange prices. **The class waits.** Interim, by PAE's own design: analytics run on **price/return series the user supplies** (CSV/import path — the user's own data, no vendor terms attach). No aggregator, no Stooq-style mirror, no paid feed without a new owner ruling.

## 2. Quotes, symbol search, company metadata — **GAP: waits with §1**

- **In code today:** Yahoo (same module). Same failure. No first+free source exists (this data is exchange/issuer data sold through the same licensing system as §1; EDGAR covers issuer *filings* metadata — see §6 — not quotes).
- **Disposition:** waits. Symbol search in-product degrades to the user's own imported universe.

## 3. Fama-French factors — **CONDITIONAL: permission path for commercial use**

- **In code today:** Kenneth French Data Library (Dartmouth), direct file download (`analytics/pae/data/factors.py`) — **first source ✓, free download ✓.**
- **Terms evidence:** the library publishes no licence granting commercial use. Dartmouth's copyright statement: "Redistribution or commercial use without the expressed, written permission of Dartmouth College is prohibited." Commercial users in practice carry the line "Kenneth R. French (© …). Used by permission." (e.g., T. Rowe Price publications). Under the owner's bright line, published terms do **not** clearly allow commercial product use.
- **Disposition:** (a) self-host/AGPL edition — a user fetching factors for their own analysis is the library's evident intent; ship with attribution and no implied endorsement; (b) **commercial/hosted enablement waits on Dartmouth's written permission** — a single permission request (owner-approved before sending; none sent by this lane). If permission is refused or unanswered, the factor module stays self-host-only.

## 4. US macro & rates — **ADOPT: FRED, Fed-owned series only**

- **In code today:** FRED via the public CSV endpoint (`fredgraph.csv`, same module) — **first source ✓** (Federal Reserve Bank of St. Louis).
- **Terms evidence (FRED API Terms of Use, read 2026-10-26):** the API is free (key registration). Property-rights clause: "Data series available through the FRED API may be owned by third parties and subject to copyright restrictions… Before using data series owned by third parties for anything other than your own personal use, you must contact the data owner to obtain permission." Copyrighted series are marked "Copyright" in their notes. Requirements if the API is used: display "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis" and bind app users to the FRED terms.
- **Disposition:** ADOPT with a **series allowlist**: only Federal-Reserve/public-domain-owned series (e.g., DGS10, FEDFUNDS, CPIAUCSL is BLS-owned — third-party: EXCLUDED until BLS terms are separately verified; the allowlist is built series-by-series in the Phase 3 wiring, each entry with its ownership note recorded). PAE's current FRED calls get re-pointed at the allowlist.

## 5. CAD rates & FX — **ADOPT (conditional): Bank of Canada Valet**

- **In code today:** not consumed (FX comes via Yahoo — fails, §7).
- **Candidate:** Bank of Canada **Valet API** — the Bank's own web service (first source for the policy rate, CORRA, GoC yields, and the daily FX reference rates), free public API (~12,500 series), governed by the Bank's Terms of Use (bankofcanada.ca/terms/), which the Valet documentation expressly incorporates. The retrieved terms impose use conditions (no circumvention of request limits, no unlawful use) and reserve third-party-content rights.
- **Terms evidence (reproduction clause read in full 2026-10-26, bankofcanada.ca/terms/):** §1 — "the Bank permits you to freely use, copy, distribute and transmit its website content" on three conditions: **attribution** (no implied endorsement); **accuracy diligence**; and §1.3 — *if the content is provided through paid services or in documents for sale, prospective purchasers must be told before sale that the content came from the Bank's website and is available there free of charge.* §2.3 excludes third-party content from the grant; §3 bars circumventing request limits.
- **Disposition:** **ADOPT** for CAD rates/FX, conditions carried into the product: Bank attribution on every BoC-sourced value, the §1.3 free-availability notice wherever BoC data surfaces inside a paid tier, third-party-marked content excluded, request limits honoured. (Closes the one check the first pass left open.)

## 6. Fundamentals & filings — **ADOPT: SEC EDGAR (US); Canada waits**

- **In code today:** not consumed (PAE has no fundamentals fetcher).
- **Candidate (US):** SEC EDGAR APIs (`data.sec.gov` — submissions + XBRL companyfacts/frames). **First source ✓** (the filings themselves, from the regulator). **Free ✓** — the SEC's API page: the APIs "do not require any authentication or API keys"; filings are public records. Conditions are conduct rules, not fees: automated access must comply with the SEC's Privacy and Security Policy (declared User-Agent, fair-access rate ≤10 requests/second).
- **Canada:** SEDAR+ publishes filings but offers no public API — Canadian fundamentals wait (user-supplied filings remain possible via import).

## 7. Commodities & non-CAD FX — **PARTIAL: energy ADOPTED via EIA (2026-10-26, fleet keys); the rest waits**

- **In code today:** the legacy Yahoo-backed commodity provider is gated off in product mode. **Energy spot prices (WTI, Brent, Henry Hub) serve from the U.S. Energy Information Administration** via `pae.data.eia` — first-source federal statistics, free through the Open Data API v2 with a registered key. The fleet already held the key (Secure Vault connector `custom.eia`, verified live 2026-10-14 and 2026-10-26); PAE adopts it as `PAE_EIA_API_KEY` in the service environment (the vault value is surrogate-only; arming a deployment is an environment entry, the RA pattern).
- **Still out:** the LBMA gold price is administered commercially (IBA) — a licensed product. Exchange commodity data is paid. FRED's third-party-owned commodity/FX series (IMF, World Bank, Dow Jones) fall under §4's exclusion — only Fed-owned series clear.
- **Disposition:** energy serves; everything else in this class waits.

## 8. Fleet keys adopted (owner direction, 2026-10-26)

The owner directed that keys the fleet already holds be adopted into PAE rather than re-provisioned:

- **FRED:** the fleet's existing key (held for the other products; on the Aetheris box as `RA_MARKET_FRED_API_KEY`) arms PAE as `PAE_FRED_API_KEY`. When set, PAE's macro path uses the keyed FRED API (`api.stlouisfed.org`); the keyless CSV endpoint remains the no-key path. The key never appears in logs, errors, cache, or results. Proven live 2026-10-26: PAE's code, running on the box with the fleet key loaded in-process, returned DGS10 values identical to the CSV read.
- **EIA:** as §7 — vault connector `custom.eia`, adopted as `PAE_EIA_API_KEY`.
- **EDGAR:** no key exists or is needed. EDGAR's data APIs are keyless by design; the identity the SEC asks for is the declared User-Agent, which the adapter already sends. (EDGAR credentials exist only for *filers* submitting filings — not a data-access key, and not PAE's business.)

---

## Consequences for the Phase 3 build

1. `yahoo.py` and the Yahoo paths in `adapter.py`/`commodities.py` are **removed from product builds** (development-only at most, labelled non-primary, per the house first-source doctrine — never the production source of record, never a silent fallback).
2. The adapter gains the **source registry** this document starts: every dataset names its first source; every served value carries a provenance stamp naming the actual source; a failed source surfaces as unavailable/stale-with-disclosure, never a quiet swap.
3. Two owner-gated items arise, neither urgent: the **Dartmouth permission request** (one email, his approval to send) and, only if he ever wants the prices class filled, a **paid-data ruling** — the register's evidence says exchange data at commercial terms starts at four-to-five figures monthly for redistribution, which is exactly what his rule exists to avoid sleepwalking into.
4. Nothing in this register blocks the self-host Phase 3 shape: user-supplied data + FRED allowlist + BoC Valet + EDGAR + self-host French factors cover a real product under the rule.
