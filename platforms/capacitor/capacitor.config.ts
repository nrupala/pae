import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Capacitor configuration for PAE Android/iOS builds.
 * Wraps the vanilla TypeScript PWA in a native container.
 *
 * Build: build the webapp first (see ui/README), then:
 *   npx cap sync android && cd android && ./gradlew assembleRelease
 * The web assets are copied from ui/dist/ (the compiled webapp output) into the native project.
 */
const config: CapacitorConfig = {
  appId: 'com.nrupala.pae',
  appName: 'PAE',
  webDir: '../../ui/dist',
  server: {
    // In dev, proxy API calls to the Rust engine
    url: 'http://localhost:3000',
    cleartext: true, // dev only -- disable in production
  },
  android: {
    buildOptions: {
      keystorePath: undefined, // set via env for release builds
      keystoreAlias: undefined,
    },
  },
  ios: {
    scheme: 'PAE',
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#0f172a',
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0f172a',
    },
    Keyboard: {
      resize: 'body',
      resizeOnFullScreen: true,
    },
  },
};

export default config;
