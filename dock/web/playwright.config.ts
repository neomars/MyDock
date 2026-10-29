import { defineConfig } from '@playwright/test'

// Écran du dock : 1024x600, tactile, un pixel CSS = un pixel physique.
export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    viewport: { width: 1024, height: 600 },
    deviceScaleFactor: 1,
    hasTouch: true,
    isMobile: false,
    locale: 'fr-FR',
    timezoneId: 'Europe/Paris',
    colorScheme: 'dark',
    launchOptions: {
      args: ['--force-device-scale-factor=1', '--disable-pinch', '--hide-scrollbars'],
    },
  },
})
