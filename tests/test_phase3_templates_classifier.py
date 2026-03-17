"""
Тесты для Фазы 3: Шаблоны сообщений и LLM-классификация

Покрывает:
- templates: шаблоны WARN1/WARN2, авто-ответы, рендеринг, подстановка переменных
- classifier: все 8 типов ответов, fallback, таймаут, парсинг JSON (mock OpenAI)
"""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from whatsapp.templates import (
    MessageTemplate,
    MessageCategory,
    ToneLevel,
    get_warn1_template,
    get_warn2_template,
    get_auto_reply_template,
    render_template,
    get_all_classifications,
    WARN1_TEMPLATES,
    WARN2_TEMPLATES,
    REPLY_DIDNT_KNOW,
    REPLY_PROVE_IT,
    REPLY_WONT_REMOVE,
    REPLY_ALREADY_REMOVED,
    REPLY_NEED_TIME,
    REPLY_AGGRESSIVE,
    REPLY_NEGOTIATE,
    REPLY_UNKNOWN,
)
from whatsapp.classifier import (
    MessageClassifier,
    ClassificationType,
    ClassificationResult,
)


# ========================================================================
# Тесты шаблонов (templates.py)
# ========================================================================


class TestTemplateStructure:
    """Проверка структуры и полноты пулов шаблонов."""

    def test_warn1_templates_count(self):
        """WARN1 должен иметь >= 5 вариантов."""
        assert len(WARN1_TEMPLATES) >= 5

    def test_warn2_templates_count(self):
        """WARN2 должен иметь >= 5 вариантов."""
        assert len(WARN2_TEMPLATES) >= 5

    def test_reply_pools_not_empty(self):
        """Все пулы авто-ответов непустые."""
        pools = [
            REPLY_DIDNT_KNOW, REPLY_PROVE_IT, REPLY_WONT_REMOVE,
            REPLY_ALREADY_REMOVED, REPLY_NEED_TIME, REPLY_AGGRESSIVE,
            REPLY_NEGOTIATE, REPLY_UNKNOWN,
        ]
        for pool in pools:
            assert len(pool) >= 2, f"Пул {pool[0].code} имеет менее 2 шаблонов"

    def test_all_classifications_list(self):
        """get_all_classifications() возвращает все 8 типов."""
        classifications = get_all_classifications()
        assert len(classifications) == 8
        expected = {
            "DIDNT_KNOW", "PROVE_IT", "WONT_REMOVE", "ALREADY_REMOVED",
            "NEED_TIME", "AGGRESSIVE", "NEGOTIATE", "UNKNOWN",
        }
        assert set(classifications) == expected

    def test_warn1_all_have_correct_category(self):
        for t in WARN1_TEMPLATES:
            assert t.category == MessageCategory.WARN1
            assert t.tone == ToneLevel.SOFT

    def test_warn2_all_have_correct_category(self):
        for t in WARN2_TEMPLATES:
            assert t.category == MessageCategory.WARN2
            assert t.tone == ToneLevel.FIRM

    def test_all_templates_have_unique_codes(self):
        """Все коды шаблонов уникальны."""
        all_templates = (
            WARN1_TEMPLATES + WARN2_TEMPLATES
            + REPLY_DIDNT_KNOW + REPLY_PROVE_IT + REPLY_WONT_REMOVE
            + REPLY_ALREADY_REMOVED + REPLY_NEED_TIME + REPLY_AGGRESSIVE
            + REPLY_NEGOTIATE + REPLY_UNKNOWN
        )
        codes = [t.code for t in all_templates]
        assert len(codes) == len(set(codes)), "Есть дублирующиеся коды шаблонов"

    def test_all_templates_have_text(self):
        """Все шаблоны содержат непустой текст."""
        all_templates = (
            WARN1_TEMPLATES + WARN2_TEMPLATES
            + REPLY_DIDNT_KNOW + REPLY_PROVE_IT + REPLY_WONT_REMOVE
            + REPLY_ALREADY_REMOVED + REPLY_NEED_TIME + REPLY_AGGRESSIVE
            + REPLY_NEGOTIATE + REPLY_UNKNOWN
        )
        for t in all_templates:
            assert t.text.strip(), f"Пустой текст у шаблона {t.code}"


class TestGetTemplates:
    """Тесты получения случайных шаблонов."""

    def test_get_warn1_returns_template(self):
        t = get_warn1_template()
        assert isinstance(t, MessageTemplate)
        assert t.category == MessageCategory.WARN1

    def test_get_warn2_returns_template(self):
        t = get_warn2_template()
        assert isinstance(t, MessageTemplate)
        assert t.category == MessageCategory.WARN2

    def test_get_auto_reply_known_type(self):
        for cls_type in get_all_classifications():
            t = get_auto_reply_template(cls_type)
            assert isinstance(t, MessageTemplate)
            assert t.category == MessageCategory.AUTO_REPLY

    def test_get_auto_reply_unknown_type_fallback(self):
        """Неизвестный тип → шаблон UNKNOWN."""
        t = get_auto_reply_template("SOME_NONEXISTENT_TYPE")
        assert isinstance(t, MessageTemplate)
        assert t.code.startswith("REPLY_UNKNOWN")


class TestRenderTemplate:
    """Тесты рендеринга шаблонов с подстановкой переменных."""

    def test_basic_substitution(self):
        template = MessageTemplate(
            code="TEST_01",
            category=MessageCategory.AUTO_REPLY,
            tone=ToneLevel.SOFT,
            text="Магазин: {shop_name}, товары: {product_links}",
        )
        result = render_template(template, {
            "shop_name": "ТестМагазин",
            "product_links": "https://kaspi.kz/item1",
        })
        assert result == "Магазин: ТестМагазин, товары: https://kaspi.kz/item1"

    def test_multiple_same_placeholder(self):
        template = MessageTemplate(
            code="TEST_02",
            category=MessageCategory.AUTO_REPLY,
            tone=ToneLevel.SOFT,
            text="{shop_name} — это {shop_name}",
        )
        result = render_template(template, {"shop_name": "МойМагазин"})
        assert result == "МойМагазин — это МойМагазин"

    def test_missing_placeholder_kept(self):
        """Переменная не найдена в context → плейсхолдер остаётся."""
        template = MessageTemplate(
            code="TEST_03",
            category=MessageCategory.AUTO_REPLY,
            tone=ToneLevel.SOFT,
            text="Дедлайн: {deadline}",
        )
        result = render_template(template, {})
        assert result == "Дедлайн: {deadline}"

    def test_warn1_render_with_all_vars(self):
        """Рендеринг реального WARN1 с полным контекстом."""
        t = WARN1_TEMPLATES[0]
        result = render_template(t, {
            "shop_name": "NAVIEN ЦЕНТР",
            "product_links": "— https://kaspi.kz/item/123\n— https://kaspi.kz/item/456",
            "our_company": "PKS Ltd",
        })
        assert "NAVIEN ЦЕНТР" in result
        assert "kaspi.kz/item/123" in result
        assert "PKS Ltd" in result

    def test_warn2_render_with_warn1_date(self):
        """Рендеринг WARN2 с датой первого предупреждения."""
        t = WARN2_TEMPLATES[2]  # шаблон с {warn1_date}
        result = render_template(t, {
            "shop_name": "ТестМагазин",
            "product_links": "— https://kaspi.kz/item/789",
            "our_company": "PKS Ltd",
            "warn1_date": "15.03.2026",
        })
        assert "15.03.2026" in result
        assert "ТестМагазин" in result


# ========================================================================
# Тесты классификатора (classifier.py)
# ========================================================================


def _make_llm_response(type_val: str, confidence: float) -> str:
    """Создать JSON-ответ LLM."""
    return json.dumps({"type": type_val, "confidence": confidence})


def _mock_completion(content: str):
    """Создать mock объект OpenAI completion."""
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return mock_completion


class TestClassificationType:
    """Тесты enum ClassificationType."""

    def test_all_types_exist(self):
        expected = {
            "DIDNT_KNOW", "PROVE_IT", "WONT_REMOVE", "ALREADY_REMOVED",
            "NEED_TIME", "AGGRESSIVE", "NEGOTIATE", "UNKNOWN",
        }
        actual = {t.value for t in ClassificationType}
        assert actual == expected

    def test_string_value(self):
        assert ClassificationType.DIDNT_KNOW == "DIDNT_KNOW"
        assert ClassificationType.AGGRESSIVE.value == "AGGRESSIVE"


class TestClassifierParseResponse:
    """Тесты парсинга JSON-ответа LLM (без вызова API)."""

    def test_valid_response(self):
        content = _make_llm_response("DIDNT_KNOW", 0.95)
        result = MessageClassifier._parse_response(content)
        assert result.classification == ClassificationType.DIDNT_KNOW
        assert result.confidence == 0.95

    def test_all_valid_types(self):
        for type_val in ClassificationType:
            content = _make_llm_response(type_val.value, 0.8)
            result = MessageClassifier._parse_response(content)
            assert result.classification == type_val

    def test_unknown_type_fallback(self):
        content = _make_llm_response("SOME_UNKNOWN", 0.9)
        result = MessageClassifier._parse_response(content)
        assert result.classification == ClassificationType.UNKNOWN

    def test_invalid_json(self):
        result = MessageClassifier._parse_response("not json at all")
        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    def test_confidence_clamped_above_1(self):
        content = _make_llm_response("PROVE_IT", 1.5)
        result = MessageClassifier._parse_response(content)
        assert result.confidence == 1.0

    def test_confidence_clamped_below_0(self):
        content = _make_llm_response("PROVE_IT", -0.5)
        result = MessageClassifier._parse_response(content)
        assert result.confidence == 0.0

    def test_missing_confidence(self):
        content = json.dumps({"type": "AGGRESSIVE"})
        result = MessageClassifier._parse_response(content)
        assert result.classification == ClassificationType.AGGRESSIVE
        assert result.confidence == 0.0

    def test_missing_type(self):
        content = json.dumps({"confidence": 0.9})
        result = MessageClassifier._parse_response(content)
        assert result.classification == ClassificationType.UNKNOWN

    def test_empty_json_object(self):
        result = MessageClassifier._parse_response("{}")
        assert result.classification == ClassificationType.UNKNOWN


@pytest.mark.asyncio
class TestClassifierClassify:
    """Тесты метода classify() с mock OpenAI API."""

    async def test_successful_classification(self):
        classifier = MessageClassifier(api_key="test-key")
        mock_resp = _mock_completion(_make_llm_response("DIDNT_KNOW", 0.92))

        with patch.object(
            classifier._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classifier.classify("Я не знал что так нельзя")

        assert result.classification == ClassificationType.DIDNT_KNOW
        assert result.confidence == 0.92

    async def test_aggressive_message(self):
        classifier = MessageClassifier(api_key="test-key")
        mock_resp = _mock_completion(_make_llm_response("AGGRESSIVE", 0.88))

        with patch.object(
            classifier._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classifier.classify("Иди нафиг со своими претензиями!")

        assert result.classification == ClassificationType.AGGRESSIVE

    async def test_empty_text_returns_unknown(self):
        classifier = MessageClassifier(api_key="test-key")
        result = await classifier.classify("")
        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    async def test_whitespace_only_returns_unknown(self):
        classifier = MessageClassifier(api_key="test-key")
        result = await classifier.classify("   ")
        assert result.classification == ClassificationType.UNKNOWN

    async def test_timeout_returns_unknown(self):
        """Таймаут OpenAI → UNKNOWN."""
        classifier = MessageClassifier(api_key="test-key", timeout=0.1)

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(1.0)
            return _mock_completion(_make_llm_response("DIDNT_KNOW", 0.9))

        with patch.object(
            classifier._client.chat.completions,
            "create",
            side_effect=slow_llm,
        ):
            result = await classifier.classify("Тестовое сообщение")

        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    async def test_api_error_returns_unknown(self):
        """Ошибка OpenAI API → UNKNOWN."""
        classifier = MessageClassifier(api_key="test-key")

        with patch.object(
            classifier._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            side_effect=Exception("API Error"),
        ):
            result = await classifier.classify("Тестовое сообщение")

        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    async def test_empty_llm_response_returns_unknown(self):
        """Пустой ответ LLM → UNKNOWN."""
        classifier = MessageClassifier(api_key="test-key")
        mock_msg = MagicMock()
        mock_msg.content = None
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        with patch.object(
            classifier._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_completion,
        ):
            result = await classifier.classify("Тест")

        assert result.classification == ClassificationType.UNKNOWN

    async def test_all_8_types_via_llm(self):
        """Проверка всех 8 типов через mock LLM."""
        classifier = MessageClassifier(api_key="test-key")

        for type_val in ClassificationType:
            mock_resp = _mock_completion(
                _make_llm_response(type_val.value, 0.85)
            )
            with patch.object(
                classifier._client.chat.completions,
                "create",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await classifier.classify(f"Тест для {type_val.value}")
                assert result.classification == type_val


# ========================================================================
# Интеграционные тесты: шаблоны + классификатор
# ========================================================================


class TestTemplateClassifierIntegration:
    """Проверка, что для каждого типа классификации есть авто-ответ."""

    def test_every_classification_has_reply(self):
        """Для всех 8 типов существуют шаблоны авто-ответов."""
        for type_val in ClassificationType:
            template = get_auto_reply_template(type_val.value)
            assert template is not None
            assert template.text.strip()

    def test_reply_renders_without_errors(self):
        """Авто-ответы можно отрендерить с типичным контекстом."""
        context = {
            "shop_name": "Тест Магазин",
            "product_links": "— https://kaspi.kz/shop/p/test-123",
            "our_company": "PKS Ltd",
            "detection_date": "15.03.2026",
            "deadline": "24 часа",
        }
        for type_val in ClassificationType:
            template = get_auto_reply_template(type_val.value)
            result = render_template(template, context)
            assert isinstance(result, str)
            assert len(result) > 0
