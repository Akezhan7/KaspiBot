/**
 * MostClickablePage — топ товаров по CTR.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { AdsItem } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

export default function MostClickablePage() {
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
      .getMostClickable(30)
      .then((res) => setItems(res.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;

  return (
    <div className="page">
      <h1 className="page-title">👆 Кликабельные</h1>
      <p className="page-subtitle">Топ товаров по CTR</p>

      {items.length === 0 ? (
        <div className="empty-state">Нет данных</div>
      ) : (
        <div className="product-list">
          {items.map((item, idx) => (
            <ClickableRow
              key={item.sku}
              rank={idx + 1}
              item={item}
              onClick={() => navigate(`/products/${encodeURIComponent(item.sku)}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ClickableRow({
  rank,
  item,
  onClick,
}: {
  rank: number;
  item: AdsItem;
  onClick: () => void;
}) {
  const ctr = item.avg_ctr ?? item.ctr;
  const clicks = item.total_clicks ?? item.clicks;

  return (
    <div className="product-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="rank-badge">{rank}</div>
      <div className="product-row-main">
        <div className="product-title">{item.title ?? item.sku}</div>
        <div className="product-sku">{item.sku}</div>
      </div>
      <div className="product-row-metrics">
        {ctr != null && (
          <span className="metric-chip highlight">
            CTR {ctr.toFixed(2)}%
          </span>
        )}
        {clicks != null && (
          <span className="metric-chip">
            Клики: {clicks.toLocaleString("ru-KZ")}
          </span>
        )}
        {item.total_spend != null && (
          <span className="metric-chip">
            {item.total_spend.toLocaleString("ru-KZ", { maximumFractionDigits: 0 })} ₸
          </span>
        )}
      </div>
    </div>
  );
}
