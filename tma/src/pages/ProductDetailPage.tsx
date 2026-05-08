/**
 * ProductDetailPage — детальная карточка товара.
 *
 * Парсинг работает только за последние KASPI_MARKETING_REPORT_DAYS (=7д),
 * поэтому показываем фактически релевантные метрики:
 *   - Расход, Показы, Клики, CTR, CPC
 *   - Статус бонуса (активен / неактивен / нет данных)
 *   - Тренды (clicks/spend/ctr) — за весь доступный период истории
 *
 * ROI/ROAS/Выручка скрыты — Kaspi не отдаёт revenue в выгрузках.
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

export default function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [data, setData] = useState<ProductDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    showBackButton(() => navigate("/products"));
  }, [showBackButton, navigate]);

  useEffect(() => {
    if (!api || !sku) return;
    setLoading(true);
    setError(null);
    api
      .getProduct(decodeURIComponent(sku), { period: 30, trend_days: 30 })
      .then(setData)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api, sku]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;
  if (!data) return null;

  const latest = data.latest_data ?? {};
  const spend = num(latest.spend) ?? num(data.roi?.spend);
  const impressions = num(latest.impressions);
  const clicks = num(latest.clicks);
  const ctr = num(latest.ctr);
  const cpc = num(latest.cpc);

  const bonusActive =
    latest.bonus_active === 1 || latest.bonus_active === true;
  const bonusPercent = num(latest.bonus_percent);
  const hasBonusData = latest.bonus_scraped_at != null;

  const chartData = data.trends.map((t) => ({
    ...t,
    day: t.day.slice(5),
  }));

  return (
    <div className="page">
      <h1 className="page-title">{data.title ?? data.sku}</h1>
      <div className="sku-label">{data.sku}</div>

      {/* Основные метрики из последнего скрапинга */}
      <div className="metrics-grid">
        <MetricCard label="Расход" value={`${fmt(spend)} ₸`} />
        <MetricCard label="Показы" value={fmt(impressions)} />
        <MetricCard label="Клики" value={fmt(clicks)} />
        <MetricCard
          label="CTR"
          value={ctr != null ? `${ctr.toFixed(2)}%` : "—"}
        />
        <MetricCard
          label="CPC"
          value={cpc != null ? `${cpc.toFixed(0)} ₸` : "—"}
        />
        {hasBonusData ? (
          <MetricCard
            label="Бонус"
            value={bonusActive ? `${bonusPercent ?? 0}%` : "Нет"}
            className={bonusActive ? "roi-positive" : "roi-negative"}
          />
        ) : (
          <MetricCard label="Бонус" value="—" />
        )}
      </div>

      {/* Подробный бонус-бейдж */}
      {hasBonusData && (
        <div
          className={`bonus-badge ${bonusActive ? "bonus-active" : "bonus-inactive"}`}
        >
          {bonusActive
            ? `Бонус активен: ${bonusPercent ?? "—"}%`
            : "Бонус не активен"}
          <span className="bonus-icon-wrap">
            {bonusActive ? <Gift size={14} /> : <CircleX size={14} />}
          </span>
        </div>
      )}

      {/* График трендов: клики + расход */}
      {chartData.length > 1 ? (
        <div className="chart-block">
          <h3 className="chart-title">Динамика</h3>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chartData}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={40} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line
                type="monotone"
                dataKey="clicks"
                stroke="#0066ff"
                strokeWidth={2}
                dot={false}
                name="Клики"
              />
              <Line
                type="monotone"
                dataKey="spend"
                stroke="#dc2626"
                strokeWidth={2}
                dot={false}
                name="Расход ₸"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="empty-state">
          Динамика будет доступна, когда накопится несколько дней истории
        </div>
      )}

      {/* CTR тренд — отдельно если данных хотя бы 2 точки */}
      {chartData.length > 1 && (
        <div className="chart-block">
          <h3 className="chart-title">CTR по дням</h3>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={chartData}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={35} />
              <Tooltip formatter={(v) => [`${Number(v).toFixed(2)}%`, "CTR"]} />
              <Line
                type="monotone"
                dataKey="ctr"
                stroke="#16a34a"
                strokeWidth={2}
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

function num(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

function fmt(v: number | undefined | null): string {
  if (v == null) return "—";
  return v.toLocaleString("ru-KZ", { maximumFractionDigits: 0 });
}
