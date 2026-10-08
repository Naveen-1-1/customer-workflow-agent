# Frontend

React (Vite + TypeScript + Tailwind + shadcn/ui) for the customer chat (`/`) and the supervisor
page (`/supervisor`). It talks to the FastAPI backend through `/api` (proxied to port 8000 in
dev) and follows chats live over server-sent events.

```bash
npm run dev     # http://localhost:5173 (start the backend with `make dev-api`)
npm test        # Vitest + Testing Library
npm run build   # dist/, served by `make serve`
```

See the project [README](../README.md) for the full picture.
