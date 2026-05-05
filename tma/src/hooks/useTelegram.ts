/**
 * useTelegram — хук для работы с Telegram WebApp SDK.
 *
 * Предоставляет:
 *   - initData: строка для авторизации API-запросов
 *   - user: данные текущего пользователя
 *   - colorScheme: 'light' | 'dark'
 *   - themeParams: объект с цветами темы Telegram
 *   - ready(): вызвать после монтирования UI
 *   - expand(): развернуть приложение на полный экран
 *   - showBackButton(cb): показать кнопку «Назад» с колбэком
 *   - hideBackButton(): скрыть кнопку «Назад»
 *
 * В dev-режиме (без window.Telegram) возвращает stub-данные.
 */

export interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

export interface TelegramTheme {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
}

export interface UseTelegramResult {
  initData: string;
  user: TelegramUser | null;
  colorScheme: "light" | "dark";
  themeParams: TelegramTheme;
  ready: () => void;
  expand: () => void;
  showBackButton: (onClick: () => void) => void;
  hideBackButton: () => void;
  isAvailable: boolean;
}

// Тип для window.Telegram.WebApp, используем any для совместимости
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type TgWebApp = any;

function getTgWebApp(): TgWebApp | null {
  if (
    typeof window !== "undefined" &&
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).Telegram?.WebApp
  ) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (window as any).Telegram.WebApp;
  }
  return null;
}

// Singleton: раз распарсили — держим в памяти, не пересоздаём на каждом ре-рендере
let _cachedResult: UseTelegramResult | null = null;
let _backButtonCallback: (() => void) | null = null;

export function useTelegram(): UseTelegramResult {
  if (_cachedResult) return _cachedResult;

  const tg = getTgWebApp();

  if (!tg) {
    // Stub для локальной разработки без Telegram
    _cachedResult = {
      initData: "",
      user: { id: 0, first_name: "Dev", username: "devuser" },
      colorScheme: "light",
      themeParams: {},
      ready: () => {},
      expand: () => {},
      showBackButton: () => {},
      hideBackButton: () => {},
      isAvailable: false,
    };
    return _cachedResult;
  }

  const ready = () => tg.ready();
  const expand = () => tg.expand();

  const showBackButton = (onClick: () => void) => {
    // Снять предыдущий обработчик перед назначением нового
    if (_backButtonCallback) {
      tg.BackButton.offClick(_backButtonCallback);
    }
    _backButtonCallback = onClick;
    tg.BackButton.onClick(_backButtonCallback);
    tg.BackButton.show();
  };

  const hideBackButton = () => {
    if (_backButtonCallback) {
      tg.BackButton.offClick(_backButtonCallback);
      _backButtonCallback = null;
    }
    tg.BackButton.hide();
  };

  _cachedResult = {
    initData: tg.initData ?? "",
    user: tg.initDataUnsafe?.user ?? null,
    colorScheme: tg.colorScheme ?? "light",
    themeParams: tg.themeParams ?? {},
    ready,
    expand,
    showBackButton,
    hideBackButton,
    isAvailable: true,
  };

  return _cachedResult;
}
