# Ads Analytics Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix analytics correctness around bonus detection, report periods, wasted-budget API failures, and scraper product identity without requiring a full local scrape.

**Architecture:** Keep the current layers. Add narrowly scoped helpers in DAO/scraper code, then update processor/API consumers to use those helpers. Validate with synthetic SQLite databases and targeted pytest runs.

**Tech Stack:** Python 3.12, SQLite, aiosqlite, aiohttp, pytest, existing KaspiBot modules.

---

### Task 1: Regression Tests

**Files:**
- Modify: `tests/test_phase12_processor.py`
- Modify: `tests/test_phase13_api.py`
- Modify: `tests/test_phase11_scraper.py`

- [ ] Add tests proving bonus status includes `kaspi_bonus_seller` and `kaspi_bonus_review`.
- [ ] Add tests proving no-bonus analytics excludes products with either active bonus source.
- [ ] Add an API test proving `/api/ads/wasted-budget` returns 200 and includes titles.
- [ ] Add scraper tests proving parsed `RPT-*` or short SKU rows can be normalized to a real product SKU from `products`.
- [ ] Run the new tests and confirm they fail for the expected reasons.

### Task 2: DAO And Processor Fixes

**Files:**
- Modify: `database/ads_data.py`
- Modify: `analytics/processor.py`

- [ ] Add shared bonus source constants.
- [ ] Update bonus status queries to include seller, review, and legacy sources.
- [ ] Keep legacy behavior for old rows.
- [ ] Update processor no-bonus logic to use the unified status.
- [ ] Run phase 12 processor tests.

### Task 3: API Fix

**Files:**
- Modify: `api/routes.py`
- Modify: `tests/test_phase13_api.py`

- [ ] Read `ads_db` from route dependencies in `_handle_wasted_budget`.
- [ ] Keep response shape unchanged.
- [ ] Run the targeted API test with localhost permissions if the sandbox blocks binding.

### Task 4: Scraper SKU Normalization And Diagnostics

**Files:**
- Modify: `scraper/marketing.py`
- Modify: `tests/test_phase11_scraper.py`

- [ ] Add helpers to identify real Kaspi SKU values.
- [ ] Build a product lookup from `products.master_sku`, exact SKU-like names, and normalized titles.
- [ ] Normalize parsed `AdCampaignData` and `BonusData` rows before returning from drill-down report parsing.
- [ ] Add a diagnostic summary method for parsed rows: total, real SKU count, `RPT-*` count, and product match count.
- [ ] Run scraper tests.

### Task 5: Verification

**Files:**
- No production files unless tests reveal a focused gap.

- [ ] Run `tests/test_phase11_scraper.py`.
- [ ] Run `tests/test_phase12_processor.py`.
- [ ] Run targeted phase 13 API tests.
- [ ] Run a copied-DB migration smoke check, not the live local DB.
- [ ] Summarize local verification and production rollout steps.
