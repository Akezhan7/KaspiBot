/**
 * ProductDetailPage — детальная карточка товара.
 * Метрики, тренды (линейный график), история CPC, статус бонуса.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CircleX, Gift } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { ProductDetailResponse } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

const TREND_PERIODS = [7, 14, 30] as const;

export default function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [data, setData] = useState<ProductDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [trendDays, setTrendDays] = useState<7 | 14 | 30>(30);

  useEffect(() => {
    showBackButton(() => navigate("/products"));
  }, [showBackButton, navigate]);

  useEffect(() => {
    if (!api || !sku) return;
    setLoading(true);
    setError(null);
    api
      .getProduct(decodeURIComponent(sku), { period: 30, trend_days: trendDays })
      .then(setData)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api, sku, trendDays]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;
  if (!data) return null;

  const { roi, roas, trends, latest_data } = data;
  const roiPercent = roi?.roi_percent;
  const roiClass = roiPercent == null ? "" : roiPercent >= 0 ? "roi-positive" : "roi-negative";

  const bonusActive = latest_data?.bonus_active === 1 || latest_data?.bonus_active === true;
  const bonusPercent = latest_data?.bonus_percent as number | undefined;

  // Форматирование дат для оси X
  const chartData = trends.map((t) => ({
    ...t,
    day: t.day.slice(5), // "MM-DD"
  }));

  return (
    <div className="page">
      <h1 className="page-title">{data.title ?? data.sku}</h1>
      <div className="sku-label">{data.sku}</div>

      {/* Основные метрики */}
      <div className="metrics-grid">
        <MetricCard
          label="Потрачено"
          value={`${fmt(roi?.spend)} ₸`}
        />
        <MetricCard
          label="Выручка"
          value={`${fmt(roi?.revenue)} ₸`}
        />
        <MetricCard
          label="ROI"
          value={roiPercent != null ? `${roiPercent.toFixed(1)}%` : "—"}
          className={roiClass}
        />
        <MetricCard
          label="ROAS"
          value={roas != null ? roas.toFixed(2) : "—"}
        />
      </div>

      {/* Бонус */}
      <div className={`bonus-badge ${bonusActive ? "bonus-active" : "bonus-inactive"}`}>
        {bonusActive
          ? `Бонус активен: ${bonusPercent ?? "—"}%`
          : "Бонус не активен"}
        <span className="bonus-icon-wrap">
          {bonusActive ? <Gift size={14} /> : <CircleX size={14} />}
        </span>
      </div>

      {/* Выбор периода тренда */}
      <div className="period-tabs">
        {TREND_PERIODS.map((d) => (
          <button
            key={d}
            className={`period-tab ${trendDays === d ? "active" : ""}`}
            onClick={() => setTrendDays(d)}
          >
            {d}д
          </button>
        ))}
      </div>

      {/* График трендов */}
      {chartData.length > 0 ? (
        <div className="chart-block">
          <h3 className="chart-title">Тренды</h3>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chartData}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={40} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line
                type="monotone"
                dataKey="clicks"
                stroke="var(--tg-theme-button-color, #2196f3)"
                dot={false}
                name="Клики"
              />
              <Line
                type="monotone"
                dataKey="spend"
                stroke="#f44336"
                dot={false}
                name="Затраты ₸"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="empty-state">Нет данных для графика</div>
      )}

      {/* CTR тренд */}
      {chartData.length > 0 && (
        <div className="chart-block">
          <h3 className="chart-title">CTR (%)</h3>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={chartData}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={35} />
              <Tooltip formatter={(v) => [`${Number(v).toFixed(2)}%`, "CTR"]} />
              <Line
                type="monotone"
                dataKey="ctr"
                stroke="#4caf50"
                dot={false}
                name="CTR%"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={`metric-card ${className}`}>
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function fmt(v: number | undefined | null): string {
  if (v == null) return "—";
  return v.toLocaleString("ru-KZ", { maximumFractionDigits: 0 });
}
