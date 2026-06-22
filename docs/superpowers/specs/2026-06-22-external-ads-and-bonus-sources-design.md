# External Ads And Bonus Sources Design

## Goal

Preserve both Kaspi bonus types for the same product, parse external-report spend
from the monetary column, and collect external advertising without relying on
campaign detail links that the external Kaspi application does not expose.

## Design

Bonus rows remain separate by `source`. Deduplication only collapses repeated
rows of the same source, SKU, and report period; a review bonus and a seller
bonus for one SKU are both returned and saved.

The spend-column resolver treats labels containing `доля`, `%`, or `DRR` as
ratios, never as monetary spend. A plain `Стоимость` column is accepted as
spend when the report does not contain the usual `Расходы на рекламу` column.

External advertising has a dedicated collection path. Its SPA requests the
authenticated campaign list from `/external/advertising/products/api/v1/merchant/<id>/campaigns`.
The scraper captures that response while opening the page, extracts campaign
IDs, then downloads the product report for every campaign using the same
authenticated browser context. The generic campaign-link drill-down remains for
internal advertising. If the list response is absent or unreadable, the scraper
logs a precise external-collection warning and returns no external rows rather
than reporting a false successful collection.

## Validation

Unit tests cover source-aware bonus deduplication, `Стоимость` versus `Доля
рекламных расходов`, and the external-page report path with a fake Playwright
page. Production validation is one server-side scrape after deployment, then
comparison of the external source count and spend against the Kaspi exports.
