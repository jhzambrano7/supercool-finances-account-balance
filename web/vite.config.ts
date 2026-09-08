import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// No CORS middleware exists on the backend (docs/web-ui-plan.md §1.5) --
// deliberately not asking for one, since that would be a production-affecting
// change to satisfy a demo surface. The dev server proxies instead, so the
// browser only ever makes same-origin requests.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/accounts': 'http://localhost:8000',
      '/transfers': 'http://localhost:8000',
      '/deposits': 'http://localhost:8000',
      '/withdrawals': 'http://localhost:8000',
    },
  },
})
