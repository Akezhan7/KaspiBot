"""
Шаблоны сообщений WhatsApp для воронки работы с продавцами.

Структура:
- MessageTemplate — dataclass с текстом и метаданными
- Пулы шаблонов для WARN1, WARN2, авто-ответов по классификации
- render_template() — подстановка переменных
- Рандомный выбор из пула для разнообразия
"""
import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

logger = logging.getLogger(__name__)


class MessageCategory(str, Enum):
    """Категория шаблона сообщения."""
    WARN1 = "WARN1"
    WARN2 = "WARN2"
    AUTO_REPLY = "AUTO_REPLY"
    CLARIFICATION = "CLARIFICATION"


class ToneLevel(str, Enum):
    """Тон сообщения."""
    SOFT = "soft"
    FIRM = "firm"
    LEGAL = "legal"


@dataclass(frozen=True)
class MessageTemplate:
    """Шаблон сообщения WhatsApp."""
    code: str            # "WARN1_SOFT_01", "REPLY_DIDNT_KNOW_01"
    category: MessageCategory
    text: str            # Текст с плейсхолдерами: {shop_name}, {product_links}, {deadline}
    tone: ToneLevel


# ---------------------------------------------------------------------------
# WARN1 — первое предупреждение (мягкий тон)
# ---------------------------------------------------------------------------
WARN1_TEMPLATES: List[MessageTemplate] = [
    MessageTemplate(
        code="WARN1_SOFT_01",
        category=MessageCategory.WARN1,
        tone=ToneLevel.SOFT,
        text=(
            "Здравствуйте!\n\n"
            "Мы обнаружили, что магазин «{shop_name}» размещает свои предложения "
            "на карточках наших товаров на Kaspi.kz:\n\n"
            "{product_links}\n\n"
            "Просим вас убрать свои предложения с указанных карточек "
            "в течение 24 часов.\n\n"
            "Если это произошло по ошибке — пожалуйста, сообщите нам, "
            "мы с радостью разберёмся в ситуации.\n\n"
            "С уважением, {our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN1_SOFT_02",
        category=MessageCategory.WARN1,
        tone=ToneLevel.SOFT,
        text=(
            "Добрый день!\n\n"
            "Обращаемся к вам как к владельцу магазина «{shop_name}» на Kaspi.kz.\n\n"
            "Мы зафиксировали, что ваш магазин присоединился к карточкам "
            "наших товаров:\n\n"
            "{product_links}\n\n"
            "Данные товарные карточки принадлежат нашей компании. "
            "Просим вас отсоединиться от них в течение 24 часов.\n\n"
            "Будем рады решить вопрос мирно. Спасибо за понимание!\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN1_SOFT_03",
        category=MessageCategory.WARN1,
        tone=ToneLevel.SOFT,
        text=(
            "Здравствуйте, «{shop_name}»!\n\n"
            "Мы заметили ваши предложения на наших товарных карточках Kaspi.kz:\n\n"
            "{product_links}\n\n"
            "Эти карточки были созданы и оформлены нашей компанией. "
            "Пожалуйста, уберите свои предложения в течение 24 часов.\n\n"
            "Если у вас есть вопросы — напишите нам, обсудим.\n\n"
            "С уважением, {our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN1_SOFT_04",
        category=MessageCategory.WARN1,
        tone=ToneLevel.SOFT,
        text=(
            "Добрый день!\n\n"
            "Магазин «{shop_name}» разместил предложения на карточках товаров, "
            "которые принадлежат нашей компании:\n\n"
            "{product_links}\n\n"
            "Мы просим вас отсоединиться от данных карточек в ближайшие 24 часа.\n\n"
            "Надеемся на ваше понимание и сотрудничество.\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN1_SOFT_05",
        category=MessageCategory.WARN1,
        tone=ToneLevel.SOFT,
        text=(
            "Приветствуем!\n\n"
            "Мы, компания {our_company}, обнаружили, что магазин «{shop_name}» "
            "присоединился к нашим товарным карточкам на Kaspi.kz:\n\n"
            "{product_links}\n\n"
            "Просим вас удалить свои предложения с этих карточек в течение 24 часов.\n\n"
            "Если это недоразумение — мы готовы обсудить ситуацию.\n\n"
            "Спасибо!"
        ),
    ),
]


# ---------------------------------------------------------------------------
# WARN2 — второе предупреждение (строгий тон, юридическое)
# ---------------------------------------------------------------------------
WARN2_TEMPLATES: List[MessageTemplate] = [
    MessageTemplate(
        code="WARN2_FIRM_01",
        category=MessageCategory.WARN2,
        tone=ToneLevel.FIRM,
        text=(
            "Уважаемый представитель магазина «{shop_name}»!\n\n"
            "Ранее мы направляли вам уведомление о необходимости убрать "
            "предложения с наших товарных карточек на Kaspi.kz:\n\n"
            "{product_links}\n\n"
            "К сожалению, по состоянию на текущий момент ваши предложения "
            "всё ещё размещены.\n\n"
            "⚠️ ПОВТОРНОЕ ПРЕДУПРЕЖДЕНИЕ\n\n"
            "Данное размещение нарушает правила площадки Kaspi.kz и наши "
            "права как владельцев товарных карточек.\n\n"
            "Если предложения не будут убраны в течение 24 часов, "
            "мы будем вынуждены обратиться в юридический отдел Kaspi.kz "
            "и подготовить материалы для судебного разбирательства.\n\n"
            "Просим отнестись к данному уведомлению серьёзно.\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN2_FIRM_02",
        category=MessageCategory.WARN2,
        tone=ToneLevel.FIRM,
        text=(
            "Добрый день, «{shop_name}»!\n\n"
            "Это повторное уведомление. Несмотря на наше первое обращение, "
            "ваш магазин по-прежнему размещает предложения на наших карточках:\n\n"
            "{product_links}\n\n"
            "⚠️ Обращаем внимание: мы фиксируем все факты размещения, "
            "переписку и даты обнаружения для возможного обращения в суд.\n\n"
            "Срок для отсоединения: 24 часа.\n\n"
            "В случае неисполнения мы оставляем за собой право на подачу "
            "искового заявления с требованием о возмещении убытков.\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN2_FIRM_03",
        category=MessageCategory.WARN2,
        tone=ToneLevel.FIRM,
        text=(
            "Здравствуйте!\n\n"
            "Настоящим повторно уведомляем магазин «{shop_name}» о нарушении "
            "наших прав на товарные карточки Kaspi.kz:\n\n"
            "{product_links}\n\n"
            "Первое уведомление было направлено {warn1_date}.\n\n"
            "⚠️ В случае отсутствия действий с вашей стороны в течение 24 часов, "
            "мы инициируем процедуру подготовки юридической претензии, включая:\n"
            "— обращение в службу поддержки Kaspi.kz\n"
            "— контрольную закупку для фиксации нарушения\n"
            "— подачу искового заявления\n\n"
            "Настоятельно рекомендуем урегулировать вопрос добровольно.\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN2_FIRM_04",
        category=MessageCategory.WARN2,
        tone=ToneLevel.FIRM,
        text=(
            "«{shop_name}», добрый день.\n\n"
            "Вы не отреагировали на наше первое уведомление от {warn1_date}.\n\n"
            "Ваши предложения по-прежнему размещены на наших карточках:\n\n"
            "{product_links}\n\n"
            "⚠️ Это последнее предупреждение перед подготовкой юридических документов.\n\n"
            "Просим отсоединиться в течение 24 часов. После истечения срока "
            "претензия будет направлена без дополнительного уведомления.\n\n"
            "{our_company}"
        ),
    ),
    MessageTemplate(
        code="WARN2_FIRM_05",
        category=MessageCategory.WARN2,
        tone=ToneLevel.FIRM,
        text=(
            "Уведомление №2 для магазина «{shop_name}»\n\n"
            "Мы фиксируем продолжающееся нарушение — размещение предложений "
            "на наших товарных карточках:\n\n"
            "{product_links}\n\n"
            "Первое уведомление: {warn1_date}\n"
            "Срок исполнения: истёк\n\n"
            "⚠️ Дальнейшее игнорирование приведёт к:\n"
            "1. Подаче жалобы в Kaspi.kz\n"
            "2. Проведению контрольной закупки\n"
            "3. Подаче искового заявления в суд\n\n"
            "Крайний срок: 24 часа.\n\n"
            "{our_company}"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Авто-ответы по типу классификации входящего сообщения
# ---------------------------------------------------------------------------

# DIDNT_KNOW — «Я не знал»
REPLY_DIDNT_KNOW: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_DIDNT_KNOW_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Спасибо за ответ!\n\n"
            "Мы понимаем, что это могло произойти по незнанию. "
            "Просим вас убрать предложения с наших карточек:\n\n"
            "{product_links}\n\n"
            "Пожалуйста, подтвердите, когда отсоединитесь. Спасибо!"
        ),
    ),
    MessageTemplate(
        code="REPLY_DIDNT_KNOW_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Благодарим за обратную связь!\n\n"
            "Ничего страшного — главное, что мы можем решить вопрос. "
            "Пожалуйста, уберите свои предложения с карточек:\n\n"
            "{product_links}\n\n"
            "Сообщите нам, когда будет готово."
        ),
    ),
    MessageTemplate(
        code="REPLY_DIDNT_KNOW_03",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Понимаем, бывает!\n\n"
            "Просим отсоединиться от следующих карточек:\n\n"
            "{product_links}\n\n"
            "Как только уберёте — напишите нам для подтверждения. "
            "Будем благодарны за оперативность!"
        ),
    ),
]


# PROVE_IT — «Докажите»
REPLY_PROVE_IT: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_PROVE_IT_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Понимаем ваш запрос.\n\n"
            "Вот факты:\n"
            "— Карточки товаров были созданы нашей компанией\n"
            "— Ваш магазин «{shop_name}» был обнаружен на этих карточках "
            "{detection_date}\n"
            "— Товары:\n{product_links}\n\n"
            "Мы ведём документальный учёт всех обнаружений. "
            "Просим отсоединиться в течение 24 часов."
        ),
    ),
    MessageTemplate(
        code="REPLY_PROVE_IT_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Мы фиксируем все случаи размещения с указанием дат и скриншотов.\n\n"
            "Ваш магазин «{shop_name}» размещает предложения на карточках:\n\n"
            "{product_links}\n\n"
            "Эти карточки принадлежат нашей компании. Если у вас есть "
            "основания оспорить это — пожалуйста, предоставьте документы.\n\n"
            "В противном случае просим отсоединиться в течение 24 часов."
        ),
    ),
    MessageTemplate(
        code="REPLY_PROVE_IT_03",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Готовы предоставить все доказательства.\n\n"
            "Факт размещения вашего магазина «{shop_name}» на наших карточках "
            "зафиксирован автоматической системой мониторинга с указанием "
            "даты и времени.\n\n"
            "Товары:\n{product_links}\n\n"
            "Просим решить вопрос мирно и отсоединиться в течение 24 часов."
        ),
    ),
]


# WONT_REMOVE — «Не сниму»
REPLY_WONT_REMOVE: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_WONT_REMOVE_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.LEGAL,
        text=(
            "Мы сожалеем, что вы приняли такое решение.\n\n"
            "Информируем, что в случае отказа мы будем вынуждены:\n"
            "1. Обратиться в службу поддержки Kaspi.kz\n"
            "2. Провести контрольную закупку для фиксации нарушения\n"
            "3. Подготовить исковое заявление\n\n"
            "Товары:\n{product_links}\n\n"
            "У вас ещё есть возможность решить вопрос мирно в течение 24 часов."
        ),
    ),
    MessageTemplate(
        code="REPLY_WONT_REMOVE_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.LEGAL,
        text=(
            "Ваш отказ зафиксирован.\n\n"
            "В соответствии с правилами Kaspi.kz, размещение предложений "
            "на чужих товарных карточках является нарушением.\n\n"
            "Мы подготовим все необходимые документы для обращения "
            "в судебные органы.\n\n"
            "Товары:\n{product_links}\n\n"
            "Рекомендуем пересмотреть решение в течение 24 часов."
        ),
    ),
    MessageTemplate(
        code="REPLY_WONT_REMOVE_03",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.LEGAL,
        text=(
            "Принято к сведению.\n\n"
            "Ваш отказ от отсоединения будет включён в доказательную базу.\n\n"
            "Карточки:\n{product_links}\n\n"
            "Мы оставляем за собой право на судебную защиту наших "
            "интересов. У вас есть 24 часа на добровольное решение."
        ),
    ),
]


# ALREADY_REMOVED — «Уже снял»
REPLY_ALREADY_REMOVED: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_ALREADY_REMOVED_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Спасибо за информацию!\n\n"
            "Мы проверим статус и подтвердим отсоединение. "
            "Если всё в порядке — вопрос будет закрыт.\n\n"
            "Благодарим за сотрудничество!"
        ),
    ),
    MessageTemplate(
        code="REPLY_ALREADY_REMOVED_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Отлично, спасибо!\n\n"
            "Мы проведём проверку и вернёмся с подтверждением. "
            "Ценим вашу оперативность!"
        ),
    ),
    MessageTemplate(
        code="REPLY_ALREADY_REMOVED_03",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Благодарим за быстрое решение!\n\n"
            "Проверяем — как только убедимся, что предложения убраны, "
            "пришлём подтверждение. Спасибо!"
        ),
    ),
]


# NEED_TIME — «Дайте время»
REPLY_NEED_TIME: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_NEED_TIME_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Понимаем, спасибо за ответ.\n\n"
            "Мы готовы подождать — но просим уложиться в 24 часа.\n\n"
            "Товары:\n{product_links}\n\n"
            "Пожалуйста, подтвердите, когда всё будет готово."
        ),
    ),
    MessageTemplate(
        code="REPLY_NEED_TIME_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Хорошо, даём время.\n\n"
            "Крайний срок — 24 часа с момента этого сообщения.\n\n"
            "Карточки:\n{product_links}\n\n"
            "Напишите нам, когда отсоединитесь. Спасибо!"
        ),
    ),
    MessageTemplate(
        code="REPLY_NEED_TIME_03",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Мы понимаем, что это может занять время.\n\n"
            "Просим завершить отсоединение в течение 24 часов:\n\n"
            "{product_links}\n\n"
            "Ждём вашего подтверждения."
        ),
    ),
]


# AGGRESSIVE — Агрессия
REPLY_AGGRESSIVE: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_AGGRESSIVE_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Мы ведём диалог в конструктивном русле и просим вас "
            "сохранять деловой тон.\n\n"
            "Наша позиция остаётся прежней — просим отсоединиться "
            "от карточек:\n\n"
            "{product_links}\n\n"
            "Вся переписка фиксируется и может быть использована "
            "в качестве доказательства."
        ),
    ),
    MessageTemplate(
        code="REPLY_AGGRESSIVE_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Мы уважаем вашу позицию, но просим общаться корректно.\n\n"
            "Данная переписка ведётся в рамках досудебного урегулирования.\n\n"
            "Просим отсоединиться от карточек:\n{product_links}\n\n"
            "Срок: 24 часа."
        ),
    ),
]


# NEGOTIATE — Попытка договориться
REPLY_NEGOTIATE: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_NEGOTIATE_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Спасибо за готовность к диалогу.\n\n"
            "К сожалению, мы не можем допустить размещение "
            "сторонних предложений на наших карточках.\n\n"
            "Просим отсоединиться от:\n{product_links}\n\n"
            "Если у вас есть предложения по сотрудничеству — "
            "мы готовы обсудить их отдельно, после отсоединения."
        ),
    ),
    MessageTemplate(
        code="REPLY_NEGOTIATE_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.FIRM,
        text=(
            "Мы открыты к переговорам, но отсоединение от карточек — "
            "обязательное условие.\n\n"
            "Карточки:\n{product_links}\n\n"
            "После отсоединения готовы обсудить возможности "
            "сотрудничества. Срок: 24 часа."
        ),
    ),
]


# UNKNOWN — Не удалось классифицировать
REPLY_UNKNOWN: List[MessageTemplate] = [
    MessageTemplate(
        code="REPLY_UNKNOWN_01",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Спасибо за ваше сообщение.\n\n"
            "Напоминаем, что мы просим убрать предложения с наших карточек:\n\n"
            "{product_links}\n\n"
            "Пожалуйста, подтвердите, когда отсоединитесь, "
            "или сообщите, если у вас есть вопросы."
        ),
    ),
    MessageTemplate(
        code="REPLY_UNKNOWN_02",
        category=MessageCategory.AUTO_REPLY,
        tone=ToneLevel.SOFT,
        text=(
            "Получили ваше сообщение.\n\n"
            "Если у вас есть вопросы — мы готовы ответить.\n\n"
            "Просим не забыть об отсоединении от карточек:\n{product_links}\n\n"
            "Ждём вашего ответа."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Реестр шаблонов по типу классификации
# ---------------------------------------------------------------------------
_REPLY_TEMPLATES: Dict[str, List[MessageTemplate]] = {
    "DIDNT_KNOW": REPLY_DIDNT_KNOW,
    "PROVE_IT": REPLY_PROVE_IT,
    "WONT_REMOVE": REPLY_WONT_REMOVE,
    "ALREADY_REMOVED": REPLY_ALREADY_REMOVED,
    "NEED_TIME": REPLY_NEED_TIME,
    "AGGRESSIVE": REPLY_AGGRESSIVE,
    "NEGOTIATE": REPLY_NEGOTIATE,
    "UNKNOWN": REPLY_UNKNOWN,
}


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------

def get_warn1_template() -> MessageTemplate:
    """Получить случайный шаблон WARN1."""
    return random.choice(WARN1_TEMPLATES)


def get_warn2_template() -> MessageTemplate:
    """Получить случайный шаблон WARN2."""
    return random.choice(WARN2_TEMPLATES)


def get_auto_reply_template(classification: str) -> MessageTemplate:
    """
    Получить случайный шаблон авто-ответа по типу классификации.

    Args:
        classification: тип ответа продавца (DIDNT_KNOW, PROVE_IT, и т.д.)

    Returns:
        Случайный шаблон из соответствующего пула.
        Если тип неизвестен — возвращает шаблон для UNKNOWN.
    """
    templates = _REPLY_TEMPLATES.get(classification, REPLY_UNKNOWN)
    return random.choice(templates)


def render_template(template: MessageTemplate, context: Dict[str, str]) -> str:
    """
    Подставить переменные в шаблон.

    Поддерживаемые переменные:
        {shop_name}      — название магазина продавца
        {product_links}  — список ссылок на товары (каждая с новой строки)
        {our_company}    — название нашей компании
        {deadline}       — крайний срок
        {warn1_date}     — дата отправки WARN1
        {detection_date} — дата обнаружения

    Args:
        template: шаблон сообщения
        context: словарь переменных подстановки

    Returns:
        Готовый текст сообщения
    """
    text = template.text
    for key, value in context.items():
        placeholder = "{" + key + "}"
        text = text.replace(placeholder, str(value))
    return text


def get_all_classifications() -> List[str]:
    """Получить список всех поддерживаемых типов классификации."""
    return list(_REPLY_TEMPLATES.keys())
