/**
 * Products — каталог товаров с наслоением рекламных метрик.
 * Первичный источник — таблица products (реальные названия, 539+ позиций).
 * Для товаров с активной рекламой показывает spend, CTR, клики.
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import type { ProductItem, ProductsQuery } from "../api/client";
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

export default function ProductsPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [items, setItems] = useState<ProductItem[]>([]);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState("spend_desc");
  const [adsFilter, setAdsFilter] = useState<"" | "with" | "without">("");
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query);
      setOffset(0);
    }, 400);
    return () => clearTimeout(timer);
  }, [query]);

  const fetchProducts = useCallback(async () => {
    if (!api) return;
    setLoading(true);
    setError(null);
    const params: ProductsQuery = { sort, limit: PAGE_SIZE, offset, period: 30 };
    if (debouncedQuery) params.q = debouncedQuery;
    if (adsFilter)       params.ads = adsFilter;

    try {
      const res = await api.getProducts(params);
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setLoading(false);
    }
  }, [api, sort, offset, debouncedQuery, adsFilter]);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const totalPages  = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="page">
      <h1 className="page-title">Товары</h1>

      {/* Строка поиска + сортировка */}
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
          onChange={(e) => { setSort(e.target.value); setOffset(0); }}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Фильтр по рекламе */}
      <div className="filter-tabs">
        {(["", "with", "without"] as const).map((v) => (
          <button
            key={v}
            className={`filter-tab${adsFilter === v ? " active" : ""}`}
            onClick={() => { setAdsFilter(v); setOffset(0); }}
          >
            {v === ""        ? "Все"        : v === "with" ? "С рекламой" : "Без рекламы"}
          </button>
        ))}
      </div>

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
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            ‹ Пред
          </button>
          <span className="page-info">{currentPage} / {totalPages}</span>
          <button
            className="btn btn-sm"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
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
          {item.has_ads && <span className="ads-badge">Реклама</span>}
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
