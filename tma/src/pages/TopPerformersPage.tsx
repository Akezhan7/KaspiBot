/**
 * TopPerformersPage — топ товаров по ROAS.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Trophy } from "lucide-react";
import type { AdsItem } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

export default function TopPerformersPage() {
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
      .getTopPerformers(30)
      .then((res) => setItems(res.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;

  return (
    <div className="page">
      <div className="title-row">
        <Trophy className="title-icon" />
        <h1 className="page-title">Топ исполнители</h1>
      </div>
      <p className="page-subtitle">Лучший ROAS (выручка / затраты)</p>

      {items.length === 0 ? (
        <div className="empty-state">Нет данных</div>
      ) : (
        <div className="product-list">
          {items.map((item, idx) => (
            <PerformerRow
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

function PerformerRow({
  rank,
  item,
  onClick,
}: {
  rank: number;
  item: AdsItem;
  onClick: () => void;
}) {
  return (
    <div className="product-row" onClick={onClick} role="button" tabIndex={0}>
      <div className="rank-badge top">{rank}</div>
      <div className="product-row-main">
        <div className="product-title">{item.title ?? item.sku}</div>
        <div className="product-sku">{item.sku}</div>
      </div>
      <div className="product-row-metrics">
        {item.roas != null && (
          <span className="metric-chip highlight">
            ROAS {item.roas.toFixed(2)}
          </span>
        )}
        {item.roi_percent != null && (
          <span className={`metric-chip ${item.roi_percent >= 0 ? "roi-positive" : "roi-negative"}`}>
            ROI {item.roi_percent.toFixed(0)}%
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
