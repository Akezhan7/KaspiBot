/**
 * useApi — хук для получения экземпляра API-клиента с initData из Telegram.
 * Возвращает null если клиент ещё не готов.
 */
import { useMemo } from "react";
import { createApiClient } from "../api/client";
import { useTelegram } from "./useTelegram";

export function useApi() {
  const { initData } = useTelegram();
  return useMemo(() => createApiClient(initData), [initData]);
}
