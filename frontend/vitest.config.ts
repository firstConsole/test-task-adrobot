import { fileURLToPath } from 'node:url'

import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config.ts'

/**
 * The tests run against the app's own resolver and plugins, merged from `vite.config.ts`, so
 * an alias or a transform that works in the browser cannot quietly differ here.
 *
 * A separate file rather than a `test` block in the app config: the app config is what the
 * Docker build reads, and it has no business importing a test runner's types to do it.
 */
export default defineConfig((env) =>
  mergeConfig(viteConfig(env), {
    test: {
      environment: 'jsdom',
      setupFiles: [fileURLToPath(new URL('./src/shared/test/setup.ts', import.meta.url))],
      css: false,
      restoreMocks: true,
      unstubGlobals: true,
    },
  }),
)
