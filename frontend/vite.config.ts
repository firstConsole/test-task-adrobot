import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// Where compose publishes the api service on the host.
const API_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => {
  // The repository root holds the one .env; `''` reads every name and not only VITE_ ones.
  // This file runs in Node, so nothing read here can reach the bundle.
  const env = loadEnv(mode, fileURLToPath(new URL('..', import.meta.url)), '')
  const token = env.ADROBOT_ACCESS_TOKEN

  if (!token) {
    console.warn('[adrobot] no ADROBOT_ACCESS_TOKEN in the root .env — /api will answer 401')
  }

  return {
    plugins: [react()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    // Same-origin in dev as in production: the browser calls /api and nothing else, and the
    // proxy — nginx in production, this one in dev — is what holds the shared token. No
    // VITE_ variable exists, so neither the token nor the tracker URL can be bundled.
    server: {
      proxy: {
        '/api': {
          target: API_ORIGIN,
          ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
        },
      },
    },
  }
})
