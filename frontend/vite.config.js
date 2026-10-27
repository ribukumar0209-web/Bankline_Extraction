import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // Build output goes directly into the backend's static folder
    // so FastAPI serves the latest frontend without a manual copy step.
    outDir: path.resolve(__dirname, '../backend/static'),
    emptyOutDir: true,
  },
})
