/**
 * Dashboard — главная страница TMA.
 * Показывает сводные метрики, сигналы и навигацию.
 * Вкладки скрываются, если по ним нет данных (top-performers, wasted-budget,
 * no-bonus, most-clickable) — чтобы пользователь не тыкал в пустоту.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, BadgeX } from "lucide-react";
import type { DashboardResponse, ReportPeriod } from "../api/client";
import { ApiError } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import "../styles/pages.css";

interface Counts {
  topPerformers: number;
  wastedBudget: number;
  mostClickable: number;
}

const REPORT_PERIODS: ReportPeriod[] = [7, 30];
const REPORT_PERIOD_STORAGE_KEY = "kaspibot.reportPeriod";

function readStoredReportPeriod(): ReportPeriod {
  if (typeof window === "undefined") return 7;
  const raw = Number(localStorage.getItem(REPORT_PERIOD_STORAGE_KEY));
  return REPORT_PERIODS.includes(raw as ReportPeriod) ? (raw as ReportPeriod) : 7;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const api = useApi();
  const { hideBackButton } = useTelegram();
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [counts, setCounts] = useState<Counts>({
    topPerformers: 0,
    wastedBudget: 0,
    mostClickable: 0,
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [reportPeriod, setReportPeriod] = useState<ReportPeriod>(readStoredReportPeriod);

  useEffect(() => {
    hideBackButton();
  }, [hideBackButton]);

  useEffect(() => {
    if (!api) return;
    setLoading(true);

    Promise.all([
      api.getDashboard({ report_period: reportPeriod }),
      api.getTopPerformers(1).catch(() => ({ count: 0, items: [] })),
      api.getWastedBudget(0).catch(() => ({ count: 0, items: [], threshold: 0 })),
      api.getMostClickable(1).catch(() => ({ count: 0, items: [] })),
    ])
      .then(([dash, tp, wb, mc]) => {
        setData(dash);
        setCounts({
          topPerformers: tp.count ?? tp.items?.length ?? 0,
          wastedBudget: wb.count ?? wb.items?.length ?? 0,
          mostClickable: mc.count ?? mc.items?.length ?? 0,
        });
      })
      .catch((err: ApiError | Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api, reportPeriod]);

  const handleReportPeriodChange = useCallback((next: ReportPeriod) => {
    setReportPeriod(next);
    try {
      localStorage.setItem(REPORT_PERIOD_STORAGE_KEY, String(next));
    } catch {
      // localStorage недоступен — не критично
    }
  }, []);

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

      <div className="toolbar-row">
        <div
          className="period-toggle"
          role="group"
          aria-label="Период отчёта"
        >
          {REPORT_PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              className={`period-toggle-btn${p === reportPeriod ? " active" : ""}`}
              onClick={() => handleReportPeriodChange(p)}
            >
              {p} дн
            </button>
          ))}
        </div>
      </div>

      {/* Алерты — только если в них есть смысл */}
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
        {counts.mostClickable > 0 && (
          <NavItem
            label="Кликабельные"
            badge={counts.mostClickable}
            onClick={() => navigate("/most-clickable")}
          />
        )}
        {counts.topPerformers > 0 && (
          <NavItem
            label="Топ исполнители"
            badge={counts.topPerformers}
            onClick={() => navigate("/top-performers")}
          />
        )}
        {counts.wastedBudget > 0 && (
          <NavItem
            label="Слив бюджета"
            badge={counts.wastedBudget}
            onClick={() => navigate("/wasted-budget")}
          />
        )}
      </div>

      <div className="section-header">Что отсутствует</div>
      <div className="nav-links">
        <NavItem
          label="Без рекламы"
          onClick={() => navigate("/products?missing=ads")}
        />
        <NavItem
          label="Без внешней рекламы"
          onClick={() => navigate("/products?missing=external")}
        />
        <NavItem
          label="Без бонуса продавца"
          onClick={() => navigate("/products?missing=bonus_seller")}
        />
        <NavItem
          label="Без бонуса за отзыв"
          onClick={() => navigate("/products?missing=bonus_review")}
        />
      </div>

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
  badge,
  onClick,
}: {
  label: string;
  badge?: number;
  onClick: () => void;
}) {
  return (
    <button className="nav-item" onClick={onClick}>
      <span className="nav-label">{label}</span>
      {badge != null && badge > 0 && <span className="nav-badge">{badge}</span>}
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
