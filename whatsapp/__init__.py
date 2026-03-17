"""
WhatsApp модуль — интеграция с Green API
"""
from .client import GreenAPIClient, WhatsAppClientBase
from .phone_utils import normalize_phone, is_valid_kz_phone, phone_to_chat_id
from .webhook import WhatsAppWebhook
from .classifier import MessageClassifier, ClassificationType, ClassificationResult
from .templates import (
    MessageTemplate,
    MessageCategory,
    ToneLevel,
    get_warn1_template,
    get_warn2_template,
    get_auto_reply_template,
    render_template,
)

__all__ = [
    'GreenAPIClient',
    'WhatsAppClientBase',
    'WhatsAppWebhook',
    'normalize_phone',
    'is_valid_kz_phone',
    'phone_to_chat_id',
    'MessageClassifier',
    'ClassificationType',
    'ClassificationResult',
    'MessageTemplate',
    'MessageCategory',
    'ToneLevel',
    'get_warn1_template',
    'get_warn2_template',
    'get_auto_reply_template',
    'render_template',
]
