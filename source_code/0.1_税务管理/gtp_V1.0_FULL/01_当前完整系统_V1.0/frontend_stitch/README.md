<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/d8293ed4-7c7c-4cbc-80f9-311aa1eacd48

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in [.env.local](.env.local) to your Gemini API key
3. Run the app:
   `npm run dev`

## Tax API data contract

The Tax backend currently exposes project data by numeric ID (`/api/projects/{id}`).
When `VITE_PROJECT_IDS` is empty, the frontend first tries a real `/api/projects`
collection JSON response and extracts only positive IDs returned by that response.
The current backend has no such collection endpoint, so the dashboard remains
`UNAVAILABLE` until `VITE_PROJECT_IDS` is set to real database IDs. No demo IDs or
financial fallback values are used. AI Assistant and AI Review requests leave the
endpoint unset so the backend model pool can choose the active endpoint at runtime;
the response panel displays backend-provided endpoint, fallback, degraded, and
attempt metadata when available.

The current backend exposes `/risks`, `/tax-ledger`, and `/audit` as HTML pages only;
it does not expose JSON collection contracts for the React risk, tax-ledger, or audit
views. Those views therefore remain explicitly `UNAVAILABLE` rather than parsing or
fabricating data from HTML.

## Production static deployment

The Tax service serves the React SPA root from
`chengdu_construction_tax_system_v1_0/app/static_dist/index.html` and its `assets/`
directory. Build and synchronize the current frontend with:

```text
npm run sync:tax-static
npm run verify:tax-static
```

The sync script copies only Vite build files (`index.html` and `assets/*`) and keeps a
manifest of files it owns. It refuses to overwrite an untracked target asset and only
removes files listed by its previous manifest. `verify:tax-static` compares SHA-256
checksums between `frontend_stitch/dist` and the backend static directory. Set
`TAX_STATIC_DIST_DIR` to override the target directory when deploying elsewhere.

Verification commands:

```text
npm run lint
npm test
npm run build
npm run verify:tax-static
```
