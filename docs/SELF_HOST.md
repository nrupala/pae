<!-- Copyright (C) 2026 2351485 ALBERTA INC. -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Self-Hosting PAE

PAE is built to run on your own machine, under your own keys. This guide
takes you from the release bundle to a running, seeded install.

**No Docker required** — and no Python, Node, or Rust either. PAE ships
as a *native bundle*: one directory per OS/architecture containing a
compiled engine, a self-contained analytics service, and the dashboard
as static files. If you would rather build that bundle yourself from
source, see [PACKAGING.md](PACKAGING.md).

> **Non-advisory note.** PAE is an educational analytics tool, not a
> financial advisor. It computes and displays; it does not advise,
> recommend, or suggest. All investment decisions are yours. The demo
> data seeded below is fictional.

## What runs

Two processes, both on loopback:

| Process   | What it is                                            | Port |
|-----------|-------------------------------------------------------|------|
| engine    | Rust binary: risk math, crypto vault, stores, and it serves the dashboard itself | 3001 |
| analytics | Python service (frozen executable): factors, journal, imports, the dashboard's data API | 3002 |

You open **one address — http://localhost:3001**. The engine serves the
dashboard from its own origin and proxies the dashboard's analytics
calls (`/api/v1/analytics/...`, `/api/v1/dashboard/...`) to the
analytics service; analytics in turn calls the engine for risk
computation. The `pae` launcher runs both processes for you.

## Install

Unpack the bundle for your OS/architecture, then run its installer:

```bash
tar xzf pae-<version>-<os>-<arch>.tar.gz
cd pae-<version>-<os>-<arch>
./install.sh                 # Windows: .\install.ps1
```

The installer copies the bundle to a prefix (default
`~/.local/opt/pae`; Windows `%LOCALAPPDATA%\Programs\PAE`) and links
the `pae` command into `~/.local/bin` (Windows: adds the prefix to
your user PATH). It refuses to overwrite a directory that is not a
previous PAE install.

Optional autostart:

- Linux: `./install.sh --systemd` writes a systemd *user* unit; the
  installer prints the two `systemctl --user` commands that enable it
  (nothing starts until you run them).
- macOS: `./install.sh --launchd` writes a LaunchAgent plist and
  prints the `launchctl load` command.
- Windows: `.\install.ps1 -RegisterTask` registers a logon scheduled
  task running `pae serve` — the supported autostart.
  `-RegisterService` registers a true Windows service **only if** a
  service wrapper (WinSW or NSSM) is on your PATH, because PAE's
  processes are ordinary executables and `sc.exe` cannot host a
  script honestly; without a wrapper it falls back to the task and
  says so.

## Run

```bash
pae start        # start engine + analytics, wait for health
pae status       # what's running, dashboard URL
pae seed         # load the fictional demo data (first run)
pae logs         # tail both logs (pae logs engine|analytics for one)
pae stop         # stop both; your data is untouched
pae restart
```

Then open **http://localhost:3001**.

### Seed the demo

A first run against empty stores is a poor introduction, so the bundle
carries a demo seed — fictional data, labelled as demo everywhere it
appears. `pae seed`:

1. Appends demo records to the engine through its own API
   (`POST /api/v1/version`): a vault manifest, a sample holdings
   snapshot in two versions, and a sample decision-journal entry — then
   verifies them through the snapshot API
   (`POST /api/v1/version/snapshot`) and the integrity endpoint.
2. Creates a **Demo Portfolio** with sample holdings in analytics, so
   the dashboard opens on populated data.

Re-running the seed is safe: the engine's store is append-only, so the
demo entities gain new versions (snapshots always show the latest),
and the analytics portfolio is created only if no "Demo Portfolio"
exists.

*Seed content note:* real PAE clients encrypt record content in the
browser before it reaches the server; the server stores opaque bytes.
The seed stands in for that step with base64-encoded JSON so you can
inspect exactly what was seeded. Never seed real holdings this way.

## Where your data lives

Everything persistent is under **PAE_HOME**
(default `~/.local/share/pae`; Windows `%LOCALAPPDATA%\PAE`):

| Path | Contents |
|------|----------|
| `data/engine/pae.db`    | Engine SQLite store: portfolios, holdings, import staging, and the append-only version store (decision journal and other versioned records) |
| `data/analytics/pae.db` | Analytics SQLite store: portfolios, holdings, journal |
| `data/agent-token`       | The per-install agent token (owner-only permissions) — see "The agent token" below |
| `run/`                  | PID files for the two processes |
| `logs/`                 | `engine.log`, `analytics.log` |

The installed bundle in the prefix contains no data — replacing it
never touches PAE_HOME. Back up by copying the `data/` directory
while PAE is stopped (`pae stop`).

Versioned records persist: the engine's append-only version store
(the kind the demo seed appends, and the decision journal) writes
every record into `data/engine/pae.db` and reloads the full history
on startup, so entries survive `pae restart` and reboots. (Builds
before Phase 3B kept the version store in memory only; records from
those builds were lost on restart and cannot be recovered.)

## Upgrade

Unpack the new bundle anywhere and re-run its installer. It replaces
the files in the prefix; PAE_HOME (your data) is untouched, so both
SQLite stores carry over. The analytics store applies its own
migrations at startup. The version store's table is created
automatically the first time the new engine opens the database.

## Configuration that matters

Set these in the environment before `pae start` (or in the service
unit / plist / task you registered).

| Variable | Effect |
|----------|--------|
| `PAE_HOME` | Data directory (default `~/.local/share/pae`). |
| `PAE_ENGINE_PORT`, `PAE_ANALYTICS_PORT` | Ports (defaults 3001 / 3002). |
| `PAE_API_KEY` (analytics) | When set, the analytics data routes require `Authorization: Bearer <key>` (or `X-API-Key`) on every call. Unset = open, which is only appropriate while analytics stays on loopback. **Set it before exposing analytics beyond your machine.** |
| `PAE_AGENT_TOKEN` (both) | Your own agent-token value, if you don't want a generated one — see "The agent token" below. When set, the token file is not used. |
| `PAE_AGENT_TOKEN_PATH` (both) | Where the agent token file lives. The launcher sets it to `data/agent-token` under PAE_HOME. |
| `PAE_CORS_ORIGINS` (analytics) | Extra browser origins allowed to call the analytics API directly, comma-separated. The launcher already allows the loopback origins for both ports; add yours here if the dashboard is reached under another name. |
| `PAE_CORS_ORIGIN` (engine) | Single origin allowed to call the engine directly from a browser (default `http://localhost:3000`). The bundled dashboard is served by the engine itself, so most installs never touch this. |
| `PAE_ENGINE_HOST` (engine) | Bind address (default `127.0.0.1`). Set to `0.0.0.0` only deliberately — see the security note below. |
| `PAE_FRED_API_KEY`, `PAE_EIA_API_KEY` (analytics) | Data-source keys for the macro/energy series (see `docs/DATA_SOURCES.md`). Optional; the product runs without them. |
| `PAE_PKE_PATH` (analytics) | Location of the knowledge-base (PKE) database the dashboard's Knowledge view reads. Default `~/.local/share/pae/pke.db`. |

Security posture for anything beyond your own machine: both processes
bind loopback by default, and that is the supported posture. The
engine's `/api/v1/crypto/encrypt` and `/decrypt` endpoints are
caller-supplied-key utilities designed for local-first use, and since
Phase 3C they also require the agent token (see `docs/THREAT_MODEL.md`).
If you deliberately expose PAE on a network,
put it behind your own authenticated reverse proxy, set
`PAE_API_KEY`, and understand the threat model first.

## The agent token (authorizing an AI agent)

PAE has one **per-install agent token**. It authorises the surfaces
an AI agent uses on your behalf:

- the A2A / agent endpoints on the **analytics** service —
  `GET /.well-known/agent.json`, `POST /api/v1/a2a/message/send`,
  `GET /.well-known/pae.json`, `GET /api/v1/tools`;
- the engine's caller-supplied-key crypto helpers —
  `POST /api/v1/crypto/encrypt` and `POST /api/v1/crypto/decrypt`;
- replacing the stored DEK envelope on the engine —
  `POST /api/v1/crypto/dek-envelope` (see the note below).

**Where it lives.** The token is generated the first time the
engine or the analytics service starts, and stored in a file only
your OS account can read (permissions `0600`):

| How you run PAE | Token file |
| --- | --- |
| Via the launcher | `data/agent-token` under PAE_HOME (default `~/.local/share/pae/data/agent-token`) |
| Services started by hand | `~/.pae/agent-token` |
| Custom location | Set `PAE_AGENT_TOKEN_PATH` for both processes |

**How to give it to your agent.** Read the token value from that
file and configure your agent/client to send it on those requests
as an HTTP header:

```
Authorization: Bearer <token>
```

Requests without the header — or with the wrong token — get
`401 AGENT_TOKEN_REQUIRED`. There is deliberately **no** "no token
means open" mode: if no token can be resolved, those surfaces stay
closed. Treat the token like a password — it is the credential that
lets a program act as your agent against PAE — so don't paste it
into shared prompts or commit it anywhere.

Notes:

- The dashboard in your browser does **not** need the token: the UI
  routes, the version endpoints the journal uses, and the dashboard
  data routes work exactly as before. The analytics *data* routes
  keep their separate, opt-in `PAE_API_KEY`; the agent token does
  not replace it.
- To rotate the token: stop both services, delete the token file,
  and start again — a fresh one is generated on the next start.
- **The stored DEK envelope is replace-protected.** The envelope your
  browser vault creates on first run is the only wrapped copy of the
  vault's data key: if it were overwritten, every encrypted record
  would become permanently unreadable. The first write (vault
  creation) is therefore open, but once an envelope exists the engine
  refuses to replace it without the agent token
  (`409 DEK_ENVELOPE_EXISTS`, stored envelope untouched). A legitimate
  replacement — key rotation — sends the same POST with
  `Authorization: Bearer <token>`.

## Troubleshooting

- **A port is taken:** set `PAE_ENGINE_PORT` / `PAE_ANALYTICS_PORT`
  before `pae start`.
- **Something didn't start:** `pae status`, then `pae logs` — the
  failing process explains itself there.
- **Dashboard loads but shows no data:** confirm analytics is healthy
  (`curl http://localhost:3002/health`) and that the demo seed ran
  (`pae seed`).
- **Engine snapshot is empty after a restart:** check that the
  engine is opening the same `PAE_DB_PATH` as before (see "Where your
  data lives") — version history lives in that file, and a different
  path means a different, empty database.
