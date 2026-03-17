"""
WhatsApp клиент — абстрактный базовый класс + реализация Green API

Green API endpoints:
- POST /waInstance{id}/sendMessage/{token} — отправка текста
- POST /waInstance{id}/checkWhatsapp/{token} — проверка номера
- POST /waInstance{id}/readChat/{token} — пометить прочитанным
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional

import httpx

from .phone_utils import phone_to_chat_id, normalize_phone

logger = logging.getLogger(__name__)

# Таймаут для HTTP-запросов к Green API (секунды)
_REQUEST_TIMEOUT = 10.0

# Retry конфигурация
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 3, 5]


class WhatsAppClientBase(ABC):
    """Абстрактный базовый класс для WhatsApp-клиентов"""

    @abstractmethod
    async def send_text(self, to_phone: str, text: str) -> Dict:
        """
        Отправить текстовое сообщение.

        Args:
            to_phone: Номер телефона получателя (любой формат)
            text: Текст сообщения (до 20000 символов)

        Returns:
            Dict с ключом 'idMessage' при успехе
        """
        ...

    @abstractmethod
    async def check_phone_exists(self, phone: str) -> bool:
        """
        Проверить наличие WhatsApp на номере.

        Args:
            phone: Номер телефона (любой формат)

        Returns:
            True если WhatsApp зарегистрирован на номере
        """
        ...

    @abstractmethod
    async def mark_as_read(self, chat_id: str) -> bool:
        """
        Пометить чат как прочитанный.

        Args:
            chat_id: ID чата в формате Green API (77017545109@c.us)

        Returns:
            True при успехе
        """
        ...


class GreenAPIClient(WhatsAppClientBase):
    """
    Реализация WhatsApp-клиента через Green API.

    Green API позволяет отправлять любой текст без модерации шаблонов.
    Работает как WhatsApp Web — привязка номера через QR-код.
    """

    def __init__(self, api_url: str, instance_id: str, token: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._instance_id = instance_id
        self._token = token

        if not instance_id or not token:
            logger.warning(
                "GreenAPIClient создан без credentials — "
                "WhatsApp-функции будут недоступны"
            )

    def _build_url(self, method: str) -> str:
        """Построить URL эндпоинта Green API."""
        return (
            f"{self._api_url}/waInstance{self._instance_id}"
            f"/{method}/{self._token}"
        )

    async def _request(
        self,
        method: str,
        payload: Dict,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> Dict:
        """
        Базовый HTTP-запрос к Green API с retry.

        Args:
            method: Имя метода Green API (sendMessage, checkWhatsapp, readChat)
            payload: Тело запроса
            timeout: Таймаут в секундах

        Returns:
            Распарсенный JSON-ответ

        Raises:
            httpx.HTTPStatusError: при ошибке HTTP (4xx, 5xx)
            httpx.TimeoutException: при превышении таймаута
        """
        url = self._build_url(method)
        last_error: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    f"Green API таймаут ({method}), "
                    f"попытка {attempt + 1}/{_MAX_RETRIES}: {e}"
                )

            except httpx.HTTPStatusError as e:
                # 4xx ошибки (кроме 429) — не ретраим, это ошибка в запросе
                if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    logger.error(
                        f"Green API ошибка клиента ({method}): "
                        f"{e.response.status_code} — {e.response.text}"
                    )
                    raise

                last_error = e
                logger.warning(
                    f"Green API ошибка сервера ({method}), "
                    f"попытка {attempt + 1}/{_MAX_RETRIES}: "
                    f"{e.response.status_code}"
                )

            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    f"Green API ошибка соединения ({method}), "
                    f"попытка {attempt + 1}/{_MAX_RETRIES}: {e}"
                )

            # Задержка перед ретраем
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                await asyncio.sleep(delay)

        logger.error(
            f"Green API: все {_MAX_RETRIES} попытки исчерпаны для {method}"
        )
        raise last_error  # type: ignore[misc]

    async def send_text(self, to_phone: str, text: str) -> Dict:
        """
        Отправить текстовое сообщение через Green API.

        Формат: POST /waInstance{id}/sendMessage/{token}
        Тело: {"chatId": "77017545109@c.us", "message": "текст"}
        Ответ: {"idMessage": "3EB0C767D097B7C7C030"}
        """
        chat_id = phone_to_chat_id(to_phone)

        if len(text) > 20000:
            raise ValueError(
                f"Текст сообщения слишком длинный: {len(text)} символов (макс 20000)"
            )

        payload = {
            "chatId": chat_id,
            "message": text,
        }

        result = await self._request("sendMessage", payload)

        logger.info(
            f"WhatsApp сообщение отправлено: "
            f"chat={chat_id[:7]}***, msg_id={result.get('idMessage', '?')}"
        )
        return result

    async def check_phone_exists(self, phone: str) -> bool:
        """
        Проверить наличие WhatsApp на номере.

        Формат: POST /waInstance{id}/checkWhatsapp/{token}
        Тело: {"phoneNumber": 77017545109}  (integer!)
        Ответ: {"existsWhatsapp": true}
        """
        normalized = normalize_phone(phone)
        if not normalized:
            logger.warning(f"Невалидный номер для проверки WhatsApp: {phone}")
            return False

        # Green API ожидает phoneNumber как integer
        payload = {
            "phoneNumber": int(normalized),
        }

        try:
            result = await self._request("checkWhatsapp", payload)
            exists = result.get("existsWhatsapp", False)

            logger.debug(
                f"WhatsApp проверка: {normalized[:4]}*** → "
                f"{'есть' if exists else 'нет'}"
            )
            return exists

        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(
                f"Ошибка проверки WhatsApp для {normalized[:4]}***: {e}"
            )
            return False

    async def mark_as_read(self, chat_id: str) -> bool:
        """
        Пометить все сообщения в чате как прочитанные.

        Формат: POST /waInstance{id}/readChat/{token}
        Тело: {"chatId": "77017545109@c.us"}
        Ответ: {"setRead": true}
        """
        payload = {
            "chatId": chat_id,
        }

        try:
            result = await self._request("readChat", payload)
            return result.get("setRead", False)

        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Ошибка mark_as_read для {chat_id[:7]}***: {e}")
            return False
