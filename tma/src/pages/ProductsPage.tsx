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
import type { MissingFilter, ProductItem, ProductsQuery } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

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

  const [items, setItems] = useState<ProductItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState(queryFromUrl);
  const [debouncedQuery, setDebouncedQuery] = useState(queryFromUrl);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

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

  const fetchProducts = useCallback(async () => {
    if (!api) return;
    setLoading(true);
    setError(null);
    const query: ProductsQuery = { sort, limit: PAGE_SIZE, offset, period: 30 };
    if (debouncedQuery) query.q = debouncedQuery;
    if (adsFilter)      query.ads = adsFilter;
    if (missing)        query.missing = missing;

    try {
      const res = await api.getProducts(query);
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setLoading(false);
    }
  }, [api, sort, offset, debouncedQuery, adsFilter, missing]);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

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

      {totalPages > 1 && (
        <div className="pagination">
          <button
            className="btn btn-sm"
            disabled={offset === 0}
            onClick={() => updateParam("offset", String(Math.max(0, offset - PAGE_SIZE)))}
          >
            ‹ Пред
          </button>
          <span className="page-info">{currentPage} / {totalPages}</span>
          <button
            className="btn btn-sm"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => updateParam("offset", String(offset + PAGE_SIZE))}
          >
            След ›
          </button>
        </div>
      )}
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
