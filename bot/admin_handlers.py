"""
Админ-команды для управления воронкой и контрольной закупкой.

Фаза 7: /assign_purchase, /purchase_done, FSM-диалог
Фаза 8: /workflows, /workflow, /warn, /legal_requests, /legal,
         /export, /close_workflow, inline-кнопки
"""
import io
import json
import logging
from pathlib import Path
from typing import Optional

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from database import LegalRequestsDB, MessageLogDB, SellersDB, SellerWorkflowDB

logger = logging.getLogger(__name__)

admin_router = Router()


# === FSM States ===

class PurchaseDataFSM(StatesGroup):
    """Состояния ввода данных контрольной закупки"""
    waiting_bin = State()
    waiting_order = State()
    waiting_docs = State()
    waiting_notes = State()
    confirm = State()


# === Helpers ===

def _is_admin(user_id: int) -> bool:
    return user_id in Config.ADMIN_USER_IDS


def _get_legal_db() -> LegalRequestsDB:
    return LegalRequestsDB(str(Config.DB_PATH))


def _get_workflow_db() -> SellerWorkflowDB:
    return SellerWorkflowDB(str(Config.DB_PATH))


def _get_message_log_db() -> MessageLogDB:
    return MessageLogDB(str(Config.DB_PATH))


def _get_sellers_db() -> SellersDB:
    return SellersDB(str(Config.DB_PATH))


def _docs_dir(request_id: int) -> Path:
    """Директория для документов конкретной заявки"""
    d = Config.PURCHASE_DOCUMENTS_DIR / str(request_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# === /assign_purchase ===

@admin_router.message(Command("assign_purchase"))
async def cmd_assign_purchase(message: Message) -> None:
    """
    /assign_purchase <request_id> <@username>
    Назначить контрольную закупку ответственному лицу.
    """
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Формат: /assign_purchase <code>&lt;request_id&gt;</code> "
            "<code>&lt;@username&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        request_id = int(parts[1])
    except ValueError:
        await message.answer("ID заявки должен быть числом")
        return

    assigned_to = parts[2].strip()

    legal_db = _get_legal_db()
    req = await legal_db.get_request(request_id)
    if not req:
        await message.answer(f"Юрзаявка #{request_id} не найдена")
        return

    if req["control_purchase_status"] == "COMPLETED":
        await message.answer(
            f"Закупка по заявке #{request_id} уже выполнена"
        )
        return

    await legal_db.assign_purchase(request_id, assigned_to)

    # Обновить статус workflow → CONTROL_PURCHASE_REQUIRED
    workflow_db = _get_workflow_db()
    workflow_id = req["workflow_id"]
    await workflow_db.update_status(workflow_id, "CONTROL_PURCHASE_REQUIRED")

    await message.answer(
        f"✅ <b>Закупка назначена</b>\n\n"
        f"Заявка: #{request_id}\n"
        f"Магазин: {req.get('shop_name', '—')}\n"
        f"Ответственный: {assigned_to}\n\n"
        f"Для ввода данных после закупки: "
        f"/purchase_done {request_id}",
        parse_mode="HTML",
    )

    logger.info(
        f"Закупка для заявки {request_id} назначена: {assigned_to}, "
        f"admin={message.from_user.id}"
    )


# === /purchase_done — запуск FSM ===

@admin_router.message(Command("purchase_done"))
async def cmd_purchase_done(message: Message, state: FSMContext) -> None:
    """
    /purchase_done <request_id>
    Начать ввод данных контрольной закупки через FSM-диалог.
    """
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /purchase_done <code>&lt;request_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        request_id = int(parts[1])
    except ValueError:
        await message.answer("ID заявки должен быть числом")
        return

    legal_db = _get_legal_db()
    req = await legal_db.get_request(request_id)
    if not req:
        await message.answer(f"Юрзаявка #{request_id} не найдена")
        return

    if req.get("ready_for_lawsuit"):
        await message.answer(
            f"Заявка #{request_id} уже готова к подаче иска"
        )
        return

    # Сохраняем request_id в FSM-контексте
    await state.update_data(
        request_id=request_id,
        shop_name=req.get("shop_name", "Неизвестный"),
        doc_paths=[],
    )
    await state.set_state(PurchaseDataFSM.waiting_bin)

    await message.answer(
        f"📋 <b>Ввод данных закупки — заявка #{request_id}</b>\n"
        f"Магазин: {req.get('shop_name', '—')}\n\n"
        f"<b>Шаг 1/4:</b> Введите БИН/ИИН продавца\n\n"
        f"Для отмены: /cancel_purchase",
        parse_mode="HTML",
    )


# === /cancel_purchase ===

@admin_router.message(Command("cancel_purchase"))
async def cmd_cancel_purchase(message: Message, state: FSMContext) -> None:
    """Отменить ввод данных контрольной закупки"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного ввода данных для отмены")
        return

    await state.clear()
    await message.answer("❌ Ввод данных закупки отменён")


# === FSM: Шаг 1 — БИН/ИИН ===

@admin_router.message(PurchaseDataFSM.waiting_bin, F.text)
async def fsm_process_bin(message: Message, state: FSMContext) -> None:
    """Получение БИН/ИИН"""
    bin_iin = message.text.strip()

    # Валидация: БИН — 12 цифр, ИИН — 12 цифр
    clean = bin_iin.replace(" ", "").replace("-", "")
    if not clean.isdigit() or len(clean) != 12:
        await message.answer(
            "⚠️ БИН/ИИН должен содержать 12 цифр.\n"
            "Попробуйте ещё раз или /cancel_purchase для отмены"
        )
        return

    await state.update_data(bin_iin=clean)
    await state.set_state(PurchaseDataFSM.waiting_order)

    await message.answer(
        f"✅ БИН/ИИН: <code>{clean}</code>\n\n"
        f"<b>Шаг 2/4:</b> Введите номер заказа Kaspi",
        parse_mode="HTML",
    )


# === FSM: Шаг 2 — Номер заказа ===

@admin_router.message(PurchaseDataFSM.waiting_order, F.text)
async def fsm_process_order(message: Message, state: FSMContext) -> None:
    """Получение номера заказа"""
    order_number = message.text.strip()

    if not order_number:
        await message.answer("⚠️ Введите номер заказа")
        return

    await state.update_data(order_number=order_number)
    await state.set_state(PurchaseDataFSM.waiting_docs)

    await message.answer(
        f"✅ Заказ: <code>{order_number}</code>\n\n"
        f"<b>Шаг 3/4:</b> Отправьте скриншоты/документы\n"
        f"(фото или файлы — можно несколько)\n\n"
        f"Когда все документы будут отправлены, нажмите кнопку:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📎 Документы загружены",
                callback_data="purchase_docs_done",
            )],
        ]),
    )


# === FSM: Шаг 3 — Документы (фото) ===

@admin_router.message(PurchaseDataFSM.waiting_docs, F.photo)
async def fsm_process_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Приём фотографий для закупки"""
    data = await state.get_data()
    request_id = data["request_id"]
    doc_paths: list = data.get("doc_paths", [])

    # Берём самое большое фото
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    file_name = f"photo_{len(doc_paths) + 1}.jpg"
    dest = _docs_dir(request_id) / file_name
    await bot.download_file(file.file_path, str(dest))

    doc_paths.append(str(dest))
    await state.update_data(doc_paths=doc_paths)

    await message.answer(
        f"📷 Фото сохранено ({len(doc_paths)} шт.)\n"
        f"Отправьте ещё или нажмите «Документы загружены»"
    )


# === FSM: Шаг 3 — Документы (файл) ===

@admin_router.message(PurchaseDataFSM.waiting_docs, F.document)
async def fsm_process_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """Приём документов (файлов) для закупки"""
    data = await state.get_data()
    request_id = data["request_id"]
    doc_paths: list = data.get("doc_paths", [])

    doc = message.document
    file = await bot.get_file(doc.file_id)

    # Используем оригинальное имя файла, если есть
    original_name = doc.file_name or f"doc_{len(doc_paths) + 1}"
    # Безопасное имя: убираем спецсимволы
    safe_name = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in original_name
    )
    dest = _docs_dir(request_id) / safe_name

    # Если файл с таким именем уже есть — добавляем суффикс
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = _docs_dir(request_id) / f"{stem}_{counter}{suffix}"
            counter += 1

    await bot.download_file(file.file_path, str(dest))

    doc_paths.append(str(dest))
    await state.update_data(doc_paths=doc_paths)

    await message.answer(
        f"📄 Файл сохранён: {safe_name} ({len(doc_paths)} шт.)\n"
        f"Отправьте ещё или нажмите «Документы загружены»"
    )


# === FSM: Шаг 3 → 4 (кнопка «Документы загружены») ===

@admin_router.callback_query(
    PurchaseDataFSM.waiting_docs,
    F.data == "purchase_docs_done",
)
async def fsm_docs_done(callback: CallbackQuery, state: FSMContext) -> None:
    """Переход от документов к комментарию"""
    data = await state.get_data()
    doc_count = len(data.get("doc_paths", []))

    await callback.answer()
    await state.set_state(PurchaseDataFSM.waiting_notes)

    await callback.message.answer(
        f"✅ Документов: {doc_count}\n\n"
        f"<b>Шаг 4/4:</b> Добавьте комментарий (или отправьте «-» чтобы пропустить)",
        parse_mode="HTML",
    )


# === FSM: Шаг 4 — Комментарий ===

@admin_router.message(PurchaseDataFSM.waiting_notes, F.text)
async def fsm_process_notes(message: Message, state: FSMContext) -> None:
    """Получение комментария"""
    notes = message.text.strip()
    if notes == "-":
        notes = ""

    await state.update_data(notes=notes)
    await state.set_state(PurchaseDataFSM.confirm)

    data = await state.get_data()

    # Показать сводку для подтверждения
    doc_count = len(data.get("doc_paths", []))
    summary = (
        f"📋 <b>Подтвердите данные закупки</b>\n\n"
        f"Заявка: #{data['request_id']}\n"
        f"Магазин: {data.get('shop_name', '—')}\n"
        f"БИН/ИИН: <code>{data['bin_iin']}</code>\n"
        f"Заказ: <code>{data['order_number']}</code>\n"
        f"Документов: {doc_count}\n"
        f"Комментарий: {notes or '—'}\n"
    )

    await message.answer(
        summary,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data="purchase_confirm_yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="purchase_confirm_no",
                ),
            ],
        ]),
    )


# === FSM: Подтверждение — Да ===

@admin_router.callback_query(
    PurchaseDataFSM.confirm,
    F.data == "purchase_confirm_yes",
)
async def fsm_confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение ввода данных — сохранение в БД"""
    await callback.answer()

    data = await state.get_data()
    request_id = data["request_id"]
    bin_iin = data["bin_iin"]
    order_number = data["order_number"]
    notes = data.get("notes", "")
    doc_paths = data.get("doc_paths", [])

    documents_json = json.dumps(doc_paths, ensure_ascii=False) if doc_paths else None

    legal_db = _get_legal_db()
    workflow_db = _get_workflow_db()

    # Сохранить данные закупки
    await legal_db.update_purchase_info(
        request_id=request_id,
        bin_iin=bin_iin,
        order_number=order_number,
        notes=notes or None,
        documents=documents_json,
    )

    # Отметить готовность к иску
    await legal_db.mark_ready_for_lawsuit(request_id)

    # Обновить статус workflow → READY_FOR_LAWSUIT
    req = await legal_db.get_request(request_id)
    if req:
        await workflow_db.update_status(req["workflow_id"], "READY_FOR_LAWSUIT")

    await state.clear()

    await callback.message.answer(
        f"✅ <b>Данные закупки сохранены!</b>\n\n"
        f"Заявка #{request_id} готова к подаче иска.\n"
        f"Экспорт: /export {request_id}",
        parse_mode="HTML",
    )

    logger.info(
        f"Закупка завершена: заявка={request_id}, "
        f"БИН={bin_iin}, заказ={order_number}, "
        f"документов={len(doc_paths)}, "
        f"admin={callback.from_user.id}"
    )


# === FSM: Подтверждение — Нет ===

@admin_router.callback_query(
    PurchaseDataFSM.confirm,
    F.data == "purchase_confirm_no",
)
async def fsm_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена подтверждения"""
    await callback.answer()
    await state.clear()

    await callback.message.answer(
        "❌ Ввод данных закупки отменён.\n"
        "Для повторного ввода используйте /purchase_done"
    )


# =====================================================================
# ФАЗА 8: Админ-панель — команды и inline-кнопки
# =====================================================================

# Статусы → эмодзи для отображения
_STATUS_EMOJI = {
    "NEW_SELLER_ATTACH": "🆕",
    "WARN1_SENT": "⚠️",
    "WARN2_SENT": "⚠️⚠️",
    "DIALOG_ACTIVE": "💬",
    "LEGAL_REQUEST_CREATED": "⚖️",
    "CONTROL_PURCHASE_REQUIRED": "🛒",
    "READY_FOR_LAWSUIT": "📄",
    "DETACHED": "🔌",
    "CLOSED": "✅",
    "RECIDIVE": "🔄",
}

ITEMS_PER_PAGE = 10


def _workflow_buttons(workflow_id: int, status: str) -> InlineKeyboardMarkup:
    """Inline-кнопки для карточки воронки, зависят от статуса."""
    rows: list[list[InlineKeyboardButton]] = []

    if status == "NEW_SELLER_ATTACH":
        rows.append([InlineKeyboardButton(
            text="⚠️ Отправить WARN1",
            callback_data=f"wf_warn1_{workflow_id}",
        )])
    elif status in ("WARN1_SENT", "DIALOG_ACTIVE"):
        rows.append([InlineKeyboardButton(
            text="📤 Отправить WARN2",
            callback_data=f"wf_warn2_{workflow_id}",
        )])
    elif status == "WARN2_SENT":
        rows.append([InlineKeyboardButton(
            text="⚖️ Юрзаявка",
            callback_data=f"wf_escalate_{workflow_id}",
        )])

    if status not in ("CLOSED", "READY_FOR_LAWSUIT"):
        rows.append([InlineKeyboardButton(
            text="✅ Закрыть воронку",
            callback_data=f"wf_closeask_{workflow_id}",
        )])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _get_workflow_engine():
    """Получить ссылку на глобальный workflow_engine из main."""
    import main  # noqa: E402 — ленивый импорт для избежания циклов
    return main.workflow_engine


def _get_evidence_exporter():
    """Создать EvidenceExporter с актуальными DAO."""
    from workflow import EvidenceExporter
    db = str(Config.DB_PATH)
    return EvidenceExporter(
        legal_db=LegalRequestsDB(db),
        workflow_db=SellerWorkflowDB(db),
        message_log_db=MessageLogDB(db),
        sellers_db=SellersDB(db),
        products_db=__import__("database").ProductsDB(db),
    )


# === /workflows ===

@admin_router.message(Command("workflows"))
async def cmd_workflows(message: Message) -> None:
    """Список активных воронок с пагинацией."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    page = 1
    if len(parts) > 1:
        try:
            page = max(1, int(parts[1]))
        except ValueError:
            pass

    workflow_db = _get_workflow_db()
    total = await workflow_db.count_active_workflows()

    if total == 0:
        await message.answer("Нет активных воронок")
        return

    offset = (page - 1) * ITEMS_PER_PAGE
    workflows = await workflow_db.get_all_active_workflows(
        limit=ITEMS_PER_PAGE, offset=offset
    )
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    lines = [f"<b>Активные воронки</b> ({total} шт., стр. {page}/{total_pages})\n"]
    for wf in workflows:
        emoji = _STATUS_EMOJI.get(wf["status"], "❓")
        name = wf.get("merchant_name", "?")
        products_count = wf.get("products_count", 0)
        lines.append(
            f"{emoji} <b>#{wf['id']}</b> {name}\n"
            f"   Статус: {wf['status']} | Товаров: {products_count}\n"
            f"   /workflow {wf['id']}"
        )

    # Пагинация
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"wf_page_{page - 1}",
        ))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"wf_page_{page + 1}",
        ))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_buttons]) if nav_buttons else None

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


# === /workflow <id> ===

@admin_router.message(Command("workflow"))
async def cmd_workflow_detail(message: Message) -> None:
    """Детальная карточка воронки."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /workflow <code>&lt;id&gt;</code>", parse_mode="HTML"
        )
        return

    try:
        wf_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    text, keyboard = await _build_workflow_card(wf_id)
    if text is None:
        await message.answer(f"Воронка #{wf_id} не найдена")
        return

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _build_workflow_card(wf_id: int) -> tuple[Optional[str], Optional[InlineKeyboardMarkup]]:
    """Сформировать текстовую карточку воронки + кнопки."""
    workflow_db = _get_workflow_db()
    wf = await workflow_db.get_workflow(wf_id)
    if not wf:
        return None, None

    sellers_db = _get_sellers_db()
    seller = await sellers_db.get_seller(wf["seller_id"])
    name = seller.get("merchant_name", "?") if seller else "?"
    phone = seller.get("phone", "—") if seller else "—"

    products = await workflow_db.get_workflow_products(wf_id)
    products_lines = []
    for p in products:
        attached = "✅" if p.get("still_attached") else "❌"
        products_lines.append(
            f"  {attached} {p.get('title', p['product_id'])}"
        )

    msg_db = _get_message_log_db()
    messages = await msg_db.get_messages_for_workflow(wf_id)
    msg_count_in = sum(1 for m in messages if m["direction"] == "IN")
    msg_count_out = sum(1 for m in messages if m["direction"] == "OUT")

    emoji = _STATUS_EMOJI.get(wf["status"], "❓")

    text = (
        f"{emoji} <b>Воронка #{wf_id}</b>\n\n"
        f"<b>Магазин:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Статус:</b> {wf['status']}\n"
        f"<b>Создана:</b> {wf.get('created_at', '—')}\n"
        f"<b>Обновлена:</b> {wf.get('updated_at', '—')}\n"
    )

    if wf.get("warn1_sent_at"):
        text += f"<b>WARN1:</b> {wf['warn1_sent_at']}\n"
    if wf.get("warn2_sent_at"):
        text += f"<b>WARN2:</b> {wf['warn2_sent_at']}\n"

    text += f"\n<b>Товары ({len(products)}):</b>\n"
    text += "\n".join(products_lines) if products_lines else "  —"
    text += f"\n\n<b>Сообщения:</b> ↗️ {msg_count_out} / ↙️ {msg_count_in}"

    # Последние 3 сообщения
    if messages:
        text += "\n\n<b>Последние сообщения:</b>"
        for m in messages[-3:]:
            arrow = "→" if m["direction"] == "OUT" else "←"
            snippet = m["message_text"][:80]
            text += f"\n{arrow} {snippet}"

    keyboard = _workflow_buttons(wf_id, wf["status"])
    return text, keyboard


# === /warn <seller_id> ===

@admin_router.message(Command("warn"))
async def cmd_warn(message: Message) -> None:
    """Ручная отправка предупреждения продавцу."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /warn <code>&lt;seller_id&gt;</code>", parse_mode="HTML"
        )
        return

    seller_id = parts[1].strip()

    workflow_db = _get_workflow_db()
    wf = await workflow_db.get_active_workflow_for_seller(seller_id)
    if not wf:
        await message.answer(
            f"Нет активной воронки для продавца <code>{seller_id}</code>",
            parse_mode="HTML",
        )
        return

    engine = _get_workflow_engine()
    if engine is None:
        await message.answer("Движок воронки не инициализирован")
        return

    wf_id = wf["id"]
    status = wf["status"]

    if status in ("NEW_SELLER_ATTACH", "WARN1_SENT", "DIALOG_ACTIVE"):
        # Отправляем WARN1 для NEW, WARN2 для остальных
        if status == "NEW_SELLER_ATTACH":
            ok = await engine.send_warn1(wf_id)
            label = "WARN1"
        else:
            ok = await engine.send_warn2(wf_id)
            label = "WARN2"

        if ok:
            await message.answer(
                f"✅ {label} отправлен для воронки #{wf_id}"
            )
        else:
            await message.answer(
                f"⚠️ Не удалось отправить {label} (см. логи)"
            )
    else:
        await message.answer(
            f"Воронка #{wf_id} в статусе {status} — отправка warn невозможна"
        )


# === /legal_requests ===

@admin_router.message(Command("legal_requests"))
async def cmd_legal_requests(message: Message) -> None:
    """Список юрзаявок с пагинацией."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    page = 1
    if len(parts) > 1:
        try:
            page = max(1, int(parts[1]))
        except ValueError:
            pass

    legal_db = _get_legal_db()
    total = await legal_db.count_requests()

    if total == 0:
        await message.answer("Нет юрзаявок")
        return

    offset = (page - 1) * ITEMS_PER_PAGE
    requests = await legal_db.get_all_requests(limit=ITEMS_PER_PAGE, offset=offset)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    lines = [f"<b>Юридические заявки</b> ({total} шт., стр. {page}/{total_pages})\n"]
    for req in requests:
        ready = "✅" if req.get("ready_for_lawsuit") else "⏳"
        purchase = req.get("control_purchase_status", "PENDING")
        name = req.get("merchant_name") or req.get("shop_name") or "?"
        lines.append(
            f"{ready} <b>#{req['id']}</b> {name}\n"
            f"   Закупка: {purchase}\n"
            f"   /legal {req['id']}"
        )

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"lr_page_{page - 1}",
        ))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"lr_page_{page + 1}",
        ))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_buttons]) if nav_buttons else None

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


# === /legal <id> ===

@admin_router.message(Command("legal"))
async def cmd_legal_detail(message: Message) -> None:
    """Детали юрзаявки."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /legal <code>&lt;id&gt;</code>", parse_mode="HTML"
        )
        return

    try:
        req_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    legal_db = _get_legal_db()
    req = await legal_db.get_request(req_id)
    if not req:
        await message.answer(f"Юрзаявка #{req_id} не найдена")
        return

    ready_icon = "✅" if req.get("ready_for_lawsuit") else "⏳"
    text = (
        f"⚖️ <b>Юрзаявка #{req_id}</b> {ready_icon}\n\n"
        f"<b>Магазин:</b> {req.get('shop_name', '?')}\n"
        f"<b>Телефон:</b> {req.get('phone', '—')}\n"
        f"<b>Workflow:</b> #{req.get('workflow_id')}\n"
        f"<b>Создана:</b> {req.get('created_at', '—')}\n"
        f"<b>Закупка:</b> {req.get('control_purchase_status', 'PENDING')}\n"
    )

    if req.get("assigned_to"):
        text += f"<b>Назначена:</b> {req['assigned_to']}\n"
    if req.get("bin_iin"):
        text += f"<b>БИН/ИИН:</b> <code>{req['bin_iin']}</code>\n"
    if req.get("purchase_order_number"):
        text += f"<b>Заказ:</b> <code>{req['purchase_order_number']}</code>\n"
    if req.get("completed_at"):
        text += f"<b>Завершена:</b> {req['completed_at']}\n"

    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([
        InlineKeyboardButton(
            text="📋 Воронка",
            callback_data=f"wf_view_{req['workflow_id']}",
        ),
        InlineKeyboardButton(
            text="📦 Экспорт",
            callback_data=f"wf_export_{req_id}",
        ),
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


# === /export <request_id> ===

@admin_router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Экспорт юрзаявки как ZIP-архив."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /export <code>&lt;request_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        req_id = int(parts[1])
    except ValueError:
        await message.answer("ID заявки должен быть числом")
        return

    legal_db = _get_legal_db()
    req = await legal_db.get_request(req_id)
    if not req:
        await message.answer(f"Юрзаявка #{req_id} не найдена")
        return

    await message.answer("📦 Формирую архив, подождите...")

    try:
        exporter = _get_evidence_exporter()
        zip_bytes = await exporter.generate_legal_package(req_id)

        file = BufferedInputFile(
            zip_bytes,
            filename=f"legal_package_{req_id}.zip",
        )
        await message.answer_document(
            file,
            caption=f"⚖️ Юрзаявка #{req_id} — {req.get('shop_name', '?')}",
        )
    except Exception as e:
        logger.error(f"Ошибка экспорта юрзаявки {req_id}: {e}", exc_info=True)
        await message.answer(f"Ошибка при формировании архива: {e}")


# === /close_workflow <id> ===

@admin_router.message(Command("close_workflow"))
async def cmd_close_workflow(message: Message) -> None:
    """Ручное закрытие воронки."""
    if not _is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /close_workflow <code>&lt;id&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        wf_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    workflow_db = _get_workflow_db()
    wf = await workflow_db.get_workflow(wf_id)
    if not wf:
        await message.answer(f"Воронка #{wf_id} не найдена")
        return

    if wf["status"] == "CLOSED":
        await message.answer(f"Воронка #{wf_id} уже закрыта")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, закрыть",
                callback_data=f"wf_close_confirm_{wf_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="wf_close_cancel",
            ),
        ],
    ])

    sellers_db = _get_sellers_db()
    seller = await sellers_db.get_seller(wf["seller_id"])
    name = seller.get("merchant_name", "?") if seller else "?"

    await message.answer(
        f"Закрыть воронку #{wf_id}?\n"
        f"Магазин: {name}\n"
        f"Статус: {wf['status']}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =====================================================================
# Callback-обработчики (Фаза 8.2)
# =====================================================================

# --- Пагинация воронок ---

@admin_router.callback_query(F.data.startswith("wf_page_"))
async def cb_workflows_page(callback: CallbackQuery) -> None:
    """Пагинация в списке воронок."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    page = int(callback.data.split("_")[-1])
    await callback.answer()

    workflow_db = _get_workflow_db()
    total = await workflow_db.count_active_workflows()

    if total == 0:
        await callback.message.edit_text("Нет активных воронок")
        return

    offset = (page - 1) * ITEMS_PER_PAGE
    workflows = await workflow_db.get_all_active_workflows(
        limit=ITEMS_PER_PAGE, offset=offset
    )
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    lines = [f"<b>Активные воронки</b> ({total} шт., стр. {page}/{total_pages})\n"]
    for wf in workflows:
        emoji = _STATUS_EMOJI.get(wf["status"], "❓")
        name = wf.get("merchant_name", "?")
        products_count = wf.get("products_count", 0)
        lines.append(
            f"{emoji} <b>#{wf['id']}</b> {name}\n"
            f"   Статус: {wf['status']} | Товаров: {products_count}\n"
            f"   /workflow {wf['id']}"
        )

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"wf_page_{page - 1}",
        ))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"wf_page_{page + 1}",
        ))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_buttons]) if nav_buttons else None

    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )


# --- Пагинация юрзаявок ---

@admin_router.callback_query(F.data.startswith("lr_page_"))
async def cb_legal_page(callback: CallbackQuery) -> None:
    """Пагинация в списке юрзаявок."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    page = int(callback.data.split("_")[-1])
    await callback.answer()

    legal_db = _get_legal_db()
    total = await legal_db.count_requests()
    offset = (page - 1) * ITEMS_PER_PAGE
    requests = await legal_db.get_all_requests(limit=ITEMS_PER_PAGE, offset=offset)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    lines = [f"<b>Юридические заявки</b> ({total} шт., стр. {page}/{total_pages})\n"]
    for req in requests:
        ready = "✅" if req.get("ready_for_lawsuit") else "⏳"
        purchase = req.get("control_purchase_status", "PENDING")
        name = req.get("merchant_name") or req.get("shop_name") or "?"
        lines.append(
            f"{ready} <b>#{req['id']}</b> {name}\n"
            f"   Закупка: {purchase}\n"
            f"   /legal {req['id']}"
        )

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"lr_page_{page - 1}",
        ))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"lr_page_{page + 1}",
        ))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_buttons]) if nav_buttons else None

    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )


# --- Просмотр воронки (inline) ---

@admin_router.callback_query(F.data.startswith("wf_view_"))
async def cb_workflow_view(callback: CallbackQuery) -> None:
    """Показать карточку воронки по inline-кнопке."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer()

    text, keyboard = await _build_workflow_card(wf_id)
    if text is None:
        await callback.message.answer(f"Воронка #{wf_id} не найдена")
        return

    await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)


# --- Ручной WARN1 (inline) ---

@admin_router.callback_query(F.data.startswith("wf_warn1_"))
async def cb_send_warn1(callback: CallbackQuery) -> None:
    """Отправить WARN1 через inline-кнопку."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer("Отправляю WARN1...")

    engine = _get_workflow_engine()
    if engine is None:
        await callback.message.answer("Движок воронки не инициализирован")
        return

    ok = await engine.send_warn1(wf_id)
    if ok:
        await callback.message.answer(f"✅ WARN1 отправлен (воронка #{wf_id})")
    else:
        await callback.message.answer(f"⚠️ Не удалось отправить WARN1 (#{wf_id})")


# --- Ручной WARN2 (inline) ---

@admin_router.callback_query(F.data.startswith("wf_warn2_"))
async def cb_send_warn2(callback: CallbackQuery) -> None:
    """Отправить WARN2 через inline-кнопку."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer("Отправляю WARN2...")

    engine = _get_workflow_engine()
    if engine is None:
        await callback.message.answer("Движок воронки не инициализирован")
        return

    ok = await engine.send_warn2(wf_id)
    if ok:
        await callback.message.answer(f"✅ WARN2 отправлен (воронка #{wf_id})")
    else:
        await callback.message.answer(f"⚠️ Не удалось отправить WARN2 (#{wf_id})")


# --- Ручная эскалация (inline) ---

@admin_router.callback_query(F.data.startswith("wf_escalate_"))
async def cb_escalate(callback: CallbackQuery) -> None:
    """Эскалация до юрзаявки через inline-кнопку."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer("Создаю юрзаявку...")

    engine = _get_workflow_engine()
    if engine is None:
        await callback.message.answer("Движок воронки не инициализирован")
        return

    req_id = await engine.escalate_to_legal(wf_id)
    if req_id:
        await callback.message.answer(
            f"⚖️ Юрзаявка #{req_id} создана (воронка #{wf_id})\n"
            f"/legal {req_id}"
        )
    else:
        await callback.message.answer(
            f"⚠️ Не удалось создать юрзаявку (#{wf_id})"
        )


# --- Закрытие воронки (inline button → подтверждение) ---

@admin_router.callback_query(F.data.startswith("wf_close_confirm_"))
async def cb_close_confirm(callback: CallbackQuery) -> None:
    """Подтверждение закрытия воронки."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer()

    engine = _get_workflow_engine()
    if engine is None:
        await callback.message.edit_text("Движок воронки не инициализирован")
        return

    await engine.close_workflow(wf_id, reason="manual_close")
    await callback.message.edit_text(f"✅ Воронка #{wf_id} закрыта")


@admin_router.callback_query(F.data == "wf_close_cancel")
async def cb_close_cancel(callback: CallbackQuery) -> None:
    """Отмена закрытия воронки."""
    await callback.answer("Отменено")
    await callback.message.edit_text("Закрытие отменено")


# --- Кнопка закрытия из карточки воронки ---

@admin_router.callback_query(F.data.startswith("wf_closeask_"))
async def cb_close_workflow(callback: CallbackQuery) -> None:
    """Спросить подтверждение перед закрытием (из карточки)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    wf_id = int(callback.data.split("_")[-1])
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, закрыть",
                callback_data=f"wf_close_confirm_{wf_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="wf_close_cancel",
            ),
        ],
    ])
    await callback.message.answer(
        f"Закрыть воронку #{wf_id}?",
        reply_markup=keyboard,
    )


# --- Экспорт через inline-кнопку ---

@admin_router.callback_query(F.data.startswith("wf_export_"))
async def cb_export(callback: CallbackQuery) -> None:
    """Экспорт юрзаявки через inline-кнопку."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    req_id = int(callback.data.split("_")[-1])
    await callback.answer("Формирую архив...")

    legal_db = _get_legal_db()
    req = await legal_db.get_request(req_id)
    if not req:
        await callback.message.answer(f"Юрзаявка #{req_id} не найдена")
        return

    try:
        exporter = _get_evidence_exporter()
        zip_bytes = await exporter.generate_legal_package(req_id)

        file = BufferedInputFile(
            zip_bytes,
            filename=f"legal_package_{req_id}.zip",
        )
        await callback.message.answer_document(
            file,
            caption=f"⚖️ Юрзаявка #{req_id} — {req.get('shop_name', '?')}",
        )
    except Exception as e:
        logger.error(f"Ошибка экспорта юрзаявки {req_id}: {e}", exc_info=True)
        await callback.message.answer(f"Ошибка при формировании архива: {e}")
