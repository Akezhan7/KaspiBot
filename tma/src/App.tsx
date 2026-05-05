/**
 * App — корневой компонент TMA.
 * Инициализирует Telegram WebApp SDK, применяет тему, настраивает маршрутизацию.
 */
import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { useTelegram } from "./hooks/useTelegram";
import DashboardPage from "./pages/DashboardPage";
import ProductsPage from "./pages/ProductsPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import WastedBudgetPage from "./pages/WastedBudgetPage";
import NoBonusPage from "./pages/NoBonusPage";
import MostClickablePage from "./pages/MostClickablePage";
import TopPerformersPage from "./pages/TopPerformersPage";

// Определяем base path для React Router (при раздаче из /tma/* — basePath = "/tma")
const BASE_PATH = import.meta.env.BASE_URL ?? "/";

function App() {
  const { ready, expand, colorScheme, themeParams } = useTelegram();

  useEffect(() => {
    // Сигнализируем Telegram, что TMA готово к отображению
    ready();
    // Разворачиваем на полный экран
    expand();
  }, [ready, expand]);

  useEffect(() => {
    // Применяем CSS-переменные от Telegram (дополняем/переопределяем)
    if (themeParams) {
      const root = document.documentElement;
      const pairs: [string, string | undefined][] = [
        ["--tg-theme-bg-color", themeParams.bg_color],
        ["--tg-theme-text-color", themeParams.text_color],
        ["--tg-theme-hint-color", themeParams.hint_color],
        ["--tg-theme-link-color", themeParams.link_color],
        ["--tg-theme-button-color", themeParams.button_color],
        ["--tg-theme-button-text-color", themeParams.button_text_color],
        ["--tg-theme-secondary-bg-color", themeParams.secondary_bg_color],
      ];
      pairs.forEach(([prop, val]) => {
        if (val) root.style.setProperty(prop, val);
      });
    }

    // Устанавливаем класс темы на body
    document.body.dataset.theme = colorScheme;
  }, [themeParams, colorScheme]);

  return (
    <BrowserRouter basename={BASE_PATH}>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/products/:sku" element={<ProductDetailPage />} />
        <Route path="/wasted-budget" element={<WastedBudgetPage />} />
        <Route path="/no-bonus" element={<NoBonusPage />} />
        <Route path="/most-clickable" element={<MostClickablePage />} />
        <Route path="/top-performers" element={<TopPerformersPage />} />
        {/* Любой неизвестный маршрут → Dashboard */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}


export default App;
