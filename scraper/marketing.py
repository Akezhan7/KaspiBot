"""
Marketing Scraper — сбор данных из Kaspi Pay кабинета.

Разделы: «Kaspi Marketing» (рекламные кампании) и «Бонусы».
Поддерживает бесконечный скролл для загрузки 600+ товаров.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import re
import tempfile
import zlib
from io import BytesIO
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
import zipfile

import aiosqlite
from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from config import Config, now_kz
from scraper.models import AdCampaignData, BonusData, ScrapeResult

logger = logging.getLogger(__name__)

# URL-ы legacy-разделов Kaspi Pay кабинета (fallback)
_CANONICAL_MARKETING_PRODUCTS_URL = "https://marketing.kaspi.kz/external/advertising/products"
_CANONICAL_BONUSES_REVIEWS_URL = "https://marketing.kaspi.kz/bonuses/reviews/promotions/list?state=Enabled"
_CANONICAL_BONUSES_PRODUCTS_URL = "https://marketing.kaspi.kz/bonuses/products/promotions/list?state=Enabled"
_LEGACY_PROMOTIONS_SHOP_URL = "https://marketing.kaspi.kz/promotions/shop/list"
_LEGACY_MARKETING_URL = "https://kaspi.kz/mc/marketing/ads"
_LEGACY_BONUSES_URL = "https://kaspi.kz/mc/marketing/bonus"
_LEGACY_BONUS_URLS = (_LEGACY_PROMOTIONS_SHOP_URL, _LEGACY_BONUSES_URL)

# Идентификаторы источников бонусов (используются как ads_data.source)
_BONUS_SOURCE_SELLER = "kaspi_bonus_seller"   # /bonuses/products/ — бонус от продавца
_BONUS_SOURCE_REVIEW = "kaspi_bonus_review"   # /bonuses/reviews/ — бонус за отзыв
_BONUS_SOURCE_LEGACY = "kaspi_bonus"          # неопределённый раздел (legacy)


def _bonus_source_for_url(url: str) -> str:
    """Определить тип бонуса по URL раздела."""
    u = (url or "").lower()
    if "/bonuses/reviews" in u:
        return _BONUS_SOURCE_REVIEW
    if "/bonuses/products" in u:
        return _BONUS_SOURCE_SELLER
    return _BONUS_SOURCE_LEGACY


# Таймауты (мс)
_PAGE_LOAD_TIMEOUT = 60_000
_CONTENT_WAIT_TIMEOUT = 30_000
_SCROLL_PAUSE_MS = 1_500

# Максимум итераций скролла — защита от бесконечного цикла
_MAX_SCROLL_ITERATIONS = 500

# Drill-down: максимум кампаний/акций за один сеанс и задержка между ними
_MAX_CAMPAIGN_ENTRIES = 300
_CAMPAIGN_NAV_DELAY_MS = 800

_XLSX_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# Поддерживаем старый и новый формат URL отчёта (включая /api/v1/merchant/<id>/...)
_REPORT_URL_PATTERN = re.compile(
    r"(?:https://marketing\.kaspi\.kz)?(?:/external/advertising/products(?:/api/v1/merchant/\d+)?|/api/v1/merchant/\d+)/reports/overview/xlsx(?:\?[^\"'\s<]*)?"
)
_GENERIC_REPORT_URL_PATTERN = re.compile(
    r"(?:https://marketing\.kaspi\.kz)?/[^\"'\s<]*(?:reports?|export|download)[^\"'\s<]*(?:xlsx|csv|format=xlsx|format=csv)?(?:\?[^\"'\s<]*)?"
)

_BONUS_STATUS_ACTIVE_KEYWORDS = {"активен", "активна", "включён", "включен", "active", "вкл"}
_BONUS_STATUS_INACTIVE_KEYWORDS = {"неактивен", "отключён", "отключен", "inactive", "disabled", "выкл"}


def _parse_number(raw: str) -> float:
    """Парсинг числа из строки вида '1 234,56 ₸' или '2,5 %'."""
    if not raw:
        return 0.0
    # Убираем пробелы (разделители тысяч), символ валюты, процент
    cleaned = re.sub(r"[^\d,.]", "", raw.replace("\u00a0", "").replace(" ", ""))
    # Заменяем запятую на точку (казахстанский формат)
    cleaned = cleaned.replace(",", ".")
    # Если несколько точек — оставляем только последнюю (артефакт)
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _parse_int(raw: str) -> int:
    return int(_parse_number(raw))


class MarketingScraper:
    """Скрапер данных маркетинга и бонусов из Kaspi Pay кабинета."""

    def __init__(self, browser_context: BrowserContext, db_path: str) -> None:
        self._context = browser_context
        self._db_path = db_path

    async def _random_delay(self) -> None:
        """Случайная пауза для имитации человеческого поведения."""
        await asyncio.sleep(
            random.uniform(Config.SCRAPE_ACTION_DELAY_MIN, Config.SCRAPE_ACTION_DELAY_MAX)
        )

    @staticmethod
    def _marketing_urls() -> list[str]:
        """Все разделы рекламных кампаний — drill-down проходит по каждому.

        Включает:
        1. Реклама товаров (внутренняя) — /advertising/campaigns
        2. Внешняя реклама — /external/advertising/products/campaigns
        """
        candidates = [
            "https://marketing.kaspi.kz/advertising/campaigns?activeTab=Enabled",
            "https://marketing.kaspi.kz/external/advertising/products/campaigns?tab=overview&activeTab=Enabled",
            Config.KASPI_MARKETING_ADS_URL.strip(),
        ]
        return [url for idx, url in enumerate(candidates) if url and url not in candidates[:idx]]

    @staticmethod
    def _bonus_urls() -> list[str]:
        """Список URL для разделов бонусов (обе вкладки + fallback)."""
        candidates = [
            Config.KASPI_BONUSES_REVIEWS_URL.strip(),
            Config.KASPI_BONUSES_PRODUCTS_URL.strip(),
            _CANONICAL_BONUSES_REVIEWS_URL,
            _CANONICAL_BONUSES_PRODUCTS_URL,
            _LEGACY_PROMOTIONS_SHOP_URL,
            _LEGACY_BONUSES_URL,
        ]
        return [url for idx, url in enumerate(candidates) if url and url not in candidates[:idx]]

    async def scrape_marketing(self) -> list[AdCampaignData]:
        """Собрать данные из всех разделов рекламы (внутренняя + внешняя).

        Заходим в КАЖДУЮ кампанию из «Мои кампании / Активные» (по обоим
        разделам), скачиваем XLSX-отчёт изнутри. Источник кампании
        определяется по URL: /external/ → kaspi_external_ads, иначе kaspi_marketing.
        """
        page: Page | None = None
        all_products: list[AdCampaignData] = []
        try:
            page = await self._context.new_page()
            for url in self._marketing_urls():
                source = "kaspi_external_ads" if "/external/" in url else "kaspi_marketing"
                logger.info(
                    "MarketingScraper: переход на страницу маркетинга %s (source=%s)",
                    url, source,
                )
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                    await self._random_delay()
                except Exception as exc:
                    logger.warning("MarketingScraper: не удалось открыть %s: %s", url, exc)
                    continue

                products = await self._scrape_all_campaign_drilldown(page, source=source)
                if products:
                    logger.info(
                        "MarketingScraper: %s → %d товаров из %d уникальных SKU",
                        source, len(products), len({r.product_sku for r in products}),
                    )
                    all_products.extend(products)
                else:
                    logger.warning(
                        "MarketingScraper: drill-down не нашёл кампании на %s", url,
                    )

            if not all_products:
                logger.error(
                    "MarketingScraper: ни в одном разделе не удалось собрать товары. "
                    "Проверьте: 1) залогинен ли кабинет, 2) есть ли активные кампании, "
                    "3) совпадают ли селекторы со структурой DOM."
                )
            else:
                await self._normalize_report_product_skus(all_products)
                diagnostics = await self.get_report_identity_diagnostics(all_products)
                logger.info(
                    "MarketingScraper: всего собрано %d товаров (внутренняя+внешняя реклама), "
                    "real_sku=%d, surrogate=%d, matched_products=%d",
                    len(all_products),
                    diagnostics["real_sku_count"],
                    diagnostics["surrogate_sku_count"],
                    diagnostics["product_match_count"],
                )
            return all_products

        except PlaywrightTimeoutError as e:
            logger.error("MarketingScraper: таймаут при сборе маркетинга: %s", e)
            return all_products
        except Exception as e:
            logger.error("MarketingScraper: ошибка сбора маркетинга: %s", e, exc_info=True)
            return all_products
        finally:
            if page:
                await page.close()

    async def scrape_bonuses(self) -> list[BonusData]:
        """Собрать данные из раздела «Бонусы».

        Новый подход: заходим в каждую акцию из «Мои акции» и скачиваем
        отчёт изнутри — там реальные товары с процентами бонусов.
        Fallback: старый метод (скролл/xlsx верхнего уровня).
        """
        page: Page | None = None
        try:
            page = await self._context.new_page()
            collected: list[BonusData] = []

            for url in self._bonus_urls():
                if collected and url in _LEGACY_BONUS_URLS:
                    logger.info(
                        "MarketingScraper: пропускаем legacy URL бонусов %s, данные уже собраны",
                        url,
                    )
                    continue

                logger.info("MarketingScraper: переход на страницу бонусов %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                await self._random_delay()

                bonus_source = _bonus_source_for_url(url)

                # 1) Drill-down в каждую акцию → реальные товары
                drill_rows, drill_attempted = await self._scrape_all_bonus_drilldown(
                    page, source=bonus_source,
                )
                if drill_rows:
                    logger.info(
                        "MarketingScraper: drill-down дал %d бонусных товаров с %s (source=%s)",
                        len(drill_rows), url, bonus_source,
                    )
                    collected.extend(drill_rows)
                    continue

                # Если drill-down реально проходил по акциям, но не дал товаров —
                # НЕ делаем fallback на верхнеуровневый XLSX (там названия акций, не товаров).
                if drill_attempted:
                    logger.warning(
                        "MarketingScraper: drill-down прошёл по акциям на %s, но 0 товаров. "
                        "Fallback на верхний XLSX отключён (он содержит названия акций).",
                        url,
                    )
                    continue

                # 2) Fallback: DOM верхнего уровня (только если drill-down НЕ нашёл список)
                has_content = await self._wait_for_content(page)
                dom_rows: list[BonusData] = []
                if has_content:
                    dom_rows = await self._scroll_and_collect_bonus_rows(page)
                    for r in dom_rows:
                        r.source = bonus_source
                    logger.info(
                        "MarketingScraper: бонусный URL %s дал %d строк из DOM (fallback, source=%s)",
                        url, len(dom_rows), bonus_source,
                    )
                    collected.extend(dom_rows)
                else:
                    logger.warning(
                        "MarketingScraper: URL бонусов без DOM-контента, пробуем отчёт: %s", url,
                    )

                if dom_rows:
                    continue

                # 3) Fallback: xlsx верхнего уровня
                report_rows = await self._collect_bonuses_from_report(page)
                if report_rows:
                    for r in report_rows:
                        r.source = bonus_source
                    logger.info(
                        "MarketingScraper: бонусный URL %s дал %d строк из отчёта (fallback, source=%s)",
                        url, len(report_rows), bonus_source,
                    )
                    collected.extend(report_rows)
                elif not has_content:
                    logger.warning(
                        "MarketingScraper: пропуск URL бонусов без контента: %s", url,
                    )

            await self._normalize_report_product_skus(collected)
            diagnostics = await self.get_report_identity_diagnostics(collected)
            logger.info(
                "MarketingScraper: bonus identity diagnostics: total=%d, real_sku=%d, "
                "surrogate=%d, matched_products=%d",
                diagnostics["total"],
                diagnostics["real_sku_count"],
                diagnostics["surrogate_sku_count"],
                diagnostics["product_match_count"],
            )

            bonus_data = self._deduplicate_bonuses(collected)
            logger.info("MarketingScraper: собрано %d бонусных записей", len(bonus_data))
            return bonus_data

        except PlaywrightTimeoutError as e:
            logger.error("MarketingScraper: таймаут при сборе бонусов: %s", e)
            return []
        except Exception as e:
            logger.error("MarketingScraper: ошибка сбора бонусов: %s", e, exc_info=True)
            return []
        finally:
            if page:
                await page.close()

    async def scrape_all(self) -> ScrapeResult:
        """Полный цикл сбора данных: маркетинг + бонусы.

        Возвращает ScrapeResult с объединёнными данными и ошибками.
        """
        result = ScrapeResult(scraped_at=datetime.now())

        logger.info("MarketingScraper: начало полного цикла скрапинга")

        # Сбор рекламных кампаний
        try:
            result.campaigns = await self.scrape_marketing()
        except Exception as e:
            msg = f"Ошибка сбора маркетинга: {e}"
            logger.error("MarketingScraper: %s", msg, exc_info=True)
            result.add_error(msg)

        # Сбор данных о бонусах
        try:
            result.bonuses = await self.scrape_bonuses()
        except Exception as e:
            msg = f"Ошибка сбора бонусов: {e}"
            logger.error("MarketingScraper: %s", msg, exc_info=True)
            result.add_error(msg)

        logger.info(
            "MarketingScraper: завершён. Кампаний: %d, бонусов: %d, ошибок: %d",
            len(result.campaigns),
            len(result.bonuses),
            len(result.errors),
        )
        return result

    # -------------------------------------------------------------------------
    # Drill-down: обход каждой кампании/акции и скачивание отчёта изнутри
    # -------------------------------------------------------------------------

    async def _scrape_all_campaign_drilldown(
        self, page: Page, source: str = "kaspi_marketing"
    ) -> list[AdCampaignData]:
        """Собрать ссылки на кампании → зайти в каждую → скачать XLSX → вернуть товары."""
        list_url = page.url
        entries = await self._collect_list_entries(page)
        if not entries:
            return []

        logger.info("MarketingScraper: drill-down маркетинг: %d кампаний", len(entries))
        all_products: list[AdCampaignData] = []

        for i, entry in enumerate(entries):
            try:
                rows = await self._scrape_entry_products_marketing(page, entry, source)
                all_products.extend(rows)
                logger.info(
                    "MarketingScraper: кампания %d/%d «%s» → %d товаров",
                    i + 1, len(entries), entry["name"][:50], len(rows),
                )
            except Exception as exc:
                logger.warning(
                    "MarketingScraper: ошибка обработки кампании «%s»: %s",
                    entry.get("name", "?")[:50], exc,
                )
            finally:
                # Возврат на список кампаний
                try:
                    await page.goto(list_url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                    await page.wait_for_timeout(_CAMPAIGN_NAV_DELAY_MS)
                except Exception:
                    pass

        return all_products

    async def _scrape_all_bonus_drilldown(
        self, page: Page, source: str = _BONUS_SOURCE_LEGACY,
    ) -> tuple[list[BonusData], bool]:
        """Drill-down по акциям. Возвращает (товары, attempted).

        attempted=True если нашли список акций (даже если по 0 товаров на каждой):
        в таком случае НЕ нужен fallback на верхний XLSX (там названия акций, не товаров).

        Параметр source — точный идентификатор раздела бонусов
        (kaspi_bonus_seller / kaspi_bonus_review / kaspi_bonus). Применяется к
        каждой записи + сохраняется название акции в campaign_name.
        """
        list_url = page.url
        entries = await self._collect_list_entries(page)
        if not entries:
            return [], False

        logger.info("MarketingScraper: drill-down бонусы: %d акций (source=%s)", len(entries), source)
        all_bonuses: list[BonusData] = []

        for i, entry in enumerate(entries):
            try:
                rows = await self._scrape_entry_products_bonus(page, entry)
                campaign_name = entry.get("name", "")
                for r in rows:
                    r.source = source
                    if campaign_name:
                        r._campaign_name = campaign_name  # type: ignore[attr-defined]
                all_bonuses.extend(rows)
                logger.info(
                    "MarketingScraper: акция %d/%d «%s» → %d товаров",
                    i + 1, len(entries), campaign_name[:50], len(rows),
                )
            except Exception as exc:
                logger.warning(
                    "MarketingScraper: ошибка обработки акции «%s»: %s",
                    entry.get("name", "?")[:50], exc,
                )
            finally:
                try:
                    await page.goto(list_url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                    await page.wait_for_timeout(_CAMPAIGN_NAV_DELAY_MS)
                except Exception:
                    pass

        return all_bonuses, True

    # Слова в названии, по которым отсеиваем ложные «ссылки» (кнопки и т.п.)
    _BAD_ENTRY_NAMES = (
        "скачать отчет", "скачать отчёт", "скачать", "выгрузить", "экспорт",
        "создать новую", "создать акцию", "создать кампанию", "новая акция",
        "новая кампания", "добавить", "редактировать", "настройки",
        "download", "export", "create", "edit",
        "главная", "главное", "home", "назад", "back",
        "войти", "login", "logout", "выйти",
        "помощь", "поддержка", "help", "support",
    )

    @classmethod
    def _is_bad_entry_name(cls, name: str) -> bool:
        n = (name or "").strip().lower()
        if not n or len(n) < 2:
            return True
        return any(bad in n for bad in cls._BAD_ENTRY_NAMES)

    async def _collect_list_entries(self, page: Page) -> list[dict[str, str]]:
        """Сбор ссылок {url, name} на детальные страницы кампаний/акций.

        Алгоритм (устойчивый к изменениям вёрстки):

        1. Дождаться появления первых ссылок-кандидатов в DOM.
        2. Прокручивать ВСЕ скролл-контейнеры на странице (включая виртуализированные
           Vue/React-скроллеры) + window. На каждой итерации забирать видимые
           ссылки в накопительный Set. Стоп — когда Set перестаёт расти.
        3. Из ожидаемого числа кампаний (чип «Активные (N)») понимаем, сколько
           ещё нужно — если уже набрали ≥ N, заканчиваем досрочно.

        Идентификация ссылки кампании — по структуре URL:
          - тот же origin, что у базы;
          - первый сегмент пути совпадает с разделом базы (`advertising`, `bonuses`,
            `external`, `promotions`);
          - URL отличается от базы;
          - последний сегмент пути — числовой ID или slug (не служебное слово
            `list`/`overview`/`reports`/`api`/...);
          - ссылка НЕ из явной навигации (header/nav/aside/footer).
        """
        base_url = page.url

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # Сколько активных кампаний ожидаем (читаем из чипа «Активные (N)»)
        expected = await self._read_expected_count(page)
        if expected:
            logger.info(
                "MarketingScraper: ожидаемое число активных кампаний на %s: %d",
                base_url, expected,
            )

        # Накопительный сбор: скроллим все контейнеры + читаем DOM на каждой итерации
        collected = await self._collect_links_with_scroll(page, base_url, expected)

        # Финальная пост-фильтрация по «плохим именам» и слайс
        entries: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for url, name in collected.items():
            if url in seen_urls:
                continue
            if self._is_bad_entry_name(name):
                continue
            seen_urls.add(url)
            entries.append({"url": url, "name": name})
            if len(entries) >= _MAX_CAMPAIGN_ENTRIES:
                break

        if entries:
            sample = " | ".join(e["name"][:40] for e in entries[:3])
            logger.info(
                "MarketingScraper: _collect_list_entries: %d ссылок на %s | примеры: %s",
                len(entries), base_url, sample,
            )
            return entries

        # Диагностика: сохраняем HTML и логируем структуру
        await self._log_zero_links_diagnostics(page, base_url)
        return []

    async def _read_expected_count(self, page: Page) -> int | None:
        """Читает ожидаемое число активных кампаний из чипа `Активные (N)`."""
        try:
            count = await page.evaluate(
                r"""() => {
                    const re = /(?:актив|active)[^\d]*(\d+)/i;
                    const els = document.querySelectorAll('button, [role="checkbox"], [class*="chip" i], [class*="tab" i]');
                    for (const el of els) {
                        const t = (el.innerText || el.textContent || '').trim();
                        const m = t.match(re);
                        if (m) return parseInt(m[1], 10);
                    }
                    return 0;
                }"""
            )
            return int(count) if count else None
        except Exception:
            return None

    async def _collect_links_with_scroll(
        self, page: Page, base_url: str, expected: int | None
    ) -> dict[str, str]:
        """Накопительный сбор ссылок: прокручиваем все скролл-контейнеры + window.

        Возвращает dict {url: name}. На каждой итерации:
          1. Читаем все детальные ссылки из DOM (по структуре URL).
          2. Если набрали ≥ expected, выходим.
          3. Прокручиваем каждый scrollable-контейнер на 80% его viewport вниз;
             window.scrollBy на высоту окна.
          4. Если за 3 итерации Set не вырос — выходим.
        """
        accumulator: dict[str, str] = {}
        stable_iters = 0
        max_iters = 60  # 60 * 800ms = 48s — лимит на лист

        # Перед началом сбрасываем все скролл-контейнеры в верх
        try:
            await page.evaluate(
                """() => {
                    document.querySelectorAll('*').forEach(el => {
                        const cs = window.getComputedStyle(el);
                        const oy = cs.overflowY;
                        if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight) {
                            el.scrollTop = 0;
                        }
                    });
                    window.scrollTo(0, 0);
                }"""
            )
        except Exception:
            pass

        for _ in range(max_iters):
            # Шаг 1: читаем DOM, накапливаем ссылки
            new_links = await self._extract_list_entries_from_dom(page, base_url)
            grew = False
            for ent in new_links:
                u = ent.get("url")
                n = ent.get("name") or ""
                if not u:
                    continue
                if u not in accumulator:
                    accumulator[u] = n
                    grew = True
                elif not accumulator[u] and n:
                    accumulator[u] = n

            # Если набрали достаточно — заканчиваем
            if expected and len(accumulator) >= expected:
                logger.debug(
                    "MarketingScraper: набрано %d ссылок (≥ ожидаемого %d) — стоп",
                    len(accumulator), expected,
                )
                break

            # Стабилизация: 3 итерации без роста
            if not grew:
                stable_iters += 1
                if stable_iters >= 3:
                    break
            else:
                stable_iters = 0

            # Шаг 2: прокручиваем все контейнеры + window
            try:
                done = await page.evaluate(
                    """() => {
                        let scrolled = 0;
                        const containers = [];
                        document.querySelectorAll('*').forEach(el => {
                            const cs = window.getComputedStyle(el);
                            const oy = cs.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 4) {
                                containers.push(el);
                            }
                        });
                        for (const el of containers) {
                            const before = el.scrollTop;
                            el.scrollTop = Math.min(
                                el.scrollHeight - el.clientHeight,
                                before + Math.max(200, el.clientHeight * 0.8)
                            );
                            if (el.scrollTop !== before) scrolled++;
                        }
                        // Также прокручиваем window
                        const wBefore = window.scrollY;
                        window.scrollBy(0, Math.max(400, window.innerHeight * 0.8));
                        if (window.scrollY !== wBefore) scrolled++;
                        return { scrolled, containers: containers.length };
                    }"""
                )
                logger.debug(
                    "MarketingScraper: прокрутка: контейнеров=%s, прокручено=%s",
                    done.get("containers"), done.get("scrolled"),
                )
            except Exception as exc:
                logger.debug("MarketingScraper: ошибка прокрутки: %s", exc)
                break

            await page.wait_for_timeout(700)

        # Финальный проход — читаем DOM ещё раз, на случай если последняя прокрутка
        # подгрузила что-то новое
        for ent in await self._extract_list_entries_from_dom(page, base_url):
            u = ent.get("url")
            n = ent.get("name") or ""
            if u and u not in accumulator:
                accumulator[u] = n

        return accumulator

    async def _scroll_to_load_all(self, page: Page) -> None:
        """Совместимость со старым кодом: одноразовая прокрутка всех контейнеров.

        Используется только в местах, где нужно догрузить контент перед
        каким-то действием. Основной сбор ссылок теперь идёт через
        `_collect_links_with_scroll`.
        """
        try:
            await page.evaluate(
                """async () => {
                    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                    const containers = [];
                    document.querySelectorAll('*').forEach(el => {
                        const cs = window.getComputedStyle(el);
                        const oy = cs.overflowY;
                        if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 4) {
                            containers.push(el);
                        }
                    });
                    for (let i = 0; i < 30; i++) {
                        let moved = false;
                        for (const el of containers) {
                            const before = el.scrollTop;
                            el.scrollTop = el.scrollHeight;
                            if (el.scrollTop !== before) moved = true;
                        }
                        const wBefore = window.scrollY;
                        window.scrollTo(0, document.body.scrollHeight);
                        if (window.scrollY !== wBefore) moved = true;
                        if (!moved) break;
                        await sleep(400);
                    }
                }"""
            )
        except Exception:
            pass

    async def _log_zero_links_diagnostics(self, page: Page, base_url: str) -> None:
        """Логирует структурную диагностику и сохраняет HTML страницы."""
        try:
            stats = await page.evaluate(
                """() => {
                    const anchors = [...document.querySelectorAll('a[href]')];
                    return {
                        anchors_count: anchors.length,
                        anchors_sample: anchors.slice(0, 15).map(a => a.href),
                        title: document.title,
                    };
                }"""
            )
            logger.warning(
                "MarketingScraper: 0 ссылок на %s | anchors=%s, title=%r | sample_hrefs=%s",
                base_url, stats.get("anchors_count"), stats.get("title"),
                stats.get("anchors_sample", [])[:8],
            )
        except Exception:
            logger.warning("MarketingScraper: 0 ссылок на %s (диагностика недоступна)", base_url)

        try:
            html_content = await page.content()
            html_path = os.path.join("data", "debug_page.html")
            os.makedirs("data", exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("MarketingScraper: HTML страницы сохранён в %s", html_path)
        except Exception as exc:
            logger.debug("MarketingScraper: не удалось сохранить HTML: %s", exc)

    async def _extract_list_entries_from_dom(
        self, page: Page, base_url: str
    ) -> list[dict[str, str]]:
        """Извлечь ссылки и названия деталей кампаний/акций из текущего DOM.

        Концепция (устойчивая к смене классов):
          - origin совпадает с базой;
          - первый сегмент пути (раздел) совпадает с разделом базы;
          - URL отличается от базы;
          - последний сегмент пути — числовой ID или slug, НЕ служебное слово;
          - ссылка НЕ внутри `<header>/<nav>/<aside>/<footer>` или `[role="navigation"]`.

        Не зависит от точных имён классов Kaspi.
        """
        try:
            result = await page.evaluate(
                r"""(baseUrl) => {
                    const entries = [];
                    const seen = new Set();

                    let baseObj;
                    try { baseObj = new URL(baseUrl); } catch { return { entries: [] }; }
                    const baseOrigin = baseObj.origin;
                    const basePath = baseObj.pathname.replace(/\/$/, '');
                    const baseSegments = basePath.split('/').filter(Boolean);
                    const rootSegment = baseSegments[0] || '';

                    // Служебные сегменты — не детальные страницы
                    const badLastSegs = new Set([
                        'overview', 'settings', 'help', 'faq', 'support',
                        'create', 'add', 'new', 'edit', 'analytics',
                        'list', 'campaigns', 'promotions', 'login',
                        'auth', 'profile', 'account', 'csv', 'xlsx',
                        'reports', 'report', 'export',
                    ]);

                    // Явная навигация — только теги, без класс-эвристик
                    const navSelector = 'header, nav, aside, footer, [role="navigation"]';

                    document.querySelectorAll('a[href]').forEach(link => {
                        const href = link.href || '';
                        if (!href || !href.startsWith('http')) return;

                        let urlObj;
                        try { urlObj = new URL(href); } catch { return; }
                        if (urlObj.origin !== baseOrigin) return;

                        const path = urlObj.pathname.replace(/\/$/, '');
                        if (path === basePath) return;

                        // Отсекаем API/служебные пути целиком
                        if (path.includes('/api/') || path.includes('/reports/') ||
                            path.includes('/export/') || path.includes('/download')) return;

                        const pathSegs = path.split('/').filter(Boolean);
                        if (pathSegs.length === 0) return;

                        // Раздел базы должен совпадать с разделом ссылки
                        if (rootSegment && pathSegs[0] !== rootSegment) return;

                        // Путь должен быть НЕ КОРОЧЕ базового. Если такой же длины —
                        // это допустимо (например /promotions/list → /promotions/12345),
                        // но последний сегмент уже отличается, иначе это та же база.
                        if (pathSegs.length < baseSegments.length) return;

                        // Последний сегмент не должен быть служебным словом
                        const lastSeg = pathSegs[pathSegs.length - 1].toLowerCase();
                        if (badLastSegs.has(lastSeg)) return;

                        // Принимаем числовые ID, UUID-подобные хэши, slug длиной 2+.
                        // Главный признак — это явно НЕ служебное слово.
                        if (lastSeg.length < 1) return;

                        // Не из явной навигации
                        if (link.closest(navSelector)) return;

                        if (seen.has(href)) return;
                        seen.add(href);

                        // Имя ищем приоритетно из самой ссылки, потом из ближайшей строки
                        let name = (link.innerText || link.textContent || '').trim();
                        if (!name || name.length < 2) {
                            const row = link.closest(
                                'tr, [role="row"], [class*="row" i], [class*="item" i], [class*="card" i], li'
                            );
                            if (row) {
                                name = (row.innerText || row.textContent || '').trim().split('\n')[0];
                            }
                        }
                        name = (name || '').replace(/\s+/g, ' ').slice(0, 200);
                        if (!name || name.length < 2) return;

                        entries.push({ url: href, name });
                    });

                    return { entries };
                }""",
                base_url,
            )
            if not result:
                return []
            if isinstance(result, dict):
                return result.get("entries") or []
            return result
        except Exception as exc:
            logger.debug("MarketingScraper: ошибка DOM-извлечения ссылок: %s", exc)
            return []

    async def _scrape_entry_products_marketing(
        self,
        page: Page,
        entry: dict[str, str],
        source: str,
        periods: list[int] | None = None,
    ) -> list[AdCampaignData]:
        """Зайти на страницу кампании, скачать XLSX за КАЖДЫЙ период, вернуть товары.

        Args:
            page: текущая страница Playwright.
            entry: словарь со ссылкой на кампанию (`url`, `name`).
            source: значение колонки ads_data.source (kaspi_marketing /
                kaspi_external_ads).
            periods: список периодов отчёта в днях (например, [7, 30]).
                Каждой записи проставляется period_days = соответствующий
                период. Если None — берём дефолт из конфига.

        НЕ ждём таблицу: на детальной странице кампании может не быть таблицы
        товаров, но есть кнопка «Скачать отчёт» — её мы и пытаемся нажать.
        """
        report_periods = periods or list(Config.KASPI_MARKETING_REPORT_PERIODS)

        await page.goto(entry["url"], wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
        await self._random_delay()
        # Дождаться появления интерактивных элементов (кнопок), без требования таблицы
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        campaign_name = entry.get("name", "")
        result: list[AdCampaignData] = []

        for days in report_periods:
            payload = await self._download_marketing_report_by_click(page, days)
            if not payload:
                continue

            rows = await self._parse_marketing_report_payload(payload, page, depth=0)
            if not rows:
                continue

            for r in rows:
                r.source = source
                r.period_days = days
                r._campaign_name = campaign_name  # type: ignore[attr-defined]
            result.extend(rows)

        return result

    async def _scrape_entry_products_bonus(
        self,
        page: Page,
        entry: dict[str, str],
        periods: list[int] | None = None,
    ) -> list[BonusData]:
        """Зайти на страницу акции, скачать XLSX за каждый период, вернуть товары."""
        report_periods = periods or list(Config.KASPI_MARKETING_REPORT_PERIODS)

        await page.goto(entry["url"], wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
        await self._random_delay()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        result: list[BonusData] = []

        for days in report_periods:
            payload = await self._download_marketing_report_by_click(page, days)
            if not payload:
                continue

            # Сначала бонусный парсер (статус + %)
            rows = await self._parse_bonus_report_payload(payload, page, depth=0)
            if rows:
                for r in rows:
                    r.period_days = days
                result.extend(rows)
                continue

            # Иначе — формат маркетинга, конвертируем
            ad_rows = await self._parse_marketing_report_payload(payload, page, depth=0)
            if ad_rows:
                for r in ad_rows:
                    result.append(
                        BonusData(
                            product_sku=r.product_sku,
                            product_name=r.product_name,
                            bonus_active=True,
                            bonus_percent=0.0,
                            source="kaspi_bonus",
                            period_days=days,
                        )
                    )

        return result

    # -------------------------------------------------------------------------
    # Вспомогательные методы
    # -------------------------------------------------------------------------

    async def _collect_marketing_from_report(
        self, page: Page, days: int | None = None
    ) -> list[AdCampaignData]:
        """Скачать и распарсить маркетинговый xlsx-отчёт с текущей страницы."""
        report_url = await self._extract_marketing_report_url(page, days)
        if report_url:
            try:
                response = await self._context.request.get(report_url, timeout=_PAGE_LOAD_TIMEOUT)
                if response.ok:
                    payload = await response.body()
                    rows = await self._parse_marketing_report_payload(payload, page, depth=0)
                    if rows:
                        return rows
                    logger.warning("MarketingScraper: отчёт по URL загружен, но не распознан")
                else:
                    logger.warning(
                        "MarketingScraper: xlsx-отчёт вернул HTTP %s (%s)",
                        response.status,
                        report_url,
                    )
            except Exception as e:
                logger.warning("MarketingScraper: ошибка загрузки xlsx-отчёта по URL: %s", e)
        else:
            logger.info("MarketingScraper: ссылка на xlsx-отчёт не найдена на %s", page.url)

        payload = await self._download_marketing_report_by_click(page, days)
        if not payload:
            return []

        try:
            rows = await self._parse_marketing_report_payload(payload, page, depth=0)
            if not rows:
                # Иногда после клика URL отчёта появляется в HTML/скриптах страницы чуть позже.
                await page.wait_for_timeout(1_000)
                delayed_report_url = await self._extract_marketing_report_url(page, days)
                if delayed_report_url:
                    try:
                        delayed_response = await self._context.request.get(
                            delayed_report_url,
                            timeout=_PAGE_LOAD_TIMEOUT,
                        )
                        if delayed_response.ok:
                            delayed_payload = await delayed_response.body()
                            delayed_rows = await self._parse_marketing_report_payload(
                                delayed_payload,
                                page,
                                depth=0,
                            )
                            if delayed_rows:
                                logger.info(
                                    "MarketingScraper: отчёт успешно получен по отложенному URL после клика"
                                )
                                return delayed_rows
                    except Exception as delayed_error:
                        logger.debug(
                            "MarketingScraper: отложенный URL отчёта после клика не сработал: %s",
                            delayed_error,
                        )

                logger.warning("MarketingScraper: отчёт после клика пустой или не распознан")
            return rows
        except Exception as e:
            logger.warning("MarketingScraper: ошибка парсинга отчёта после клика: %s", e)
            return []

    async def _collect_bonuses_from_report(
        self, page: Page, days: int | None = None
    ) -> list[BonusData]:
        """Скачать и распарсить бонусный отчёт (если доступен) с текущей страницы."""
        report_url = await self._extract_any_report_url_from_html(page, days)
        if report_url:
            try:
                response = await self._context.request.get(report_url, timeout=_PAGE_LOAD_TIMEOUT)
                if response.ok:
                    payload = await response.body()
                    rows = await self._parse_bonus_report_payload(payload, page, depth=0)
                    if rows:
                        return rows
                else:
                    logger.warning(
                        "MarketingScraper: бонусный отчёт по URL вернул HTTP %s (%s)",
                        response.status,
                        report_url,
                    )
            except Exception as e:
                logger.debug("MarketingScraper: ошибка загрузки бонусного отчёта по URL: %s", e)

        payload = await self._download_marketing_report_by_click(page, days)
        if not payload:
            return []

        try:
            rows = await self._parse_bonus_report_payload(payload, page, depth=0)
            if not rows:
                logger.warning("MarketingScraper: бонусный отчёт после клика пустой или не распознан")
            return rows
        except Exception as e:
            logger.warning("MarketingScraper: ошибка парсинга бонусного отчёта после клика: %s", e)
            return []

    async def _parse_bonus_report_payload(
        self,
        payload: bytes,
        page: Page,
        depth: int,
    ) -> list[BonusData]:
        """Распарсить payload бонусного отчёта (xlsx/csv/text/url-wrapper)."""
        if not payload:
            return []

        if payload.startswith(b"PK\x03\x04"):
            return self._parse_bonus_xlsx(payload)

        text = self._decode_report_text(payload)
        if not text:
            return []

        if depth < 2:
            nested_url = self._extract_any_report_url_from_text(text)
            if nested_url:
                try:
                    nested_response = await self._context.request.get(
                        urljoin(page.url, nested_url),
                        timeout=_PAGE_LOAD_TIMEOUT,
                    )
                    if nested_response.ok:
                        nested_payload = await nested_response.body()
                        nested_rows = await self._parse_bonus_report_payload(
                            nested_payload,
                            page,
                            depth=depth + 1,
                        )
                        if nested_rows:
                            return nested_rows
                except Exception as e:
                    logger.debug("MarketingScraper: nested bonus report URL fetch failed: %s", e)

        return self._parse_bonus_csv(text)

    async def _extract_any_report_url_from_html(
        self, page: Page, days: int | None = None
    ) -> str | None:
        """Найти URL отчёта (xlsx/csv) в HTML страницы."""
        try:
            html_content = await page.content()
        except Exception as e:
            logger.debug("MarketingScraper: не удалось получить HTML страницы для report-url: %s", e)
            return None

        return self._extract_any_report_url_from_text(html_content, days)

    def _extract_any_report_url_from_text(
        self, text: str, days: int | None = None
    ) -> str | None:
        """Найти любой URL отчёта (xlsx/csv) в текстовом payload/HTML."""
        normalized = html.unescape(text).replace("\\/", "/")
        match = _GENERIC_REPORT_URL_PATTERN.search(normalized)
        if not match:
            return None
        url = urljoin(_CANONICAL_MARKETING_PRODUCTS_URL, match.group(0))
        return self._apply_report_period(url, days)

    def _parse_bonus_xlsx(self, payload: bytes) -> list[BonusData]:
        """Распарсить xlsx-отчёт бонусов."""
        rows = self._xlsx_rows(payload)
        if not rows:
            return []
        return self._parse_bonus_rows(rows)

    def _parse_bonus_csv(self, text: str) -> list[BonusData]:
        """Распарсить CSV/TSV-отчёт бонусов."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return []

        delimiter = ";"
        header_line = lines[0]
        if header_line.count("\t") > header_line.count(";"):
            delimiter = "\t"
        elif header_line.count(",") > header_line.count(";"):
            delimiter = ","

        rows = [line.split(delimiter) for line in lines]
        return self._parse_bonus_rows(rows)

    def _parse_bonus_rows(self, rows: list[list[str]]) -> list[BonusData]:
        """Распарсить строки бонусного отчёта в список BonusData."""
        header_idx = self._find_bonus_header_row_index(rows)
        data_rows = rows
        idx_name: int | None = None
        idx_status: int | None = None
        idx_percent: int | None = None

        if header_idx is not None:
            headers = [self._normalize_header(v) for v in rows[header_idx]]
            idx_name = self._find_product_name_column(headers)
            idx_sku = self._find_product_sku_column(headers)
            idx_status = self._find_col(headers, ["статус", "состоя", "active", "status", "state"])
            # Денежная колонка «Выплачено бонусов клиентам» не является
            # процентом бонуса и не должна попадать в bonus_percent.
            idx_percent = self._find_col(headers, ["процент", "%", "percent"])
            data_rows = rows[header_idx + 1 :]

        bonuses: list[BonusData] = []
        for raw_row in data_rows:
            values = [str(cell).strip() for cell in raw_row if str(cell).strip()]
            if not values:
                continue

            product_name = self._cell_value(raw_row, idx_name) if idx_name is not None else values[0]
            if not product_name or product_name.lower().startswith(("итого", "всего", "total")):
                continue

            row_text = " ".join(values)
            status_text = self._cell_value(raw_row, idx_status) if idx_status is not None else row_text
            percent_text = self._cell_value(raw_row, idx_percent) if idx_percent is not None else row_text
            bonus_percent = self._extract_percent_from_text(percent_text)
            # В некоторых выгрузках процент приходит числом без символа '%'.
            if bonus_percent <= 0 and idx_percent is not None:
                bonus_percent = _parse_number(percent_text)
            if bonus_percent <= 0:
                bonus_percent = self._extract_percent_from_text(row_text)

            bonus_active = self._is_bonus_active(status_text or row_text, bonus_percent)

            # Отсекаем строки, которые не похожи на данные товара.
            if bonus_percent <= 0 and not bonus_active and len(values) < 2:
                continue

            raw_sku = self._cell_value(raw_row, idx_sku)
            sku = raw_sku if self._is_real_product_sku(raw_sku) else self._extract_or_build_sku(product_name)
            bonuses.append(
                BonusData(
                    product_sku=sku,
                    product_name=product_name,
                    bonus_active=bonus_active,
                    bonus_percent=bonus_percent,
                    source="kaspi_bonus",
                )
            )

        return bonuses

    async def _parse_marketing_report_payload(
        self,
        payload: bytes,
        page: Page,
        depth: int,
    ) -> list[AdCampaignData]:
        """Распарсить payload отчёта (xlsx/csv/text/url-wrapper)."""
        if not payload:
            return []

        # OOXML / XLSX
        if payload.startswith(b"PK\x03\x04"):
            return self._parse_marketing_xlsx(payload)

        # Текстовые варианты: CSV, JSON с URL, plain URL.
        text = self._decode_report_text(payload)
        if not text:
            return []

        # Иногда сервер возвращает wrapper с реальным URL отчёта.
        if depth < 2:
            nested_url = self._extract_report_url_from_text(text)
            if nested_url:
                try:
                    nested_response = await self._context.request.get(
                        self._apply_report_period(urljoin(page.url, nested_url)),
                        timeout=_PAGE_LOAD_TIMEOUT,
                    )
                    if nested_response.ok:
                        nested_payload = await nested_response.body()
                        nested_rows = await self._parse_marketing_report_payload(
                            nested_payload,
                            page,
                            depth=depth + 1,
                        )
                        if nested_rows:
                            return nested_rows
                except Exception as e:
                    logger.debug("MarketingScraper: nested report URL fetch failed: %s", e)

        return self._parse_marketing_csv(text)

    @staticmethod
    def _decode_report_text(payload: bytes) -> str:
        """Декодировать текстовый payload с подбором кодировки."""
        for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            try:
                text = payload.decode(encoding)
                if text.strip():
                    return text
            except Exception:
                continue
        return ""

    def _extract_report_url_from_text(self, text: str) -> str | None:
        """Найти URL отчёта в текстовом payload."""
        normalized = html.unescape(text).replace("\\/", "/")
        match = _REPORT_URL_PATTERN.search(normalized)
        return match.group(0) if match else None

    def _parse_marketing_csv(self, text: str) -> list[AdCampaignData]:
        """Распарсить маркетинговый CSV/TSV отчёт."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return []

        delimiter = ";"
        header_line = lines[0]
        if header_line.count("\t") > header_line.count(";"):
            delimiter = "\t"
        elif header_line.count(",") > header_line.count(";"):
            delimiter = ","

        rows = [line.split(delimiter) for line in lines]
        header_idx = self._find_header_row_index(rows)
        if header_idx is None:
            fallback_rows = self._parse_marketing_rows_by_shape(rows)
            if fallback_rows:
                logger.info(
                    "MarketingScraper: CSV распознан через positional fallback (%d строк)",
                    len(fallback_rows),
                )
            return fallback_rows

        headers = [self._normalize_header(v) for v in rows[header_idx]]
        idx_name = self._find_product_name_column(headers)
        idx_sku_col = self._find_product_sku_column(headers)
        idx_clicks = self._find_col(headers, ["клик", "переход", "click"])
        idx_cpc = self._find_col(headers, ["ср. стоим", "средняя стоим", "cpc", "avg cost"])
        idx_spend = self._find_spend_column(headers)
        idx_impressions = self._find_col(headers, ["показ", "просмотр", "impression"])
        idx_ctr = self._find_col(headers, ["ctr", "кликабель", "click through"])

        if idx_name is None or idx_clicks is None or (idx_spend is None and idx_cpc is None):
            fallback_rows = self._parse_marketing_rows_by_shape(rows[header_idx + 1 :])
            if fallback_rows:
                logger.info(
                    "MarketingScraper: CSV с нестандартными колонками распознан через fallback (%d строк)",
                    len(fallback_rows),
                )
            return fallback_rows

        campaigns: list[AdCampaignData] = []
        for raw_row in rows[header_idx + 1 :]:
            product_name = self._cell_value(raw_row, idx_name)
            if not product_name or product_name.lower().startswith("итого"):
                continue

            clicks = self._cell_int(raw_row, idx_clicks)
            spend = self._cell_number(raw_row, idx_spend) if idx_spend is not None else 0.0
            impressions = self._cell_int(raw_row, idx_impressions) if idx_impressions is not None else 0

            if idx_ctr is not None:
                ctr = self._cell_number(raw_row, idx_ctr)
            else:
                ctr = (clicks / impressions * 100) if impressions > 0 else 0.0

            if idx_cpc is not None:
                cpc = self._cell_number(raw_row, idx_cpc)
                if idx_spend is None:
                    spend = cpc * clicks
            else:
                cpc = (spend / clicks) if clicks > 0 else 0.0

            impressions, clicks, ctr, spend, cpc = self._normalize_campaign_metrics(
                product_name=product_name,
                impressions=impressions,
                clicks=clicks,
                ctr=ctr,
                spend=spend,
                cpc=cpc,
            )

            # Если есть отдельная колонка с артикулом — использовать её
            if idx_sku_col is not None:
                raw_sku = self._cell_value(raw_row, idx_sku_col).strip()
                sku = raw_sku if re.match(r"^\d{5,12}$", raw_sku) else self._extract_or_build_sku(product_name)
            else:
                sku = self._extract_or_build_sku(product_name)

            campaigns.append(
                AdCampaignData(
                    product_sku=sku,
                    product_name=product_name,
                    impressions=impressions,
                    clicks=clicks,
                    ctr=ctr,
                    spend=spend,
                    cpc=cpc,
                    source="kaspi_marketing",
                )
            )

        return campaigns

    async def _extract_marketing_report_url(
        self, page: Page, days: int | None = None
    ) -> str | None:
        """Извлечь URL xlsx-отчёта из HTML страницы (без клика по кнопке)."""
        try:
            html_content = await page.content()
        except Exception as e:
            logger.debug("MarketingScraper: не удалось получить HTML страницы: %s", e)
            return None

        normalized_html = html.unescape(html_content).replace("\\/", "/")
        match = _REPORT_URL_PATTERN.search(normalized_html)
        if not match:
            return None

        raw_url = html.unescape(match.group(0))
        full_url = urljoin(page.url, raw_url)
        return self._apply_report_period(full_url, days)

    async def _download_report_via_link_href(
        self, page: Page, days: int | None = None
    ) -> bytes | None:
        """Скачать отчёт по прямой ссылке `<a href>` с URL XLSX/CSV.

        Покрывает кейс, когда на странице есть `<a class="m-share-button" href="...report/xlsx">`
        с иконкой загрузки (без текста). На страницах бонусов за отзывы у Kaspi
        именно такая структура.

        Args:
            page: текущая страница Playwright.
            days: длина периода отчёта (используется для подмены startDate/endDate
                в href). None = брать значение из KASPI_MARKETING_REPORT_DAYS.
        """
        try:
            href_list: list[str] = await page.evaluate(
                """() => {
                    const out = [];
                    const anchors = document.querySelectorAll('a[href]');
                    anchors.forEach(a => {
                        const href = a.href || '';
                        if (!href || !href.startsWith('http')) return;
                        const lower = href.toLowerCase();
                        // Прямые ссылки на скачивание отчётов
                        const looksLikeReport = (
                            (lower.includes('/report/') || lower.includes('/reports/') ||
                             lower.includes('/export') || lower.includes('/download')) &&
                            (lower.includes('xlsx') || lower.includes('csv') || lower.includes('format='))
                        );
                        // Иконочные кнопки share/download (Kaspi UI: m-share-button)
                        const cls = (a.className || '').toString().toLowerCase();
                        const isShareBtn = cls.includes('share-button') || cls.includes('download');
                        if (looksLikeReport || (isShareBtn && href.length > 0)) {
                            out.push(href);
                        }
                    });
                    return out;
                }"""
            ) or []
        except Exception as exc:
            logger.debug("MarketingScraper: ошибка поиска report-href: %s", exc)
            return None

        # Дедуплицируем сохраняя порядок
        seen: set[str] = set()
        unique_hrefs = [h for h in href_list if not (h in seen or seen.add(h))]
        if not unique_hrefs:
            return None

        for raw_href in unique_hrefs:
            url = self._apply_report_period(raw_href, days)
            try:
                response = await self._context.request.get(url, timeout=_PAGE_LOAD_TIMEOUT)
                if not response.ok:
                    logger.debug(
                        "MarketingScraper: report-href HTTP %s для %s",
                        response.status, url,
                    )
                    continue
                body = await response.body()
                if body and len(body) > 100:
                    logger.info(
                        "MarketingScraper: отчёт получен по прямой ссылке (%s, %d байт)",
                        url, len(body),
                    )
                    return body
            except Exception as exc:
                logger.debug("MarketingScraper: ошибка загрузки %s: %s", url, exc)
        return None

    async def _download_marketing_report_by_click(
        self, page: Page, days: int | None = None
    ) -> bytes | None:
        """Скачать отчёт: сначала по прямой ссылке `<a href>`, затем кликом по кнопке.

        Если `days` указан — отчёт скачивается за `days` дней через подмену
        startDate/endDate в href-ссылке (доступно для всех страниц Kaspi
        Marketing, где есть `<a class="m-share-button">`). Fallback по клику
        кнопки скачает отчёт за период, выбранный в UI (по умолчанию 7 дней).
        """
        # 0) Дождаться появления кнопки скачивания (Kaspi рендерит её асинхронно)
        try:
            await page.wait_for_selector(
                "a[class*='share-button'], a[href*='/reports/'], "
                "button:has-text('Скачать'), [aria-label*='Скачать' i]",
                state="attached",
                timeout=8_000,
            )
        except Exception:
            pass  # не критично — продолжаем

        # 1) Прямая ссылка (например, иконочная кнопка m-share-button с href на XLSX)
        body = await self._download_report_via_link_href(page, days)
        if body:
            return body

        # Fallback на клик кнопки даём только для дефолтного 7-дневного периода:
        # клик не управляет периодом отчёта (он берётся из UI, по умолчанию 7д).
        # Для 30-дневной выгрузки лучше пропустить отчёт, чем подменить его 7д-данными.
        if days is not None and int(days) != int(Config.KASPI_MARKETING_REPORT_DAYS):
            logger.info(
                "MarketingScraper: прямая ссылка для %sд не найдена; "
                "клик-fallback пропускаем (период не подменяется через UI)",
                days,
            )
            return None

        # 2) Кликабельные кнопки с текстом
        selectors = [
            "button:has-text('Скачать отчет')",
            "button:has-text('Скачать отчёт')",
            "a:has-text('Скачать отчет')",
            "a:has-text('Скачать отчёт')",
            "button:has-text('Скачать')",
            "button:has-text('Выгрузить')",
            "button:has-text('Экспорт')",
            "button:has-text('Download report')",
            "button:has-text('Export')",
            "a:has-text('Скачать')",
            "a:has-text('Выгрузить')",
            "a:has-text('Экспорт')",
            "[class*='download-report']",
            "a.m-share-button",
            "a[class*='share-button']",
            "[aria-label*='Скачать' i]",
            "[aria-label*='download' i]",
            "[title*='Скачать' i]",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                # Видимость проверяем без длинного ожидания: если кнопка уже не видна — скип
                if not await locator.is_visible():
                    continue
                logger.info("MarketingScraper: пробуем скачать отчёт кликом (%s)", selector)

                async with page.expect_download(timeout=20_000) as download_info:
                    await locator.click()

                download = await download_info.value
                failure = await download.failure()
                if failure:
                    logger.warning("MarketingScraper: download завершился ошибкой: %s", failure)
                    continue

                tmp_path = await download.path()
                if tmp_path:
                    with open(tmp_path, "rb") as file:
                        return file.read()

                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temp_file:
                    temp_path = temp_file.name

                try:
                    await download.save_as(temp_path)
                    with open(temp_path, "rb") as file:
                        return file.read()
                finally:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
            except PlaywrightTimeoutError:
                # Иногда выгрузка уходит через XHR/response, а не через browser download.
                try:
                    async with page.expect_response(
                        lambda response: (
                            any(token in response.url.lower() for token in ("report", "reports", "export", "download", "xlsx", "csv"))
                            and response.status < 400
                        ),
                        timeout=12_000,
                    ) as response_info:
                        await locator.click()

                    response = await response_info.value
                    if response.ok:
                        body = await response.body()
                        if body:
                            logger.info(
                                "MarketingScraper: отчёт получен через network response (%s)",
                                response.url,
                            )
                            return body
                except Exception:
                    logger.debug("MarketingScraper: таймаут/ошибка network-response для %s", selector)
            except Exception as e:
                logger.debug("MarketingScraper: ошибка click-download (%s): %s", selector, e)

        logger.warning("MarketingScraper: скачать отчёт через кнопку не удалось")
        return None

    def _apply_report_period(self, report_url: str, days: int | None = None) -> str:
        """Подменить период отчёта в URL.

        Args:
            report_url: исходный URL XLSX-отчёта.
            days: длина периода в днях. Если None — берём KASPI_MARKETING_REPORT_DAYS
                (для совместимости со старым кодом, который не передаёт days).
        """
        effective_days = max(1, int(days if days is not None else Config.KASPI_MARKETING_REPORT_DAYS))

        try:
            parts = urlsplit(report_url)
            query = parse_qs(parts.query, keep_blank_values=True)

            end_date = now_kz().date()
            start_date = end_date - timedelta(days=effective_days - 1)

            query["startDate"] = [start_date.isoformat()]
            query["endDate"] = [end_date.isoformat()]

            new_query = urlencode(query, doseq=True)
            updated = urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
            logger.info(
                "MarketingScraper: период отчёта применён: %s дней (%s..%s)",
                effective_days,
                start_date,
                end_date,
            )
            return updated
        except Exception as e:
            logger.warning("MarketingScraper: не удалось применить период отчёта: %s", e)
            return report_url

    def _parse_marketing_xlsx(self, payload: bytes) -> list[AdCampaignData]:
        """Распарсить xlsx-отчёт маркетинга в список AdCampaignData."""
        rows = self._xlsx_rows(payload)
        if not rows:
            return []

        header_idx = self._find_header_row_index(rows)
        if header_idx is None:
            fallback_rows = self._parse_marketing_rows_by_shape(rows)
            if fallback_rows:
                logger.info(
                    "MarketingScraper: XLSX распознан через positional fallback (%d строк)",
                    len(fallback_rows),
                )
            return fallback_rows

        headers = [self._normalize_header(v) for v in rows[header_idx]]
        idx_name = self._find_product_name_column(headers)
        idx_sku_col = self._find_product_sku_column(headers)
        idx_clicks = self._find_col(headers, ["клик", "переход", "click"])
        idx_cpc = self._find_col(headers, ["ср. стоим", "средняя стоим", "cpc", "avg cost"])
        idx_spend = self._find_spend_column(headers)
        idx_impressions = self._find_col(headers, ["показ", "просмотр", "impression"])
        idx_ctr = self._find_col(headers, ["ctr", "кликабель", "click through"])

        if idx_name is None or idx_clicks is None or (idx_spend is None and idx_cpc is None):
            logger.warning("MarketingScraper: не найдены ключевые колонки в xlsx-отчёте")
            fallback_rows = self._parse_marketing_rows_by_shape(rows[header_idx + 1 :])
            if fallback_rows:
                logger.info(
                    "MarketingScraper: XLSX с нестандартными колонками распознан через fallback (%d строк)",
                    len(fallback_rows),
                )
            return fallback_rows

        campaigns: list[AdCampaignData] = []

        for raw_row in rows[header_idx + 1 :]:
            if idx_name >= len(raw_row):
                continue

            product_name = str(raw_row[idx_name]).strip()
            if not product_name:
                continue
            if product_name.lower().startswith("итого"):
                continue

            clicks = self._cell_int(raw_row, idx_clicks)
            spend = self._cell_number(raw_row, idx_spend) if idx_spend is not None else 0.0
            impressions = self._cell_int(raw_row, idx_impressions) if idx_impressions is not None else 0

            if idx_ctr is not None:
                ctr = self._cell_number(raw_row, idx_ctr)
            else:
                ctr = (clicks / impressions * 100) if impressions > 0 else 0.0

            if idx_cpc is not None:
                cpc = self._cell_number(raw_row, idx_cpc)
                if idx_spend is None:
                    spend = cpc * clicks
            else:
                cpc = (spend / clicks) if clicks > 0 else 0.0

            impressions, clicks, ctr, spend, cpc = self._normalize_campaign_metrics(
                product_name=product_name,
                impressions=impressions,
                clicks=clicks,
                ctr=ctr,
                spend=spend,
                cpc=cpc,
            )

            if idx_sku_col is not None:
                raw_sku = str(raw_row[idx_sku_col]).strip() if idx_sku_col < len(raw_row) else ""
                sku = raw_sku if re.match(r"^\d{5,12}$", raw_sku) else self._extract_or_build_sku(product_name)
            else:
                sku = self._extract_or_build_sku(product_name)

            campaigns.append(
                AdCampaignData(
                    product_sku=sku,
                    product_name=product_name,
                    impressions=impressions,
                    clicks=clicks,
                    ctr=ctr,
                    spend=spend,
                    cpc=cpc,
                    source="kaspi_marketing",
                )
            )

        return campaigns

    def _parse_marketing_rows_by_shape(self, rows: list[list[str]]) -> list[AdCampaignData]:
        """Fallback-парсинг строк отчёта без надёжных заголовков колонок."""
        campaigns: list[AdCampaignData] = []

        for raw_row in rows:
            values = [str(cell).strip() for cell in raw_row if str(cell).strip()]
            if len(values) < 3:
                continue

            # Найти колонку, которая больше всего похожа на название товара
            # (содержит буквы, не только цифры, не "итого"/"всего"/"total"/"№")
            name_candidates = [
                v for v in values
                if len(v.strip()) > 3
                and re.search(r"[a-zA-Zа-яА-ЯёЁ]", v)
                and not re.match(r"^\d+$", v.strip())
                and not v.lower().startswith(("итого", "всего", "total", "№", "#", "id"))
            ]
            if name_candidates:
                product_name = max(name_candidates, key=len)
            else:
                product_name = values[0]

            if not product_name or product_name.lower().startswith(("итого", "всего", "total")):
                continue

            numeric_cells = [value for value in values if re.search(r"\d", value) and value != product_name]
            if len(numeric_cells) < 2:
                continue

            parsed_numbers = [_parse_number(value) for value in numeric_cells]
            impressions = int(parsed_numbers[0]) if len(parsed_numbers) > 0 else 0
            clicks = int(parsed_numbers[1]) if len(parsed_numbers) > 1 else 0

            ctr = 0.0
            percent_candidates = [
                _parse_number(cell)
                for cell in numeric_cells[2:]
                if "%" in cell and _parse_number(cell) > 0
            ]
            if percent_candidates:
                ctr = percent_candidates[0]
            elif impressions > 0:
                ctr = clicks / impressions * 100

            tail_numbers = [number for number in parsed_numbers[2:] if number > 0]
            if tail_numbers:
                spend = max(tail_numbers)
                cpc_candidates = [number for number in tail_numbers if number <= spend]
                if clicks > 0 and cpc_candidates:
                    cpc = min(cpc_candidates, key=lambda value: abs(value * clicks - spend))
                else:
                    cpc = 0.0
            else:
                spend = 0.0
                cpc = 0.0

            impressions, clicks, ctr, spend, cpc = self._normalize_campaign_metrics(
                product_name=product_name,
                impressions=impressions,
                clicks=clicks,
                ctr=ctr,
                spend=spend,
                cpc=cpc,
            )

            if clicks == 0 and impressions == 0 and spend == 0.0:
                continue

            # Попробуем найти числовой ID (5-12 цифр) в любой колонке для SKU
            sku_candidates = [v for v in values if re.match(r"^\d{5,12}$", v.strip())]
            if sku_candidates:
                sku = sku_candidates[0]
            else:
                sku = self._extract_or_build_sku(product_name)
            campaigns.append(
                AdCampaignData(
                    product_sku=sku,
                    product_name=product_name,
                    impressions=impressions,
                    clicks=clicks,
                    ctr=ctr,
                    spend=spend,
                    cpc=cpc,
                    source="kaspi_marketing",
                )
            )

        return campaigns

    def _xlsx_rows(self, payload: bytes) -> list[list[str]]:
        """Преобразовать xlsx (bytes) в список строк значений первой вкладки."""
        try:
            with zipfile.ZipFile(BytesIO(payload)) as zf:
                shared_strings = self._read_shared_strings(zf)
                sheet_path = self._first_sheet_path(zf)
                if not sheet_path:
                    return []

                sheet_xml = ElementTree.fromstring(zf.read(sheet_path))
                rows: list[list[str]] = []

                for row in sheet_xml.findall(".//main:row", _XLSX_NS):
                    values_by_col: dict[int, str] = {}
                    max_col = 0
                    for cell in row.findall("main:c", _XLSX_NS):
                        col_idx = self._cell_col_index(cell.get("r", "A1"))
                        value = self._xlsx_cell_value(cell, shared_strings)

                        values_by_col[col_idx] = value
                        if col_idx > max_col:
                            max_col = col_idx

                    if not values_by_col:
                        continue

                    row_values = [values_by_col.get(i, "") for i in range(max_col + 1)]
                    rows.append(row_values)

                return rows
        except Exception as e:
            logger.warning("MarketingScraper: ошибка парсинга xlsx: %s", e)
            return []

    @staticmethod
    def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
        """Извлечь значение ячейки с поддержкой shared strings и inline strings."""
        cell_type = cell.get("t")

        if cell_type == "inlineStr":
            inline_parts = [t.text or "" for t in cell.findall(".//main:is//main:t", _XLSX_NS)]
            return "".join(inline_parts).strip()

        raw_value = ""
        v = cell.find("main:v", _XLSX_NS)
        if v is not None and v.text:
            raw_value = v.text.strip()

        if cell_type == "s" and raw_value:
            try:
                return (shared_strings[int(raw_value)] or "").strip()
            except Exception:
                return raw_value

        if raw_value:
            return raw_value

        # fallback: некоторые генераторы кладут текст в <is><t> без t=inlineStr
        inline_parts = [t.text or "" for t in cell.findall(".//main:t", _XLSX_NS)]
        return "".join(inline_parts).strip()

    @staticmethod
    def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
        """Считать shared strings из xlsx-архива."""
        shared_path = "xl/sharedStrings.xml"
        if shared_path not in zf.namelist():
            return []

        root = ElementTree.fromstring(zf.read(shared_path))
        strings: list[str] = []
        for si in root.findall(".//main:si", _XLSX_NS):
            parts = [t.text or "" for t in si.findall(".//main:t", _XLSX_NS)]
            strings.append("".join(parts))
        return strings

    @staticmethod
    def _first_sheet_path(zf: zipfile.ZipFile) -> str | None:
        """Получить путь до первой worksheet в xlsx."""
        candidates = [
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet.xml",
        ]
        for path in candidates:
            if path in zf.namelist():
                return path

        for name in zf.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                return name
        return None

    @staticmethod
    def _cell_col_index(cell_ref: str) -> int:
        """Преобразовать ссылку ячейки (например B12) в индекс колонки (0-based)."""
        letters = ""
        for ch in cell_ref:
            if ch.isalpha():
                letters += ch
            else:
                break

        idx = 0
        for ch in letters.upper():
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return max(0, idx - 1)

    @staticmethod
    def _normalize_header(value: str) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _find_col(headers: list[str], needles: list[str]) -> int | None:
        for idx, header in enumerate(headers):
            if any(needle in header for needle in needles):
                return idx
        return None

    @classmethod
    def _find_product_name_column(cls, headers: list[str]) -> int | None:
        """Найти именно название товара, не соседнюю SKU-колонку."""
        for idx, header in enumerate(headers):
            if "рекламируем" in header or header in {"sku", "артикул"}:
                continue
            if any(needle in header for needle in ("наимен", "назван", "товар", "product", "name", "title", "item", "описание", "description")):
                return idx
        return None

    @classmethod
    def _find_product_sku_column(cls, headers: list[str]) -> int | None:
        return cls._find_col(
            headers,
            [
                "рекламируемый товар", "артикул", "код товара", "id товара",
                "product id", "product_id", "sku", "articul",
            ],
        )

    @staticmethod
    def _find_spend_column(headers: list[str]) -> int | None:
        for idx, header in enumerate(headers):
            if any(token in header for token in ("расход", "затрат", "spend", "expense")):
                return idx
        # Legacy fallback: «стоимость» без упоминания клика/CPC.
        for idx, header in enumerate(headers):
            if (
                ("стоим" in header or "cost" in header)
                and "клик" not in header
                and "cpc" not in header
                and "ср." not in header
                and "средн" not in header
                and "заказ" not in header
                and "order" not in header
            ):
                return idx
        return None

    @staticmethod
    def _find_header_row_index(rows: list[list[str]]) -> int | None:
        for idx, row in enumerate(rows[:20]):
            normalized = [str(v).strip().lower() for v in row if str(v).strip()]
            if not normalized:
                continue
            has_name = any(
                "наимен" in v or "назван" in v or "товар" in v or "product" in v
                for v in normalized
            )
            has_clicks = any("клик" in v or "переход" in v or "click" in v for v in normalized)
            has_cost = any(
                "стоим" in v or "расход" in v or "затрат" in v or "cost" in v or "spend" in v
                for v in normalized
            )
            if has_name and has_clicks and has_cost:
                return idx
        return None

    @staticmethod
    def _find_bonus_header_row_index(rows: list[list[str]]) -> int | None:
        """Найти индекс строки заголовка бонусного отчёта."""
        for idx, row in enumerate(rows[:20]):
            normalized = [str(v).strip().lower() for v in row if str(v).strip()]
            if not normalized:
                continue

            has_name = any(
                "наимен" in v or "назван" in v or "товар" in v or "product" in v
                for v in normalized
            )
            has_bonus = any("бонус" in v or "процент" in v or "bonus" in v or "%" in v for v in normalized)
            has_status = any("статус" in v or "состоя" in v or "status" in v or "state" in v for v in normalized)

            if has_name and (has_bonus or has_status):
                return idx
        return None

    @staticmethod
    def _extract_percent_from_text(text: str) -> float:
        """Извлечь процент из текста (например 12.5% или 12,5 %)."""
        if not text:
            return 0.0
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
        if not match:
            return 0.0
        return _parse_number(match.group(1))

    @staticmethod
    def _is_bonus_active(status_text: str, bonus_percent: float) -> bool:
        """Определить активность бонуса по тексту статуса/проценту."""
        lower = (status_text or "").lower()
        if any(keyword in lower for keyword in _BONUS_STATUS_INACTIVE_KEYWORDS):
            return False
        if any(keyword in lower for keyword in _BONUS_STATUS_ACTIVE_KEYWORDS):
            return True
        return bonus_percent > 0

    @staticmethod
    def _cell_value(row: list[str], idx: int | None) -> str:
        if idx is None or idx < 0 or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    def _cell_number(self, row: list[str], idx: int | None) -> float:
        return _parse_number(self._cell_value(row, idx))

    def _cell_int(self, row: list[str], idx: int | None) -> int:
        return _parse_int(self._cell_value(row, idx))

    def _normalize_campaign_metrics(
        self,
        product_name: str,
        impressions: int,
        clicks: int,
        ctr: float,
        spend: float,
        cpc: float,
    ) -> tuple[int, int, float, float, float]:
        """Нормализовать метрики кампании при нестандартном маппинге колонок отчёта."""
        impressions = max(0, int(impressions))
        clicks = max(0, int(clicks))
        ctr = max(0.0, float(ctr))
        spend = max(0.0, float(spend))
        cpc = max(0.0, float(cpc))

        computed_ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0
        computed_cpc = (spend / clicks) if clicks > 0 else 0.0

        # В некоторых выгрузках CTR и CPC меняются местами.
        swapped = False
        if ctr > 100.0 and 0.0 < cpc <= 100.0 and computed_cpc > 100.0:
            ctr, cpc = cpc, computed_cpc
            swapped = True

        if ctr > 100.0 and computed_ctr > 0.0:
            ctr = computed_ctr

        if cpc <= 0.0 and computed_cpc > 0.0:
            cpc = computed_cpc

        if ctr > 100.0:
            ctr = 100.0

        if swapped:
            logger.info(
                "MarketingScraper: скорректирован swap CTR/CPC для '%s' (ctr=%.2f, cpc=%.2f)",
                product_name[:80],
                ctr,
                cpc,
            )

        return impressions, clicks, ctr, spend, cpc

    def _extract_or_build_sku(self, product_name: str) -> str:
        """Извлечь SKU из названия или построить стабильный surrogate SKU."""
        extracted = re.search(r"(?:sku|арт\.?|артикул)[:\s]+([a-z0-9\-]{5,20})", product_name, re.IGNORECASE)
        if extracted:
            return extracted.group(1).upper()

        numeric = re.search(r"\b\d{5,12}\b", product_name)
        if numeric:
            return numeric.group(0)

        stable_hash = zlib.adler32(product_name.lower().encode("utf-8"))
        return f"RPT-{stable_hash:08X}"

    async def _wait_for_content(self, page: Page) -> bool:
        """Дождаться загрузки таблицы или сообщения об отсутствии данных."""
        selector = (
            "table tbody tr, [data-testid='empty-state'], .empty-state, "
            "[class*='table'] [class*='row'], [class*='Table'] tbody tr"
        )
        try:
            await page.wait_for_selector(selector, timeout=_CONTENT_WAIT_TIMEOUT)
            return True
        except PlaywrightTimeoutError:
            # Страница может быть «пустой», но валидной
            body_text = ""
            try:
                body_text = (await page.inner_text("body")).lower()
            except Exception:
                body_text = ""

            empty_tokens = (
                "нет данных",
                "ничего не найдено",
                "данные отсутствуют",
                "no data",
                "not found",
            )
            if any(token in body_text for token in empty_tokens):
                logger.info("MarketingScraper: валидная пустая страница %s", page.url)
                return True

            logger.warning(
                "MarketingScraper: таблица не обнаружена на %s (страница может быть пустой)",
                page.url,
            )
            return False

    async def _navigate_to_first_available_content(
        self,
        page: Page,
        urls: list[str],
        section_name: str,
    ) -> str | None:
        """Открыть первый URL раздела, где удалось обнаружить контент."""
        for url in urls:
            try:
                logger.info("MarketingScraper: переход на страницу %s %s", section_name, url)
                await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                await self._random_delay()
                if await self._wait_for_content(page):
                    return url
            except PlaywrightTimeoutError:
                logger.warning(
                    "MarketingScraper: таймаут открытия %s URL %s",
                    section_name,
                    url,
                )
            except Exception as e:
                logger.warning(
                    "MarketingScraper: ошибка открытия %s URL %s: %s",
                    section_name,
                    url,
                    e,
                )

        logger.error("MarketingScraper: ни один URL %s не открылся с контентом", section_name)
        return None

    @staticmethod
    def _is_real_product_sku(value: str | None) -> bool:
        """Похоже ли значение на реальный Kaspi product SKU."""
        return bool(value and re.match(r"^\d{5,12}$", value.strip()))

    @staticmethod
    def _identity_key(value: str | None) -> str:
        """Нормализованное значение для точного сопоставления строк отчёта."""
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    async def _product_identity_lookup(self) -> tuple[set[str], dict[str, str]]:
        """Построить lookup реальных SKU из products.

        Возвращает:
            product_skus: множество master_sku.
            aliases: точные алиасы -> master_sku. Алиасы намеренно строгие:
                master_sku и нормализованный title. Нечёткий поиск здесь опасен,
                потому что может связать бонус с неправильным товаром.
        """
        product_skus: set[str] = set()
        aliases: dict[str, str] = {}
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT master_sku, title FROM products") as cursor:
                    async for row in cursor:
                        sku = str(row["master_sku"] or "").strip()
                        if not sku:
                            continue
                        product_skus.add(sku)
                        aliases[self._identity_key(sku)] = sku
                        title_key = self._identity_key(row["title"])
                        if title_key:
                            aliases.setdefault(title_key, sku)
        except Exception as exc:
            logger.debug("MarketingScraper: product identity lookup недоступен: %s", exc)
        return product_skus, aliases

    def _resolve_row_product_sku(
        self,
        row: AdCampaignData | BonusData,
        product_skus: set[str],
        aliases: dict[str, str],
    ) -> str | None:
        """Найти реальный SKU для строки отчёта без fuzzy-сопоставления."""
        raw_sku = (row.product_sku or "").strip()
        if raw_sku in product_skus:
            return raw_sku

        product_name = (row.product_name or "").strip()
        direct_alias = aliases.get(self._identity_key(product_name))
        if direct_alias:
            return direct_alias

        for candidate in re.findall(r"\b\d{5,12}\b", product_name):
            if candidate in product_skus:
                return candidate

        if self._is_real_product_sku(raw_sku) and raw_sku in product_skus:
            return raw_sku

        return None

    async def _normalize_report_product_skus(
        self,
        rows: list[AdCampaignData | BonusData],
    ) -> None:
        """Заменить surrogate/короткие ключи отчёта на products.master_sku, если возможно."""
        if not rows:
            return

        product_skus, aliases = await self._product_identity_lookup()
        if not product_skus:
            return

        changed = 0
        for row in rows:
            resolved = self._resolve_row_product_sku(row, product_skus, aliases)
            if resolved and resolved != row.product_sku:
                row.product_sku = resolved
                changed += 1

        if changed:
            logger.info("MarketingScraper: нормализовано SKU строк отчёта: %d", changed)

    async def get_report_identity_diagnostics(
        self,
        rows: list[AdCampaignData | BonusData],
    ) -> dict[str, int]:
        """Диагностика качества идентификаторов в распарсенных строках отчёта."""
        product_skus, _aliases = await self._product_identity_lookup()
        total = len(rows)
        real_sku_count = sum(1 for row in rows if self._is_real_product_sku(row.product_sku))
        surrogate_sku_count = sum(
            1 for row in rows
            if str(row.product_sku or "").startswith("RPT-")
            or not self._is_real_product_sku(row.product_sku)
        )
        product_match_count = sum(1 for row in rows if row.product_sku in product_skus)
        return {
            "total": total,
            "real_sku_count": real_sku_count,
            "surrogate_sku_count": surrogate_sku_count,
            "product_match_count": product_match_count,
        }

    @staticmethod
    def _deduplicate_bonuses(items: list[BonusData]) -> list[BonusData]:
        """Объединить дубли бонусов по SKU (берём максимально информативную запись)."""
        merged: dict[str, BonusData] = {}

        for item in items:
            existing = merged.get(item.product_sku)
            if not existing:
                merged[item.product_sku] = item
                continue

            # Предпочитаем запись с активным бонусом; если обе равны — с большим процентом.
            if item.bonus_active and not existing.bonus_active:
                merged[item.product_sku] = item
                continue
            if item.bonus_active == existing.bonus_active and item.bonus_percent > existing.bonus_percent:
                merged[item.product_sku] = item

        return list(merged.values())

    async def _scroll_and_collect_marketing_rows(self, page: Page) -> list[AdCampaignData]:
        """Скролл страницы маркетинга и сбор всех строк.

        Цикл: скролл вниз → пауза → проверка новых строк.
        Завершается, когда количество строк перестаёт расти.
        """
        results: list[AdCampaignData] = []
        prev_count = 0

        for iteration in range(_MAX_SCROLL_ITERATIONS):
            # Прокрутка вниз
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(_SCROLL_PAUSE_MS)

            # Сбор строк таблицы
            rows = await self._extract_marketing_rows(page)
            current_count = len(rows)

            if current_count == prev_count and iteration > 0:
                # Новых строк не появилось — достигнут конец
                logger.debug(
                    "MarketingScraper: скролл завершён на итерации %d (%d строк)",
                    iteration,
                    current_count,
                )
                break

            prev_count = current_count

            if iteration % 10 == 0 and iteration > 0:
                logger.info(
                    "MarketingScraper: скролл итерация %d, строк загружено: %d",
                    iteration,
                    current_count,
                )

        # Финальный сбор после завершения скролла
        results = await self._extract_marketing_rows(page)
        return results

    async def _extract_marketing_rows(self, page: Page) -> list[AdCampaignData]:
        """Извлечь данные из всех видимых строк таблицы маркетинга."""
        try:
            # Пробуем несколько возможных селекторов таблицы Kaspi Pay
            row_selector = (
                "table tbody tr, "
                "[class*='Table'] tbody tr, "
                "[class*='table-row']:not([class*='header']), "
                "[role='row']:not([role='columnheader'])"
            )
            rows = await page.query_selector_all(row_selector)
            campaigns: list[AdCampaignData] = []

            for row in rows:
                campaign = await self._parse_marketing_row(row)
                if campaign:
                    campaigns.append(campaign)

            return campaigns
        except Exception as e:
            logger.warning("MarketingScraper: ошибка извлечения строк: %s", e)
            return []

    async def _parse_marketing_row(self, row) -> AdCampaignData | None:
        """Разобрать одну строку таблицы маркетинга."""
        try:
            # Получаем все ячейки строки
            cells = await row.query_selector_all("td, [role='cell']")
            if len(cells) < 4:
                return None

            # Получаем текст всех ячеек
            cell_texts = []
            for cell in cells:
                text = await cell.inner_text()
                cell_texts.append(text.strip())

            # Структура таблицы Kaspi Marketing (типичная):
            # 0: Название/SKU товара
            # 1: Охваты (Impressions)
            # 2: Клики (Clicks)
            # 3: CTR (%)
            # 4: Затраты (₸)
            # 5: CPC (₸)
            if len(cell_texts) < 4:
                return None

            # Извлечение SKU из первой ячейки (включая href ссылок)
            sku, name = await self._extract_sku_and_name(cell_texts[0], cells[0])
            if not sku:
                return None

            # Маппинг полей в зависимости от количества колонок
            if len(cell_texts) >= 6:
                impressions = _parse_int(cell_texts[1])
                clicks = _parse_int(cell_texts[2])
                ctr = _parse_number(cell_texts[3])
                spend = _parse_number(cell_texts[4])
                cpc = _parse_number(cell_texts[5])
            elif len(cell_texts) >= 5:
                impressions = _parse_int(cell_texts[1])
                clicks = _parse_int(cell_texts[2])
                ctr = _parse_number(cell_texts[3])
                spend = _parse_number(cell_texts[4])
                cpc = spend / clicks if clicks > 0 else 0.0
            else:
                # Минимальная структура — продолжаем с нулевыми значениями
                impressions = _parse_int(cell_texts[1]) if len(cell_texts) > 1 else 0
                clicks = _parse_int(cell_texts[2]) if len(cell_texts) > 2 else 0
                ctr = 0.0
                spend = 0.0
                cpc = 0.0

            impressions, clicks, ctr, spend, cpc = self._normalize_campaign_metrics(
                product_name=name,
                impressions=impressions,
                clicks=clicks,
                ctr=ctr,
                spend=spend,
                cpc=cpc,
            )

            return AdCampaignData(
                product_sku=sku,
                product_name=name,
                impressions=impressions,
                clicks=clicks,
                ctr=ctr,
                spend=spend,
                cpc=cpc,
                source="kaspi_marketing",
            )

        except Exception as e:
            logger.debug("MarketingScraper: ошибка парсинга строки: %s", e)
            return None

    async def _extract_sku_and_name(self, cell_text: str, cell_element) -> tuple[str, str]:
        """Извлечение SKU и названия из ячейки товара.

        Kaspi Pay обычно показывает: «Название товара\nSKU: 123456»,
        или SKU в href ссылки вида /shop/p/slug-123456789.
        """
        if not cell_text:
            return ("", "")

        lines = [ln.strip() for ln in cell_text.split("\n") if ln.strip()]

        # Паттерн: реальный Kaspi-артикул (9–12 цифр) или общий код (5–20 alphanum)
        kaspi_sku_pattern = re.compile(r"^\d{9,12}$")
        sku_pattern = re.compile(r"^\d{5,12}$|^[A-Z0-9\-]{5,20}$")

        sku = ""
        name = ""

        if len(lines) >= 2:
            for line in lines:
                if sku_pattern.match(line):
                    sku = sku or line
                else:
                    name = name or line
        elif len(lines) == 1:
            if sku_pattern.match(lines[0]):
                sku = lines[0]
            else:
                name = lines[0]

        # Паттерн в тексте: «SKU: 123456» или «Арт. 123456»
        if not sku and name:
            m = re.search(r"(?:SKU|Арт\.?|sku)[:\s]+(\d{5,12})", name, re.IGNORECASE)
            if m:
                sku = m.group(1)
                name = name.replace(m.group(0), "").strip()

        # Если реальный Kaspi SKU (9–12 цифр) не найден в тексте —
        # пробуем извлечь из href ссылки в ячейке (приоритет над коротким ID)
        if not kaspi_sku_pattern.match(sku):
            try:
                link = await cell_element.query_selector("a[href]")
                if link:
                    href = await link.get_attribute("href") or ""
                    # /shop/p/plastkrep-slug-123576929 или /mc/goods/123576929
                    m = re.search(r"[-/](\d{9,12})(?:[/?#]|$)", href)
                    if m:
                        sku = m.group(1)
            except Exception:
                pass

        # Если SKU так и не найден — стабильный surrogate
        if not sku:
            sku = self._extract_or_build_sku(name or cell_text[:80])

        return (sku, name or cell_text[:80])

    async def _scroll_and_collect_bonus_rows(self, page: Page) -> list[BonusData]:
        """Скролл страницы бонусов и сбор всех строк."""
        prev_count = 0

        for iteration in range(_MAX_SCROLL_ITERATIONS):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(_SCROLL_PAUSE_MS)

            rows = await page.query_selector_all(
                "table tbody tr, [class*='Table'] tbody tr, [role='row']:not([role='columnheader'])"
            )
            current_count = len(rows)

            if current_count == prev_count and iteration > 0:
                break
            prev_count = current_count

        # Финальный сбор
        rows = await page.query_selector_all(
            "table tbody tr, [class*='Table'] tbody tr, [role='row']:not([role='columnheader'])"
        )
        bonuses: list[BonusData] = []
        for row in rows:
            bonus = await self._parse_bonus_row(row)
            if bonus:
                bonuses.append(bonus)
        return bonuses

    async def _parse_bonus_row(self, row) -> BonusData | None:
        """Разобрать одну строку таблицы бонусов."""
        try:
            cells = await row.query_selector_all("td, [role='cell']")
            if len(cells) < 2:
                return None

            cell_texts = []
            for cell in cells:
                text = await cell.inner_text()
                cell_texts.append(text.strip())

            sku, name = await self._extract_sku_and_name(cell_texts[0], cells[0])
            if not sku:
                return None

            # Определяем активность бонуса и процент
            # Типичная структура: Товар | Статус бонуса | Процент бонуса
            bonus_active = False
            bonus_percent = 0.0
            status_blob = ""
            for text in cell_texts[1:]:
                status_blob += f" {text}"
                extracted_percent = self._extract_percent_from_text(text)
                if extracted_percent > 0:
                    bonus_percent = extracted_percent

            bonus_active = self._is_bonus_active(status_blob, bonus_percent)

            return BonusData(
                product_sku=sku,
                product_name=name,
                bonus_active=bonus_active,
                bonus_percent=bonus_percent,
                source="kaspi_bonus",
            )

        except Exception as e:
            logger.debug("MarketingScraper: ошибка парсинга бонусной строки: %s", e)
            return None
