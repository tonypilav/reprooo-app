import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { resolveApiBaseUrl } from './src/apiConfig.js'

export default defineConfig(({ mode }) => ({
  plugins: [react(), {
    name: 'validate-api-url',
    configResolved(config) {
      if (config.command === 'build') {
        resolveApiBaseUrl(loadEnv(mode, config.envDir, 'VITE_').VITE_API_BASE_URL, true)
      }
    },
  }],
}))
