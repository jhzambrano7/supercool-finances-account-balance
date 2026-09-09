import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// No CORS middleware exists on the backend (docs/web-ui-plan.md §1.5) --
// deliberately not asking for one, since that would be a production-affecting
// change to satisfy a demo surface. The dev server proxies instead, so the
// browser only ever makes same-origin requests.
//
// The target is an env var because the backend is not always at the same address: it is
// `localhost:8000` when both halves run on the host, and `http://api:8000` when the stack runs
// under docker compose, where `localhost` inside the web container is the web container itself.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/accounts': apiTarget,
      '/transfers': apiTarget,
      '/deposits': apiTarget,
      '/withdrawals': apiTarget,
    },
  },
})
