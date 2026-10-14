import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dashboard runs at http://localhost:5175 (npm run dev) and calls the FastAPI backend (backend/main.py)
// directly at http://localhost:8000; set VITE_API_URL to use another address. The backend's CORS settings allow
// this origin. Port 5175 avoids the lecture 13 dashboard (5173) and Homework 4 (5174), and `npm run preview`
// uses it too, so the built dashboard has the same allowed origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5175, strictPort: true },
  preview: { port: 5175, strictPort: true },
})
