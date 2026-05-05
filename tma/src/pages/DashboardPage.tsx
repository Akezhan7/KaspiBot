
/**
 * Dashboard — главная страница TMA.
 * Показывает сводные метрики, сигналы и мини-статистику за 7 дней.
 */
import { type ReactNode, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import {
  AlertTriangle,
  BadgeX,
  ChartColumn,
  MousePointerClick,
  Package,
  Trophy,
  Wallet,
} from "lucide-react";
import type { DashboardResponse } from "../api/client";
import { ApiError } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

export default function DashboardPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { hideBackButton } = useTelegram();
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  useEffect(() => {
    hideBackButton();
  }, [hideBackButton]);

  useEffect(() => {
    if (!api) return;
    api
      .getDashboard()
      .then(setData)
      .catch((err: ApiError | Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api]);

  const handleTriggerScrape = async () => {
    if (!api || triggering) return;
    setTriggering(true);
    try {
      await api.triggerScrape();
      alert("Скрапинг запущен! Результаты появятся через несколько минут.");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Ошибка";
      alert(`Ошибка: ${msg}`);
    } finally {
      setTriggering(false);
    }
  };

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;
  if (!data) return null;

  const { total_stats, today, alerts } = data;

  // Данные для мини-диаграммы (берём из today, если доступны)
  const chartData = today?.total_spend != null
    ? [{ name: "Сегодня", spend: today.total_spend ?? 0, clicks: today.total_clicks ?? 0 }]
    : [];

  return (
    <div className="page">
      <div className="title-row">
        <ChartColumn className="title-icon" />
        <h1 className="page-title">Kaspi Ads Dashboard</h1>
      </div>

      {/* Alerts */}
      {alerts.length > 0 && (
        <div className="alerts-block">
          {alerts.map((a, i) => (
            <div
              key={i}
              className={`alert alert-${a.type === "wasted_budget" ? "warning" : "info"}`}
              onClick={() => navigate(a.type === "wasted_budget" ? "/wasted-budget" : "/no-bonus")}
            >
              {a.type === "wasted_budget" ? <AlertTriangle size={14} /> : <BadgeX size={14} />}
              {a.message}
            </div>
          ))}
        </div>
      )}

      {/* Метрики */}
      <div className="metrics-grid">
        <MetricCard
          label="Всего потрачено"
          value={`${formatMoney(total_stats?.total_spend)} ₸`}
        />
        <MetricCard
          label="Средний CPC"
          value={`${formatMoney(total_stats?.avg_cpc)} ₸`}
        />
        <MetricCard
          label="Средний CTR"
          value={`${formatPercent(total_stats?.avg_ctr)}%`}
        />
        <MetricCard
          label="Товаров с рекламой"
          value={String(total_stats?.products_with_ads ?? 0)}
        />
      </div>

      {/* Мини-диаграмма */}
      {chartData.length > 0 && (
        <div className="chart-block">
          <h3 className="chart-title">Затраты сегодня</h3>
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={chartData}>
              <XAxis dataKey="name" hide />
              <YAxis hide />
              <Tooltip formatter={(v) => [`${Number(v)} ₸`, "Затраты"]} />
              <Bar dataKey="spend" fill="var(--tg-theme-button-color, #2196f3)" radius={4} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Навигация */}
      <div className="nav-links">
        <NavItem label="Все товары" icon={<Package size={18} />} onClick={() => navigate("/products")} />
        <NavItem label="Топ исполнители" icon={<Trophy size={18} />} onClick={() => navigate("/top-performers")} />
        <NavItem label="Слив бюджета" icon={<Wallet size={18} />} onClick={() => navigate("/wasted-budget")} />
        <NavItem label="Без бонусов" icon={<BadgeX size={18} />} onClick={() => navigate("/no-bonus")} />
        <NavItem
          label="Кликабельные"
          icon={<MousePointerClick size={18} />}
          onClick={() => navigate("/most-clickable")}
        />
      </div>

      {/* Кнопка запуска скрапинга */}
      <button
        className="btn btn-secondary"
        onClick={handleTriggerScrape}
        disabled={triggering}
      >
        {triggering ? "Запускаю..." : "Запустить сбор данных"}
      </button>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function NavItem({
  label,
  icon,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="nav-item" onClick={onClick}>
      <span className="nav-icon">{icon}</span>
      <span className="nav-label">{label}</span>
      <span className="nav-arrow">›</span>
    </button>
  );
}

function formatMoney(v: number | undefined | null): string {
  if (v == null) return "—";
  return v.toLocaleString("ru-KZ", { maximumFractionDigits: 0 });
}

function formatPercent(v: number | undefined | null): string {
  if (v == null) return "—";
  return v.toFixed(2);
}
