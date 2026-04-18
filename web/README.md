# Nellie Web

Static marketing + console surface for Nellie (Karna's AI agent harness).
Deployed to Cloudflare Pages at `nellie.kaeva.app`.

## Stack
- Next.js 15 (App Router, static export)
- TypeScript
- Tailwind CSS 4

## Develop
```bash
cd web
npm install
npm run dev      # http://localhost:3000
```

## Build
```bash
npm run build    # emits ./out via next.config.ts `output: 'export'`
```

CI builds and deploys on push to `main` when `web/**` changes
(see `.github/workflows/web-deploy.yml`).
