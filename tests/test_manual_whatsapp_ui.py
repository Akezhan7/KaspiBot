"""Тесты карточки продавца для ручной WhatsApp-отправки."""

import pytest

from bot.handlers import _build_seller_details


def _seller_data(product_count: int) -> dict:
    return {
        "merchant_id": "M001",
        "merchant_name": "Test Shop",
        "phone": "+77011234567",
        "products": [
            {
                "product_id": f"SKU{i}",
                "title": f"Товар {i}",
                "url": f"https://kaspi.kz/shop/p/{i}/",
                "price": 1000,
            }
            for i in range(product_count)
        ],
    }


def test_seller_card_shows_manual_progress_and_one_action():
    tracking = {
        "manual_products_sent_at": "2026-06-14 15:30:00",
        "manual_products_initial_count": 12,
    }

    text, markup = _build_seller_details(
        _seller_data(5),
        tracking=tracking,
        show_whatsapp_action=True,
    )

    assert "Отправлено: 14.06.2026 15:30" in text
    assert "Было товаров: 12" in text
    assert "Осталось: 5" in text
    assert "Откреплено: 7" in text
    assert "частично открепился" in text

    action_buttons = [
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == "Отправить товары в WhatsApp"
    ]
    assert len(action_buttons) == 1
    assert action_buttons[0].callback_data == "wa_products_send_M001"


@pytest.mark.parametrize(
    ("current_count", "expected_status"),
    [
        (12, "без изменений"),
        (0, "полностью открепился"),
    ],
)
def test_seller_card_manual_progress_statuses(current_count, expected_status):
    tracking = {
        "manual_products_sent_at": "2026-06-14 15:30:00",
        "manual_products_initial_count": 12,
    }

    text, _ = _build_seller_details(
        _seller_data(current_count),
        tracking=tracking,
    )

    assert expected_status in text


def test_seller_card_hides_whatsapp_action_for_non_admin():
    _, markup = _build_seller_details(
        _seller_data(2),
        show_whatsapp_action=False,
    )

    assert all(
        button.text != "Отправить товары в WhatsApp"
        for row in markup.inline_keyboard
        for button in row
    )
