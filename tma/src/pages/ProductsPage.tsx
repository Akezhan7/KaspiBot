/**
 * Products — каталог товаров с наслоением рекламных метрик.
 *
 * Состояние списка (фильтр / страница / сортировка / поиск) хранится в
 * query-параметрах URL. Это решает проблему «возврат с детальной страницы
 * сбрасывает фильтр» — React Router восстанавливает state из URL автоматически.
 *
 * Фильтры:
 *   ads=with|without — устаревший, оставлен для совместимости со старыми ссылками
 *   missing=ads|external|bonus_seller|bonus_review — товары, у которых
 *                                                    нет указанного признака
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type {
  MissingFilter,
  ProductItem,
  ProductsQuery,
  ReportPeriod,
} from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import Pagination from "../components/Pagination";
import "../styles/pages.css";

/** Ключ в sessionStorage, в который сохраняется URL последнего состояния
 *  списка товаров — используется ProductDetailPage чтобы вернуться на ту же
 *  страницу/фильтр после системной кнопки «назад». */
export const PRODUCTS_LIST_URL_STORAGE_KEY = "kaspibot.productsListUrl";

const SORT_OPTIONS = [
  { value: "spend_desc", label: "По затратам ↓" },
  { value: "spend_asc",  label: "По затратам ↑" },
  { value: "ctr_desc",   label: "По CTR ↓" },
  { value: "clicks_desc",label: "По кликам ↓" },
];

const PAGE_SIZE = 20;

const MISSING_LABELS: Record<MissingFilter, string> = {
  ads: "Без рекламы",
  external: "Без внешней рекламы",
  bonus_seller: "Без бонуса продавца",
  bonus_review: "Без бонуса за отзыв",
};

const VALID_MISSING: MissingFilter[] = [
  "ads", "external", "bonus_seller", "bonus_review",
];

function isMissingFilter(v: string | null): v is MissingFilter {
  return v != null && VALID_MISSING.includes(v as MissingFilter);
}

const REPORT_PERIODS: ReportPeriod[] = [7, 30];
const REPORT_PERIOD_STORAGE_KEY = "kaspibot.reportPeriod";

function parseReportPeriod(v: string | null): ReportPeriod {
  const n = Number(v);
  return REPORT_PERIODS.includes(n as ReportPeriod) ? (n as ReportPeriod) : 7;
}

export default function ProductsPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [params, setParams] = useSearchParams();

  const sort = params.get("sort") ?? "spend_desc";
  const adsFilter = (params.get("ads") as "" | "with" | "without") || "";
  const missing: MissingFilter | "" = isMissingFilter(params.get("missing"))
    ? (params.get("missing") as MissingFilter)
    : "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const queryFromUrl = params.get("q") ?? "";

  // Период отчёта: URL → localStorage → 7. Сохраняется обратно в localStorage,
  // чтобы пользователю не приходилось каждый раз выбирать вручную.
  const reportPeriod: ReportPeriod = parseReportPeriod(
    params.get("report_period")
      ?? (typeof window !== "undefined"
        ? localStorage.getItem(REPORT_PERIOD_STORAGE_KEY)
        : null),
  );

  const [items, setItems] = useState<ProductItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState(queryFromUrl);
  const [debouncedQuery, setDebouncedQuery] = useState(queryFromUrl);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

  // Сохраняем текущий URL списка чтобы детальная страница могла вернуться
  // в точности к тому же фильтру/странице. URL берётся из живых params
  // (не из window.location, чтобы это работало и в SSR / non-browser окружениях).
  useEffect(() => {
    try {
      const search = params.toString();
      const url = search ? `/products?${search}` : "/products";
      sessionStorage.setItem(PRODUCTS_LIST_URL_STORAGE_KEY, url);
    } catch {
      // sessionStorage может быть недоступен — не критично
    }
  }, [params]);

  // debounce ввода поиска -> в URL
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query);
      const next = new URLSearchParams(params);
      if (query) next.set("q", query);
      else next.delete("q");
      next.delete("offset");
      setParams(next, { replace: true });
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const updateParam = useCallback(
    (key: string, value: string | null, opts: { resetOffset?: boolean } = {}) => {
      const next = new URLSearchParams(params);
      if (value == null || value === "") next.delete(key);
      else next.set(key, value);
      if (opts.resetOffset) next.delete("offset");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const buildQuery = useCallback(
    (override?: Partial<ProductsQuery>): ProductsQuery => {
      const q: ProductsQuery = {
        sort,
        limit: PAGE_SIZE,
        offset,
        period: 30,
        report_period: reportPeriod,
      };
      if (debouncedQuery) q.q = debouncedQuery;
      if (adsFilter) q.ads = adsFilter;
      if (missing) q.missing = missing;
      return { ...q, ...override };
    },
    [sort, offset, debouncedQuery, adsFilter, missing, reportPeriod],
  );

  const fetchProducts = useCallback(async () => {
    if (!api) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getProducts(buildQuery());
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setLoading(false);
    }
  }, [api, buildQuery]);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const handleReportPeriodChange = useCallback(
    (next: ReportPeriod) => {
      if (next === reportPeriod) return;
      try {
        localStorage.setItem(REPORT_PERIOD_STORAGE_KEY, String(next));
      } catch {
        // localStorage может быть недоступен (private mode и т.п.)
      }
      updateParam("report_period", String(next), { resetOffset: true });
    },
    [reportPeriod, updateParam],
  );

  const handleExport = useCallback(async () => {
    if (!api) return;
    setExporting(true);
    try {
      // limit/offset для экспорта не важны — сервер возвращает все строки
      // после применения фильтров.
      await api.downloadProductsExport(buildQuery({ limit: undefined, offset: undefined }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка экспорта");
    } finally {
      setExporting(false);
    }
  }, [api, buildQuery]);

  const totalPages  = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  const titleText = useMemo(() => {
    if (missing) return MISSING_LABELS[missing];
    if (adsFilter === "with") return "Товары с рекламой";
    if (adsFilter === "without") return "Товары без рекламы";
    return "Товары";
  }, [missing, adsFilter]);

  return (
    <div className="page">
      <h1 className="page-title">{titleText}</h1>

      <div className="toolbar-row">
        <div
          className="period-toggle"
          role="group"
          aria-label="Период отчёта"
        >
          {REPORT_PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              className={`period-toggle-btn${p === reportPeriod ? " active" : ""}`}
              onClick={() => handleReportPeriodChange(p)}
            >
              {p} дн
            </button>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-export"
          onClick={handleExport}
          disabled={exporting}
        >
          {exporting ? "Экспорт..." : "Экспорт в Excel"}
        </button>
      </div>

      <div className="filters-row">
        <input
          className="search-input"
          type="text"
          placeholder="Поиск по названию или SKU..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="sort-select"
          value={sort}
          onChange={(e) => updateParam("sort", e.target.value, { resetOffset: true })}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Универсальный фильтр (с / без рекламы) — скрыт когда выбран missing */}
      {!missing && (
        <div className="filter-tabs">
          {(["", "with", "without"] as const).map((v) => (
            <button
              key={v}
              className={`filter-tab${adsFilter === v ? " active" : ""}`}
              onClick={() => updateParam("ads", v || null, { resetOffset: true })}
            >
              {v === "" ? "Все" : v === "with" ? "С рекламой" : "Без рекламы"}
            </button>
          ))}
        </div>
      )}

      {missing && (
        <button
          className="btn-sm filter-clear"
          onClick={() => updateParam("missing", null, { resetOffset: true })}
        >
          ✕ Сбросить фильтр
        </button>
      )}

      <div className="list-meta">
        {total > 0 ? `Найдено: ${total}` : ""}
      </div>

      {loading && <div className="page-loader">Загрузка...</div>}
      {error   && <div className="page-error">Ошибка: {error}</div>}

      {!loading && !error && (
        <div className="product-list">
          {items.length === 0 ? (
            <div className="empty-state">Товары не найдены</div>
          ) : (
            items.map((item) => (
              <ProductRow
                key={item.sku}
                item={item}
                onClick={() => navigate(`/products/${encodeURIComponent(item.sku)}`)}
              />
            ))
          )}
        </div>
      )}

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPageChange={(p) => {
          const nextOffset = (p - 1) * PAGE_SIZE;
          updateParam("offset", nextOffset > 0 ? String(nextOffset) : null);
          // Прокрутить наверх — иначе при переходе на следующую страницу
          // пользователь оказывается в середине нового списка.
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
    </div>
  );
}

function ProductRow({ item, onClick }: { item: ProductItem; onClick: () => void }) {
  return (
    <div className="product-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="product-row-main">
        <div className="product-row-header">
          <div className="product-title">{item.title ?? item.sku}</div>
          <div className="product-row-flags">
            {item.has_ads && <span className="flag-dot flag-ads" title="Реклама" />}
            {item.has_external_ads && <span className="flag-dot flag-external" title="Внешняя реклама" />}
            {item.has_bonus_seller && <span className="flag-dot flag-seller" title="Бонус продавца" />}
            {item.has_bonus_review && <span className="flag-dot flag-review" title="Бонус за отзыв" />}
          </div>
        </div>
        <div className="product-sku">{item.sku}</div>
      </div>
      {item.has_ads && (
        <div className="product-row-metrics">
          <span className="metric-chip">
            {item.spend.toLocaleString("ru-KZ", { maximumFractionDigits: 0 })} ₸
          </span>
          {item.avg_ctr > 0 && (
            <span className="metric-chip">CTR {item.avg_ctr.toFixed(1)}%</span>
          )}
          {item.clicks > 0 && (
            <span className="metric-chip">{item.clicks} кликов</span>
          )}
        </div>
      )}
    </div>
  );
}
