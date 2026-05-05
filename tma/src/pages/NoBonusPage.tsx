/**
 * NoBonusPage — товары без активных бонусов.
 * Показывает текущие охваты и клики для каждого товара.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { AdsItem } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

export default function NoBonusPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();
  const [items, setItems] = useState<AdsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    showBackButton(() => navigate("/"));
  }, [showBackButton, navigate]);

  useEffect(() => {
    if (!api) return;
    api
      .getNoBonusProducts()
      .then((res) => setItems(res.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;

  return (
    <div className="page">
      <h1 className="page-title">❌ Без бонусов</h1>
      <p className="page-subtitle">
        Товары без активных бонусов — {items.length} позиций
      </p>

      {items.length === 0 ? (
        <div className="empty-state success">
          ✓ У всех товаров активен бонус!
        </div>
      ) : (
        <div className="product-list">
          {items.map((item) => (
            <NoBonusRow
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

function NoBonusRow({
  item,
  onClick,
}: {
  item: AdsItem;
  onClick: () => void;
}) {
  return (
    <div className="product-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="product-row-main">
        <div className="product-title">{item.title ?? item.sku}</div>
        <div className="product-sku">{item.sku}</div>
      </div>
      <div className="product-row-metrics">
        {item.total_impressions != null ? (
          <span className="metric-chip">
            Охваты: {(item.total_impressions).toLocaleString("ru-KZ")}
          </span>
        ) : (
          <span className="metric-chip muted">Нет данных</span>
        )}
        {item.total_clicks != null && (
          <span className="metric-chip">
            Клики: {item.total_clicks.toLocaleString("ru-KZ")}
          </span>
        )}
      </div>
    </div>
  );
}
