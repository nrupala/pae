# PAE Licensing

Copyright © 2026 2351485 ALBERTA INC.

PAE is dual-track: an open-source edition under copyleft/permissive terms,
and a commercial licence for anyone whose use falls outside those terms or
who wants warranty, support, or proprietary-deployment rights. Which open
licence covers which part is the map below — it is a licensing map, not two
different products.

## The map

| Component | Open licence | Commercial |
| --- | --- | --- |
| **Engine** — `engine/` (Rust risk/crypto/versioning core) | AGPL-3.0-or-later | Available |
| **Analytics core** — `analytics/pae/` **except** the MCP surface below (models, data, decision journal, Personal Knowledge Engine) | AGPL-3.0-or-later | Available |
| **Dashboard** — `ui/` | AGPL-3.0-or-later | Available |
| **Common / MCP-exposed surface** — `analytics/pae/mcp/` (MCP server and tool definitions), `.well-known/` descriptors, `llms.txt` | Apache-2.0 | Available |
| **Product site** — `site/` | © 2351485 ALBERTA INC., all rights reserved | — |
| **Documentation** — `docs/`, `README.md` | © 2351485 ALBERTA INC., all rights reserved (code samples in docs follow the licence of the component they document) | — |

Full texts: [`LICENSES/AGPL-3.0.txt`](LICENSES/AGPL-3.0.txt),
[`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).

## Boundary rule

The MCP surface is Apache-2.0 precisely so agents and integrators can build
against PAE's protocol surface without copyleft friction. The engine behind
that surface is AGPL: running a modified PAE engine as a network service
triggers AGPL source-availability obligations for the engine and the
analytics core. Where a file's layer is ambiguous, the stricter licence
(AGPL-3.0-or-later) governs until the file is expressly re-filed under this
map. Per-file SPDX headers state each file's licence; this document governs
in case of conflict.

## Commercial licensing

Commercial licences are available for both tracks — the AGPL components and
the Apache surface alike (for warranty, indemnity, support, or bundled
proprietary deployment). Contact **info@aimlds.org**.

## Why this shape

The operator-cannot-see-the-book guarantee is only credible if the code
that enforces it is inspectable — hence AGPL for the engine. Adoption of
the agent surface is the growth path — hence Apache where other systems
connect.
