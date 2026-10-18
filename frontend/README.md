# SmartRouteNYC web app

Next.js (App Router) and TypeScript front end for the SmartRouteNYC API.

```bash
npm ci
npm run dev            # http://localhost:3000, expects the API at http://localhost:8000
```

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | SmartRouteNYC API |
| `NEXT_PUBLIC_MAP_STYLE_URL` | OpenFreeMap Positron | MapLibre style; any OpenStreetMap vector style works |

Both are read at build time.

## Scripts

| Command | What it does |
|---|---|
| `npm run typecheck` | Strict TypeScript |
| `npm run lint` | ESLint (Next.js and React rules) |
| `npm test` | Component and unit tests (Vitest, Testing Library) |
| `npm run test:e2e` | Browser tests against a mocked API, including automated accessibility checks (Playwright, axe) |
| `npm run api:types` | Regenerate `src/lib/api-types.ts` from `src/lib/openapi.json` |

The API types come from the backend's OpenAPI document, so a changed field fails the type check:

```bash
cd ../backend && uv run python -m scripts.export_openapi ../frontend/src/lib/openapi.json
cd ../frontend && npm run api:types
```
