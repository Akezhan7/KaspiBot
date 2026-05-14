/**
 * ProductDetailPage — детальная карточка товара.
 *
 * Показывает 4 секции активности (внутренняя реклама / внешняя / бонус продавца /
 * бонус за отзыв) с цветовой индикацией:
 *   зелёный  — активна и реально работает (есть свежие списания)
 *   жёлтый   — запущена, но не списывается (вероятно цена конкурента ниже)
 *   серый    — не запущена
 *
 * ROI/ROAS не показываем — Kaspi не отдаёт revenue.
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
import type {
  ActivityStatus,
  AdsSection,
  BonusSection,
  ProductDetailResponse,
  ReportPeriod,
} from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { PRODUCTS_LIST_URL_STORAGE_KEY } from "./ProductsPage";
import "../styles/pages.css";

const REPORT_PERIOD_STORAGE_KEY = "kaspibot.reportPeriod";

function readReportPeriod(): ReportPeriod {
  try {
    const v = Number(localStorage.getItem(REPORT_PERIOD_STORAGE_KEY));
    return v === 30 ? 30 : 7;
  } catch {
    return 7;
  }
}

export default function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const navigate = useNavigate();
  const api = useApi();
  const { showBackButton } = useTelegram();

  const [data, setData] = useState<ProductDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // navigate(-1) ненадёжен в Telegram WebApp: история может быть пустой
    // (при прямом переходе на детальную страницу) или сбиваться при
    // обновлениях URL через `replace`. Восстанавливаем явный URL списка
    // из sessionStorage, который ProductsPage сохраняет на каждом
    // изменении фильтра/страницы.
    showBackButton(() => {
      let target = "/products";
      try {
        target = sessionStorage.getItem(PRODUCTS_LIST_URL_STORAGE_KEY) || target;
      } catch {
        // sessionStorage может быть недоступен — используем дефолт
      }
      navigate(target);
    });
  }, [showBackButton, navigate]);

  useEffect(() => {
    if (!api || !sku) return;
    setLoading(true);
    setError(null);
    const reportPeriod = readReportPeriod();
    api
      .getProduct(decodeURIComponent(sku), {
        period: 30,
        trend_days: 30,
        report_period: reportPeriod,
      })
      .then(setData)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [api, sku]);

  if (loading) return <div className="page-loader">Загрузка...</div>;
  if (error) return <div className="page-error">Ошибка: {error}</div>;
  if (!data) return null;

  const sections = data.sections;
  const latest = data.latest_data ?? {};

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

      {/* 4 секции активности */}
      {sections && (
        <div className="sections">
          <AdsSectionCard
            title="Реклама"
            section={sections.marketing}
          />
          <AdsSectionCard
            title="Внешняя реклама"
            section={sections.external_ads}
          />
          <BonusSectionCard
            title="Бонус продавца"
            section={sections.bonus_seller}
          />
          <BonusSectionCard
            title="Бонус за отзыв"
            section={sections.bonus_review}
          />
        </div>
      )}

      {/* Подробный бонус-бейдж (агрегированный по обоим источникам) */}
      {hasBonusData && (
        <div
          className={`bonus-badge ${bonusActive ? "bonus-active" : "bonus-inactive"}`}
        >
          {bonusActive
            ? `Любой бонус активен: ${bonusPercent ?? "—"}%`
            : "Бонусы не активны"}
          <span className="bonus-icon-wrap">
            {bonusActive ? <Gift size={14} /> : <CircleX size={14} />}
          </span>
        </div>
      )}

      {/* График динамики */}
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

const STATUS_LABEL: Record<ActivityStatus, string> = {
  active: "Активна",
  stale: "Не показывается",
  inactive: "Не запущена",
};

function AdsSectionCard({
  title,
  section,
}: {
  title: string;
  section: AdsSection;
}) {
  return (
    <div className={`section-card section-${section.activity}`}>
      <div className="section-card-header">
        <span className="section-card-title">{title}</span>
        <span className={`section-status section-status-${section.activity}`}>
          {STATUS_LABEL[section.activity]}
        </span>
      </div>
      {section.active && (
        <div className="section-card-body">
          {section.campaign_name && (
            <div className="section-card-row">
              <span className="section-card-label">Кампания</span>
              <span className="section-card-value">{section.campaign_name}</span>
            </div>
          )}
          <div className="section-card-row">
            <span className="section-card-label">Расход</span>
            <span className="section-card-value">{fmt(section.spend)} ₸</span>
          </div>
          <div className="section-card-row">
            <span className="section-card-label">CPC</span>
            <span className="section-card-value">
              {section.cpc > 0 ? `${section.cpc.toFixed(0)} ₸` : "—"}
            </span>
          </div>
          <div className="section-card-row">
            <span className="section-card-label">Показы / Клики</span>
            <span className="section-card-value">
              {fmt(section.impressions)} / {fmt(section.clicks)}
            </span>
          </div>
          {section.ctr > 0 && (
            <div className="section-card-row">
              <span className="section-card-label">CTR</span>
              <span className="section-card-value">{section.ctr.toFixed(2)}%</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BonusSectionCard({
  title,
  section,
}: {
  title: string;
  section: BonusSection;
}) {
  return (
    <div className={`section-card section-${section.activity}`}>
      <div className="section-card-header">
        <span className="section-card-title">{title}</span>
        <span className={`section-status section-status-${section.activity}`}>
          {section.active ? `${section.percent}%` : STATUS_LABEL[section.activity]}
        </span>
      </div>
      {section.campaign_name && (
        <div className="section-card-body">
          <div className="section-card-row">
            <span className="section-card-label">Акция</span>
            <span className="section-card-value">{section.campaign_name}</span>
          </div>
        </div>
      )}
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
