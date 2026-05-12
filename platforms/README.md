# PAE Cross-Platform Builds

PAE is OS-agnostic. One codebase, every platform.

## Platform Matrix

| Platform | Technology | Output | Status |
|----------|-----------|--------|--------|
| **Web (PWA)** | Service Worker + Manifest | Installable web app | Ready |
| **Android** | Capacitor | APK / AAB | Config ready |
| **iOS** | Capacitor | IPA | Config ready |
| **Windows** | Tauri | .exe / .msi (NSIS) | Config ready |
| **macOS** | Tauri | .app / .dmg | Config ready |
| **Linux** | Tauri | .deb / .AppImage | Config ready |
| **Docker** | Multi-stage Dockerfile | Container image | Ready |

## Architecture

All platforms share the same vanilla TypeScript + Web Components UI.
No framework. No platform-specific UI code. Write once, build everywhere.

```
ui/src/                    <-- Single source of truth
  |
  +-- PWA (direct serve)   <-- Service Worker + manifest.json
  |
  +-- Capacitor            <-- Wraps ui/src/ in native Android/iOS container
  |
  +-- Tauri                <-- Wraps ui/src/ in native desktop container (Rust backend)
  |
  +-- Docker               <-- Serves ui/src/ via nginx
```

## Build Commands

### PWA (Web)
Already works. Deploy `ui/src/` to any static host.
Service worker enables offline use. Manifest enables install-to-homescreen.

### Android APK
```bash
cd platforms/capacitor
npm install
npx cap add android
npx cap sync android
cd android
./gradlew assembleDebug          # debug APK
./gradlew assembleRelease        # release APK (needs keystore)
```

### iOS
```bash
cd platforms/capacitor
npm install
npx cap add ios
npx cap sync ios
npx cap open ios                 # opens Xcode
```

### Windows / macOS / Linux (Desktop)
```bash
cd platforms/tauri
cargo install tauri-cli
cargo tauri build                # builds for current platform
cargo tauri build --target x86_64-pc-windows-msvc    # cross-compile Windows
cargo tauri build --target x86_64-apple-darwin        # cross-compile macOS
cargo tauri build --target x86_64-unknown-linux-gnu   # cross-compile Linux
```

### Docker
```bash
cd infra/docker
docker compose build
docker compose up
```

## Why This Architecture

- **Vanilla TypeScript + Web Components** = no framework lock-in.
  React/Vue/Svelte would require framework-specific adapters for each platform.
  Web Components work natively in every browser, Capacitor, and Tauri.

- **Capacitor** wraps the web app in a native WebView container.
  Full access to native APIs (filesystem, biometrics, notifications) via plugins.
  Single codebase produces Android APK and iOS IPA.

- **Tauri** wraps the web app in a Rust-powered desktop container.
  Smaller than Electron (5-10MB vs 150MB+). Native OS integration.
  The PAE Rust engine can optionally run embedded in the Tauri process.

- **PWA** is the zero-install option. Works in any modern browser.
  Service worker caches all assets for offline use.
  Manifest enables install-to-homescreen on mobile and desktop.

## Platform-Specific Notes

### Android
- Min SDK: 24 (Android 7.0)
- Target SDK: 34 (Android 14)
- Encrypted storage uses Android Keystore for hardware-backed key protection
- APK signing requires a keystore (set via environment variables in CI)

### iOS
- Min iOS: 15.0
- Uses Keychain for secure key storage
- Requires Apple Developer account for distribution

### Desktop (Tauri)
- Windows: NSIS installer, auto-update support
- macOS: .app bundle with DMG, notarization required for distribution
- Linux: .deb package + AppImage for universal Linux support
- Tauri bundles a minimal WebView (WRY) -- not a full Chromium like Electron

### Docker
- Multi-stage build: Rust compile + Python install + static UI
- ARM-compatible for OCI free tier deployment
- APP_ROOT env variable for Docker-to-VM portability
