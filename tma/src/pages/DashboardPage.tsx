/**
 * Dashboard — главная страница TMA.
 * Показывает сводные метрики, сигналы и навигацию.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, BadgeX } from "lucide-react";
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

  const todaySpend = today?.total_spend ?? 0;
  const todayClicks = today?.total_clicks ?? 0;
  const todayCtr = typeof today?.avg_ctr === "number" ? today.avg_ctr : null;

  return (
    <div className="page">
      <h1 className="page-title">Kaspi Ads</h1>

      {/* Алерты */}
      {alerts.length > 0 && (
        <div className="alerts-block">
          {alerts.map((a, i) => (
            <div
              key={i}
              className={`alert alert-${a.type === "wasted_budget" ? "warning" : "info"}`}
              onClick={() => navigate(a.type === "wasted_budget" ? "/wasted-budget" : "/no-bonus")}
            >
              {a.type === "wasted_budget"
                ? <AlertTriangle size={14} />
                : <BadgeX size={14} />}
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

      {/* Сегодня */}
      {todaySpend > 0 && (
        <div className="today-block">
          <span className="today-label">Сегодня</span>
          <div className="today-stats">
            <span className="today-stat">{formatMoney(todaySpend)} ₸</span>
            <span className="today-sep">·</span>
            <span className="today-stat">{todayClicks} кликов</span>
            {todayCtr != null && (
              <>
                <span className="today-sep">·</span>
                <span className="today-stat">CTR {todayCtr.toFixed(2)}%</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* Навигация */}
      <div className="nav-links">
        <NavItem label="Все товары" onClick={() => navigate("/products")} />
        <NavItem label="Топ исполнители" onClick={() => navigate("/top-performers")} />
        <NavItem label="Слив бюджета" onClick={() => navigate("/wasted-budget")} />
        <NavItem label="Без бонусов" onClick={() => navigate("/no-bonus")} />
        <NavItem label="Кликабельные" onClick={() => navigate("/most-clickable")} />
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

function NavItem({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="nav-item" onClick={onClick}>
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
