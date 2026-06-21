# Ads Analytics Reliability Design

## Goal

Make the Kaspi ads analytics module trustworthy without running a full local scrape on the MacBook.

## Constraints

- Local development database is not production-current.
- Do not require a full local Kaspi Marketing scrape for validation.
- Keep the fix focused on data correctness, API consistency, and diagnostics.
- Preserve the existing SQLite/DAO/API/TMA architecture.

## Design

The analytics pipeline should treat a real Kaspi product SKU as the stable identity key. Scraper fallback IDs like `RPT-*` may be stored only when no reliable SKU can be resolved, and diagnostics must make those cases visible.

The API and processor should use one shared interpretation of bonus sources:

- `kaspi_bonus_seller`
- `kaspi_bonus_review`
- legacy `kaspi_bonus`

For aggregate "has any bonus" logic, any active row from those sources counts as an active bonus. For seller/review-specific filters, the existing `/api/products?missing=...` behavior remains source-specific.

Period handling stays based on `ads_data.period_days`. Local tests use synthetic rows for 7-day and 30-day snapshots instead of requiring a real scrape.

## Components

- `scraper/marketing.py`: normalize parsed report rows against local `products` before saving, and expose scrape-quality diagnostics.
- `database/ads_data.py`: add reusable bonus-source constants and source-aware status methods.
- `analytics/processor.py`: use the unified bonus status when calculating "no bonus" products.
- `api/routes.py`: fix `/api/ads/wasted-budget` dependency bug and keep endpoints consistent.
- Tests: cover API bug, bonus source unification, report period behavior, and SKU normalization.

## Rollout

Local:

1. Run unit/API tests against synthetic databases.
2. Run migrations only on copied databases.
3. Do not run a full local scrape.

Production:

1. Backup the production database.
2. Deploy code.
3. Apply migrations.
4. Run diagnostic report before scrape.
5. Run full scrape on the server.
6. Check diagnostics: source counts, `period_days`, `RPT-*` ratio, product SKU match ratio, and marketing/bonus overlap.
