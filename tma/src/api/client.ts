/**
 * API-клиент для TMA Dashboard.
 *
 * Все запросы отправляются с заголовком:
 *   Authorization: tma {initData}
 *
 * В dev-режиме (initData пустой) заголовок не добавляется —
 * сервер в этом случае должен быть запущен с отключённой авторизацией
 * (переменная DEV_SKIP_AUTH=true), или использовать прокси в vite.config.ts.
 */

// Базовый URL API — в dev берётся из прокси Vite, в prod — из переменной среды
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(
  path: string,
  params?: Record<string, string | number>,
  initData?: string,
  method: "GET" | "POST" = "GET"
): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) =>
      url.searchParams.set(k, String(v))
    );
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (initData) {
    headers["Authorization"] = `tma ${initData}`;
  }

  const res = await fetch(url.toString(), { method, headers });

  if (!res.ok) {
    let errorMessage = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      errorMessage = body.error ?? errorMessage;
    } catch {
      // ignore JSON parse error
    }
    throw new ApiError(res.status, errorMessage);
  }

  return res.json() as Promise<T>;
}

// Фабрика API-методов, привязанных к initData
export function createApiClient(initData: string) {
  const get = <T>(path: string, params?: Record<string, string | number>) =>
    request<T>(path, params, initData, "GET");
  const post = <T>(path: string) =>
    request<T>(path, undefined, initData, "POST");

  return {
    /** GET /api/dashboard */
    getDashboard: () => get<DashboardResponse>("/api/dashboard"),

    /** GET /api/products */
    getProducts: (params: ProductsQuery) =>
      get<ProductsResponse>("/api/products", params as Record<string, string | number>),

    /** GET /api/products/{sku} */
    getProduct: (sku: string, params?: { period?: number; trend_days?: number }) =>
      get<ProductDetailResponse>(`/api/products/${encodeURIComponent(sku)}`, params as Record<string, number>),

    /** GET /api/ads/top-spenders */
    getTopSpenders: (limit = 20) =>
      get<AdsListResponse>("/api/ads/top-spenders", { limit }),

    /** GET /api/ads/top-performers */
    getTopPerformers: (limit = 20) =>
      get<AdsListResponse>("/api/ads/top-performers", { limit }),

    /** GET /api/ads/no-bonus */
    getNoBonusProducts: () =>
      get<AdsListResponse>("/api/ads/no-bonus"),

    /** GET /api/ads/most-clickable */
    getMostClickable: (limit = 20) =>
      get<AdsListResponse>("/api/ads/most-clickable", { limit }),

    /** GET /api/ads/wasted-budget */
    getWastedBudget: (threshold = 0) =>
      get<WastedBudgetResponse>("/api/ads/wasted-budget", { threshold }),

    /** GET /api/ads/trends/{sku} */
    getTrends: (sku: string, days = 30) =>
      get<TrendsResponse>(`/api/ads/trends/${encodeURIComponent(sku)}`, { days }),

    /** GET /api/summary/daily */
    getDailySummary: () => get<SummaryResponse>("/api/summary/daily"),

    /** GET /api/summary/weekly */
    getWeeklySummary: () => get<SummaryResponse>("/api/summary/weekly"),

    /** GET /api/summary/monthly */
    getMonthlySummary: () => get<SummaryResponse>("/api/summary/monthly"),

    /** POST /api/scrape/trigger */
    triggerScrape: () => post<{ status: string }>("/api/scrape/trigger"),

    /** GET /api/scrape/status */
    getScrapeStatus: () => get<ScrapeStatusResponse>("/api/scrape/status"),
  };
}

// ---- Типы ответов API ----

export interface DashboardAlert {
  type: string;
  message: string;
  count: number;
}

export interface TotalStats {
  total_spend: number;
  avg_cpc: number;
  avg_ctr: number;
  products_with_ads: number;
  products_without_bonuses: number;
}

export interface DashboardResponse {
  total_stats: TotalStats;
  today: SummaryResponse;
  alerts: DashboardAlert[];
}

export interface ProductItem {
  sku: string;
  title: string | null;
  spend: number;
  revenue: number;
  clicks: number;
  impressions: number;
  avg_ctr: number;
  avg_cpc: number;
  roi_percent: number | null;
}

export interface ProductsQuery {
  sort?: string;
  limit?: number;
  offset?: number;
  period?: number;
  q?: string;
}

export interface ProductsResponse {
  total: number;
  limit: number;
  offset: number;
  sort: string;
  period_days: number;
  items: ProductItem[];
}

export interface TrendPoint {
  day: string;
  impressions: number;
  clicks: number;
  spend: number;
  avg_ctr: number;
  avg_cpc: number;
}

export interface RoiData {
  sku: string;
  period_days: number;
  total_spend: number;
  total_revenue: number;
  roi_percent: number | null;
  orders: number;
}

export interface ProductDetailResponse {
  sku: string;
  title: string | null;
  url: string | null;
  roi: RoiData;
  roas: number | null;
  cpc_efficiency: Record<string, number | null>;
  trends: TrendPoint[];
  latest_data: Record<string, unknown> | null;
  period_days: number;
  trend_days: number;
}

export interface AdsItem {
  sku: string;
  title: string;
  total_spend?: number;
  total_clicks?: number;
  total_impressions?: number;
  avg_ctr?: number;
  avg_cpc?: number;
  roas?: number | null;
  roi_percent?: number | null;
  bonus_active?: boolean;
  bonus_percent?: number;
  clicks?: number;
  ctr?: number;
}

export interface AdsListResponse {
  items: AdsItem[];
  count: number;
}

export interface WastedBudgetItem extends AdsItem {
  total_spend: number;
  roi_percent: number;
  revenue?: number;
}

export interface WastedBudgetResponse {
  items: WastedBudgetItem[];
  count: number;
  threshold: number;
}

export interface TrendsResponse {
  sku: string;
  days: number;
  trends: TrendPoint[];
}

export interface SummaryResponse {
  period?: string;
  total_spend?: number;
  total_clicks?: number;
  total_impressions?: number;
  avg_ctr?: number;
  products_count?: number;
  [key: string]: unknown;
}

export interface ScrapeStatusResponse {
  status: string;
  log: {
    id: number;
    started_at: string;
    finished_at: string | null;
    products_scraped: number;
    errors: string | null;
    status: string;
  } | null;
}
