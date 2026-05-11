"""
LLM-классификация входящих сообщений от продавцов.

Использует gpt-4o-mini (OpenAI API) для определения типа ответа продавца.
Жёсткий таймаут 5 сек — webhook не должен висеть.

Типы ответов:
- DIDNT_KNOW   — «Я не знал»
- PROVE_IT     — «Докажите»
- WONT_REMOVE  — «Не сниму»
- ALREADY_REMOVED — «Уже снял»
- NEED_TIME    — «Дайте время»
- AGGRESSIVE   — Агрессия
- NEGOTIATE    — Попытка договориться
- UNKNOWN      — Не удалось классифицировать

При сбое OpenAI (таймаут, 401, прочее) или возврате UNKNOWN дополнительно
применяется keyword-fallback по простым регэкспам — это критически важно
для типа ALREADY_REMOVED, т.к. без него движок воронки не запускает
проверку отсоединения и продолжает рассылку.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Минимальное число подряд-ошибок OpenAI, после которого вызывается коллбэк
# для уведомления администраторов (защита от «тихих» 401/таймаутов).
_DEFAULT_FAILURE_NOTIFY_THRESHOLD = 3


class ClassificationType(str, Enum):
    """Типы ответов продавцов."""
    DIDNT_KNOW = "DIDNT_KNOW"
    PROVE_IT = "PROVE_IT"
    WONT_REMOVE = "WONT_REMOVE"
    ALREADY_REMOVED = "ALREADY_REMOVED"
    NEED_TIME = "NEED_TIME"
    AGGRESSIVE = "AGGRESSIVE"
    NEGOTIATE = "NEGOTIATE"
    UNKNOWN = "UNKNOWN"


# Все допустимые значения для валидации ответа LLM
_VALID_TYPES = {t.value for t in ClassificationType}


@dataclass(frozen=True)
class ClassificationResult:
    """Результат классификации сообщения."""
    classification: ClassificationType
    confidence: float


# ---------------------------------------------------------------------------
# Keyword-fallback (используется при сбое OpenAI или возврате UNKNOWN).
#
# Покрываем только два самых критичных по последствиям типа:
#   ALREADY_REMOVED — иначе бот продолжает спамить уже открепившегося продавца
#   WONT_REMOVE     — иначе агрессивный отказ маркируется как UNKNOWN
# Остальные типы (NEED_TIME, NEGOTIATE и т.п.) при отказе LLM продолжают
# падать в UNKNOWN — поведение шаблонов на это рассчитано.
# Все паттерны matched через re.search над text.lower() с re.IGNORECASE,
# поэтому регистр и порядок слов гибкие. Используется \b, чтобы избежать
# срабатываний внутри слов («сняла» ≠ «снял»).
# ---------------------------------------------------------------------------
_FALLBACK_PATTERNS: dict = {
    ClassificationType.ALREADY_REMOVED: [
        r"\bуже\s+(?:убра|сня|открепи|отвяза|удали)",
        r"\b(?:давно|вчера|сегодня)\s+(?:убра|сня|открепи|отвяза|удали)",
        r"\b(?:убрал|снял|открепил|отвязал|удалил)(?:и|ся|ась|и)?(?:\s+(?:уже|давно|с\s+карточ))",
        r"\bоткрепил(?:ся|ись|и)?\b",
        r"\bотвязал(?:ся|ись|и)?\b",
        r"\b(?:нас|меня|нашего\s+магазин)\s+(?:уже\s+)?(?:нет|нету|там\s+нет)",
        r"\bне\s+(?:подключен|подключены|прилеплен|прилеплены)\b",
        r"\bалып\s+таста",
        r"\bөшір(?:ді|дім|дік)",
        r"\bшықт(?:ы|ық|ым)\b",
    ],
    ClassificationType.WONT_REMOVE: [
        r"\bне\s+(?:буду|будем|собираюсь|собираемся|стану|станем)\s+(?:убир|снима|открепл|отвяз)",
        r"\bне\s+(?:уберу|уберём|сниму|снимем|откреплю|откре́пим|отвяжу|отвяжем)\b",
        r"\bничего\s+(?:не\s+)?(?:убир|снима|открепл)",
        r"\bоткаж(?:усь|емся|ываюсь|ываемся)\b",
        r"\bне\s+согласен\b",
        r"\bне\s+согласны\b",
    ],
}


def _keyword_fallback(text: str) -> Optional[ClassificationResult]:
    """
    Попытаться классифицировать сообщение по ключевым словам.

    Возвращает None если ни один паттерн не сработал.
    Confidence выставляется в 0.55 — достаточно для срабатывания
    логики обработки, но видно, что это не точная LLM-классификация.
    """
    if not text:
        return None
    lower = text.lower()
    for clf_type, patterns in _FALLBACK_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lower, re.IGNORECASE | re.UNICODE):
                return ClassificationResult(
                    classification=clf_type,
                    confidence=0.55,
                )
    return None


# Системный промпт для LLM
_SYSTEM_PROMPT = """Ты классифицируешь сообщения продавцов на Kaspi.kz, которые получили предупреждение о необходимости убрать свои предложения с чужих товарных карточек.

Определи тип ответа продавца. Верни JSON:
{"type": "<тип>", "confidence": <число от 0.0 до 1.0>}

Допустимые типы:
- DIDNT_KNOW — продавец говорит, что не знал о проблеме, готов исправить
- PROVE_IT — продавец требует доказательства, сомневается в правомерности претензии
- WONT_REMOVE — продавец открыто отказывается убирать предложения
- ALREADY_REMOVED — продавец утверждает, что уже убрал предложения
- NEED_TIME — продавец просит дать время на решение вопроса
- AGGRESSIVE — агрессивная реакция, угрозы, оскорбления
- NEGOTIATE — продавец пытается договориться, предлагает условия
- UNKNOWN — невозможно определить тип (слишком короткое, нерелевантное, неясное)

Правила:
- Сообщения могут быть на русском, казахском или смеси языков
- Если сообщение содержит элементы нескольких типов — выбери преобладающий
- Для коротких неоднозначных сообщений (типа "ок", "ладно") — DIDNT_KNOW с низкой confidence
- Для нерелевантных сообщений (реклама, спам) — UNKNOWN
- confidence > 0.8 — уверенная классификация, 0.5-0.8 — средняя, < 0.5 — низкая

Отвечай ТОЛЬКО JSON, без дополнительного текста."""


class MessageClassifier:
    """
    Классификатор входящих сообщений через OpenAI API.

    Использует gpt-4o-mini для классификации ответов продавцов
    на Kaspi.kz в одну из 8 категорий.

    Поверх LLM работает keyword-fallback: если OpenAI вернул ошибку
    (например, протух API-ключ) или UNKNOWN, мы прогоняем сообщение через
    регэкспы и пытаемся определить хотя бы ALREADY_REMOVED / WONT_REMOVE.
    Это критично — иначе движок воронки не получает сигнал «уже снял»
    и продолжает рассылку.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 5.0,
        on_failure: Optional[Callable[[str], Awaitable[None]]] = None,
        failure_notify_threshold: int = _DEFAULT_FAILURE_NOTIFY_THRESHOLD,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout,
        )
        # Уведомление о подряд-ошибках OpenAI: если api-ключ протух или сервис
        # упал, без явного сигнала это легко не заметить часами.
        self._on_failure = on_failure
        self._failure_notify_threshold = max(1, failure_notify_threshold)
        self._consecutive_failures = 0
        self._failure_notified = False

    async def classify(self, text: str) -> ClassificationResult:
        """
        Классифицировать входящее сообщение продавца.

        Жёсткий таймаут — если OpenAI не ответил за _timeout сек,
        пробуем keyword-fallback, в крайнем случае возвращаем UNKNOWN.

        Args:
            text: текст сообщения от продавца

        Returns:
            ClassificationResult с типом и уверенностью
        """
        if not text or not text.strip():
            return ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )

        stripped = text.strip()
        try:
            result = await asyncio.wait_for(
                self._call_llm(stripped),
                timeout=self._timeout,
            )
            self._on_llm_success()
            # Если LLM не определилась — пробуем fallback, чтобы поймать
            # хотя бы критичные типы (ALREADY_REMOVED и т.п.).
            if result.classification == ClassificationType.UNKNOWN:
                fallback = _keyword_fallback(stripped)
                if fallback is not None:
                    logger.info(
                        "Classifier: LLM=UNKNOWN, fallback=%s "
                        "(сообщение: %s...)",
                        fallback.classification.value,
                        stripped[:50],
                    )
                    return fallback
            return result
        except asyncio.TimeoutError:
            logger.warning(
                f"Classifier: таймаут OpenAI ({self._timeout}с) "
                f"для сообщения: {stripped[:50]}..."
            )
            await self._on_llm_failure(f"таймаут OpenAI {self._timeout}с")
            return _keyword_fallback(stripped) or ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )
        except Exception as e:
            logger.error(
                f"Classifier: ошибка OpenAI API: {e}",
                exc_info=True,
            )
            await self._on_llm_failure(str(e))
            return _keyword_fallback(stripped) or ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )

    def _on_llm_success(self) -> None:
        """Сбросить счётчик подряд-ошибок при удачном вызове."""
        if self._consecutive_failures or self._failure_notified:
            logger.info(
                "Classifier: OpenAI восстановлен после %d ошибок подряд",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._failure_notified = False

    async def _on_llm_failure(self, reason: str) -> None:
        """Учесть ошибку OpenAI и при превышении порога уведомить админов."""
        self._consecutive_failures += 1
        if (
            self._on_failure is not None
            and not self._failure_notified
            and self._consecutive_failures >= self._failure_notify_threshold
        ):
            self._failure_notified = True
            try:
                await self._on_failure(
                    f"OpenAI classifier недоступен "
                    f"({self._consecutive_failures} ошибок подряд): {reason}"
                )
            except Exception as notify_err:
                logger.error(
                    f"Classifier: не удалось доставить уведомление "
                    f"об ошибке OpenAI: {notify_err}"
                )

    async def _call_llm(self, text: str) -> ClassificationResult:
        """Выполнить запрос к OpenAI API и распарсить ответ."""
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=100,
        )

        content = completion.choices[0].message.content
        if not content:
            logger.warning("Classifier: пустой ответ от LLM")
            return ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )

        return self._parse_response(content)

    @staticmethod
    def _parse_response(content: str) -> ClassificationResult:
        """Распарсить JSON-ответ от LLM."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"Classifier: невалидный JSON от LLM: {content[:100]}")
            return ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )

        raw_type = data.get("type", "UNKNOWN")
        raw_confidence = data.get("confidence", 0.0)

        # Валидация типа
        if raw_type not in _VALID_TYPES:
            logger.warning(f"Classifier: неизвестный тип от LLM: {raw_type}")
            classification = ClassificationType.UNKNOWN
        else:
            classification = ClassificationType(raw_type)

        # Валидация confidence
        try:
            confidence = float(raw_confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        return ClassificationResult(
            classification=classification,
            confidence=confidence,
        )

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.close()
