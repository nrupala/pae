# PAE — Platform / App-Shell Build Notes

The `platforms/` directory holds thin native **shells** around the polished webapp in
`ui/`. The webapp itself is the product; the shells only package it for app stores
and desktop installers. One codebase, every platform.

> **No native build was attempted in this environment.** The mobile (Android SDK,
> Xcode) and desktop (Rust/Tauri) toolchains are not available here, and no network
> installs were performed. Everything below is build documentation verified against
> the repo's configs and package manifests — not a build log.

## 1. What each shell wraps

| Shell | Directory | Wraps | Output |
|-------|-----------|-------|--------|
| Capacitor | `platforms/capacitor/` | the compiled webapp in `ui/dist/` | Android APK / AAB, iOS IPA |
| Tauri | `platforms/tauri/` | the compiled webapp in `ui/dist/` | Windows .exe/.msi, macOS .app/.dmg, Linux .deb/.AppImage |

Both shells point at the **compiled webapp output**, `ui/dist/`, which is produced by
`npm run build` in `ui/` (tsc per `ui/package.json` + `ui/tsconfig.json`
`outDir: ./dist`). `ui/src/` is the single source of truth for code; `ui/dist/` is the
single build artifact the shells consume.

## 2. Build commands

### Step 0 — build the webapp (required before any shell)
```bash
cd ui
npm install          # once per checkout (network required; not done here)
npm run build        # tsc -> ui/dist/
```
Note: plain `tsc` compiles only the TypeScript. The dashboard-polish pass must add an
asset-copy step (index.html, manifest.json, sw.js, styles/, vendor/) into `ui/dist/`
before any shell can wrap it — see §4 gap (a).

### Capacitor — Android APK
```bash
cd platforms/capacitor
npm install
npx cap add android
npx cap sync android          # copies ui/dist/ into the native project
cd android
./gradlew assembleDebug       # debug APK
./gradlew assembleRelease     # release APK (needs keystore; see config for env-var wiring)
```

### Capacitor — iOS
```bash
cd platforms/capacitor
npm install
npx cap add ios
npx cap sync ios
npx cap open ios              # opens Xcode; requires Apple Developer account for distribution
```

### Tauri — Windows / macOS / Linux
```bash
cd platforms/tauri
cargo install tauri-cli
cargo tauri build                                      # builds for the current platform
cargo tauri build --target x86_64-pc-windows-msvc      # cross-compile Windows
cargo tauri build --target x86_64-apple-darwin         # cross-compile macOS
cargo tauri build --target x86_64-unknown-linux-gnu    # cross-compile Linux
```

## 3. Prerequisites (NOT available in this environment)

- Node.js + npm (for `ui` and `platforms/capacitor`)
- Android SDK + Gradle (Android APK), macOS + Xcode (iOS)
- Rust toolchain + tauri-cli (desktop builds)
- Java keystore for signed Android release builds
- Apple Developer account (iOS distribution), macOS notarization credentials
- **None of the above were present or installed here; no native build was attempted.**

## 4. Config coherence check (2026-10-05, Phase 3)

**Mismatches found and fixed:**
- (a) **Shell configs pointed at `ui/src/`, not the build output.**
  `capacitor.config.ts` had `webDir: '../../ui/src'` and `tauri.conf.json` had
  `frontendDist: '../../ui/src'` — the *uncompiled* TypeScript source, which neither
  Capacitor nor Tauri can serve. The documented build pipeline (`ui/package.json`
  `build` -> `tsc`, `outDir: ./dist`; `Makefile` dev target serves `dist/`;
  `ui/e2e-crypto.mjs` imports from `./dist/...`) treats **`ui/dist/`** as the webapp
  output. Both configs were corrected to `../../ui/dist` (commit-free working-tree
  edit, verified JSON validity for the Tauri config). No other config content changed.

**Gaps found — not fixed (owned by the dashboard-polish phase / morning convergence):**
- (b) **`ui/dist/` does not exist yet, and `tsc` alone cannot produce a complete app.**
  `tsc` emits only compiled JS; `ui/src/index.html`, `manifest.json`, `sw.js`,
  `styles/*.css`, and `vendor/hash-wasm/` are never copied to `dist/`. A build script
  asset-copy step is still needed before the shells can wrap `dist/`.
- (c) **Dev proxy ports are inconsistent.** Capacitor dev `server.url` and Tauri
  `devUrl` point at `http://localhost:3000`, but the Tauri CSP allows
  `connect-src http://localhost:3001` and the documented REST surfaces are
  `:3001`/`:3002`. Dev-only, but worth one explicit port decision at convergence.
- (d) **Stale wording in `platforms/README.md`.** It still says "Deploy `ui/src/` to
  any static host" and shows the diagram arrows landing on `ui/src/`; with the
  `dist/` pipeline, deployment and the shells consume `ui/dist/`. README edit left
  for its owning phase (this note's scope was `BUILD.md`).

## 5. Standing boundaries

- Shells are packaging only: no platform-specific UI code, no framework adapters.
- No merges, no deploys, no spend inside this work. First store submission needs
  Nrupal's per-item approval (keystore, Apple account, notarization all involve
  identity/signing assets).
