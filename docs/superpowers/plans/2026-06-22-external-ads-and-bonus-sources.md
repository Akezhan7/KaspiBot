# External Ads And Bonus Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve both bonus types per SKU, parse external-report spend correctly, and collect external advertising from its own export flow.

**Architecture:** Keep the internal-advertising drill-down unchanged. Make bonus deduplication source-aware, make spend-header selection reject ratio columns, and capture the external SPA campaign-list response before downloading a product report for each returned campaign.

**Tech Stack:** Python 3.12, Playwright, SQLite, pytest.

---

### Task 1: Bonus Source Regression

**Files:**
- Modify: `tests/test_phase11_scraper.py`
- Modify: `scraper/marketing.py`

- [ ] Add a test where seller and review bonuses share one SKU and assert that both are returned.
- [ ] Run `./.venv/bin/python -m pytest tests/test_phase11_scraper.py -k bonus_deduplicate -q` and confirm it fails because the current key is only SKU.
- [ ] Change `_deduplicate_bonuses` to key rows by `(product_sku, source, period_days)` and preserve the most informative row within that key.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: External Report Spend Regression

**Files:**
- Modify: `tests/test_phase11_scraper.py`
- Modify: `scraper/marketing.py`

- [ ] Add a test with `Стоимость=2041.01` and `Доля рекламных расходов=2.1` and assert the parsed spend is `2041.01`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_phase11_scraper.py -k spend_column -q` and confirm it fails because `Доля рекламных расходов` currently wins.
- [ ] Update `_find_spend_column` to reject ratio headers and prefer monetary `Стоимость` after explicit spend headers.
- [ ] Re-run the focused test and confirm it passes.

### Task 3: External Advertising Collection

**Files:**
- Modify: `tests/test_phase11_scraper.py`
- Modify: `scraper/marketing.py`

- [ ] Add a test that extracts campaign IDs from the captured external API payload and builds a product-report URL containing the campaign ID.
- [ ] Run the focused test and confirm it fails because the helpers do not yet exist.
- [ ] Add `_scrape_external_marketing`, call it from `scrape_marketing` for the external URL, capture the authenticated campaign-list response, and download each campaign product report for both configured periods.
- [ ] Keep a warning with the external URL when the campaign-list response is absent, unreadable, or produces no report rows.
- [ ] Re-run the focused test and confirm it passes.

### Task 4: Verification

**Files:**
- No production files unless a focused test reveals a defect.

- [ ] Run `./.venv/bin/python -m pytest tests/test_phase11_scraper.py -q`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_phase12_processor.py tests/test_phase13_api.py -q`.
- [ ] Run `git diff --check`.
- [ ] Summarize the server deployment and one-scrape verification procedure.
