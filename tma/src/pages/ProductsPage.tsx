/**
 * Products — список всех товаров с рекламными метриками.
 * Пагинация, сортировка, фильтры, поиск.
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { PackageSearch } from "lucide-react";
import type { ProductItem, ProductsQuery } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

const SORT_OPTIONS = [
  { value: "spend_desc", label: "По затратам ↓" },
  { value: "spend_asc", label: "По затратам ↑" },
  { value: "ctr_desc", label: "По CTR ↓" },
  { value: "clicks_desc", label: "По кликам ↓" },
  { value: "roi_desc", label: "По ROI ↓" },
  { value: "roi_asc", label: "По ROI ↑" },
];

const PAGE_SIZE = 20;

export default function ProductsPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [items, setItems] = useState<ProductItem[]>([]);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState("spend_desc");
  const [bonusFilter, setBonusFilter] = useState<"" | "with" | "without">("");
  const [roiFilter, setRoiFilter] = useState<"" | "positive" | "negative">("");
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

  // Debounce для поиска
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
    const params: ProductsQuery = {
      sort,
      limit: PAGE_SIZE,
      offset,
      period: 30,
    };
    if (debouncedQuery) params.q = debouncedQuery;
    if (bonusFilter) params.bonus = bonusFilter;
    if (roiFilter) params.roi = roiFilter;

    try {
      const res = await api.getProducts(params);
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setLoading(false);
    }
  }, [api, sort, offset, debouncedQuery, bonusFilter, roiFilter]);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="page">
      <div className="title-row">
        <PackageSearch className="title-icon" />
        <h1 className="page-title">Товары</h1>
      </div>

      {/* Фильтры */}
      <div className="filters-row">
        <input
          className="search-input"
          type="text"
          placeholder="Поиск по SKU или названию..."
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

      <div className="filters-row">
        <select
          className="sort-select"
          value={bonusFilter}
          onChange={(e) => {
            setBonusFilter(e.target.value as "" | "with" | "without");
            setOffset(0);
          }}
        >
          <option value="">Бонус: все</option>
          <option value="with">С бонусом</option>
          <option value="without">Без бонуса</option>
        </select>
        <select
          className="sort-select"
          value={roiFilter}
          onChange={(e) => {
            setRoiFilter(e.target.value as "" | "positive" | "negative");
            setOffset(0);
          }}
        >
          <option value="">ROI: все</option>
          <option value="positive">ROI &gt; 0</option>
          <option value="negative">ROI &lt; 0</option>
        </select>
      </div>

      <div className="list-meta">
        {total > 0 ? `Найдено: ${total}` : ""}
      </div>

      {loading && <div className="page-loader">Загрузка...</div>}
      {error && <div className="page-error">Ошибка: {error}</div>}

      {/* Список товаров */}
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

      {/* Пагинация */}
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
  const roiClass =
    item.roi_percent == null
      ? ""
      : item.roi_percent >= 0
      ? "roi-positive"
      : "roi-negative";

  return (
    <div className="product-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="product-row-main">
        <div className="product-title">{item.title ?? item.sku}</div>
        <div className="product-sku">{item.sku}</div>
      </div>
      <div className="product-row-metrics">
        <span className="metric-chip">
          {item.spend.toLocaleString("ru-KZ", { maximumFractionDigits: 0 })} ₸
        </span>
        <span className="metric-chip">CTR {item.avg_ctr.toFixed(1)}%</span>
        {item.roi_percent != null && (
          <span className={`metric-chip ${roiClass}`}>
            ROI {item.roi_percent.toFixed(0)}%
          </span>
        )}
      </div>
    </div>
  );
}
