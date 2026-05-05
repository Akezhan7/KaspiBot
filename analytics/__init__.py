"""
Analytics модуль — расчёт ROI/ROAS и агрегация данных Kaspi Marketing.
"""
from .processor import AdsAnalyticsProcessor
from .aggregator import DataAggregator

__all__ = [
    "AdsAnalyticsProcessor",
    "DataAggregator",
]
