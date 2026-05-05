/**
 * WastedBudgetPage — товары с ROI < 0 (сливают бюджет).
 * Сортировка по потерям. Рекомендации для каждого товара.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { WastedBudgetItem } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

export default function WastedBudgetPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();
  const [items, setItems] = useState<WastedBudgetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

  useEffect(() => {
    if (!api) return;
    api
      .getWastedBudget(0)
      .then((res) => setItems(res.items as WastedBudgetItem[]))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;

  // Сортируем по убыванию потерь (наибольшие потери сверху)
  const sorted = [...items].sort((a, b) => (a.total_spend ?? 0) - (b.total_spend ?? 0) > 0
    ? -1
    : 1
  );

  return (
    <div className="page">
      <h1 className="page-title">🔥 Слив бюджета</h1>
      <p className="page-subtitle">
        Товары с отрицательным ROI — затраты превышают выручку
      </p>

      {sorted.length === 0 ? (
        <div className="empty-state success">
          ✓ Все товары в плюсе — нет потерь!
        </div>
      ) : (
        <div className="product-list">
          {sorted.map((item) => (
            <WastedItem
              key={item.sku}
              item={item}
              onClick={() => navigate(`/products/${encodeURIComponent(item.sku)}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function WastedItem({
  item,
  onClick,
}: {
  item: WastedBudgetItem;
  onClick: () => void;
}) {
  const loss =
    item.total_spend != null && item.revenue != null
      ? item.total_spend - item.revenue
      : item.total_spend;

  return (
    <div className="product-row wasted-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="product-row-main">
        <div className="product-title">{item.title ?? item.sku}</div>
        <div className="product-sku">{item.sku}</div>
        <div className="wasted-recommendation">
          💡 Проверить ставки или приостановить кампанию
        </div>
      </div>
      <div className="product-row-metrics">
        <span className="metric-chip">
          Потрачено:{" "}
          {(item.total_spend ?? 0).toLocaleString("ru-KZ", {
            maximumFractionDigits: 0,
          })}{" "}
          ₸
        </span>
        {loss != null && (
          <span className="metric-chip roi-negative">
            Потери: {loss.toLocaleString("ru-KZ", { maximumFractionDigits: 0 })} ₸
          </span>
        )}
        <span className="metric-chip roi-negative">
          ROI {item.roi_percent?.toFixed(0) ?? "—"}%
        </span>
      </div>
    </div>
  );
}
