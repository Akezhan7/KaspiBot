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
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 5.0,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout,
        )

    async def classify(self, text: str) -> ClassificationResult:
        """
        Классифицировать входящее сообщение продавца.

        Жёсткий таймаут — если OpenAI не ответил за _timeout сек,
        возвращаем UNKNOWN с confidence=0.0.

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

        try:
            result = await asyncio.wait_for(
                self._call_llm(text.strip()),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                f"Classifier: таймаут OpenAI ({self._timeout}с) "
                f"для сообщения: {text[:50]}..."
            )
            return ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
            )
        except Exception as e:
            logger.error(
                f"Classifier: ошибка OpenAI API: {e}",
                exc_info=True,
            )
            return ClassificationResult(
                classification=ClassificationType.UNKNOWN,
                confidence=0.0,
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
