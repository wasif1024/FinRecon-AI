# FinRecon AI dashboard

Next.js 16 (App Router, Cache Components) with TypeScript, Tailwind 4 and shadcn/ui.

## How it talks to the API

The browser never holds the API token. Signing in runs a Server Action that calls `POST /auth/login` on the
FastAPI backend and stores the token in an httpOnly cookie. Server Components and Server Actions read the cookie
and call the API through the typed client in `src/lib/api/` (generated from the backend's OpenAPI schema).
`src/lib/dal.ts` is the single place that resolves the current user; navigation is filtered by permission for
convenience only, since the API enforces every permission itself. Tenant data is fetched per request and never
cached on the server.

## Develop

```bash
cp .env.example .env.local        # API_URL of the FastAPI server
pnpm install
pnpm gen:api                      # regenerate API types after backend changes
pnpm dev                          # http://localhost:3000 (run `uv run finrecon serve` for the API)
pnpm typecheck && pnpm lint
```

## End-to-end tests

Run against a live API and the production build (or a deployed stack via `E2E_BASE_URL`):

```bash
pnpm build
E2E_PASSWORD=<demo users' password> pnpm e2e
```
