from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Callable, Dict
import asyncio
from pydantic import BaseModel, Field

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.smart_components.executors.data_types import ExecutorConfigBase



class TrailingGridExecutorHotReloadingConfig(ExecutorConfigBase):
    type = "trailing_grid_executor"
    trading_pair: str
    connector_name: str
    executor_interval: int = 60
    open_order_type: OrderType = OrderType.MARKET
    take_profit_order_type: OrderType = OrderType.MARKET

    signal_func: Optional[Callable]
    signal_func_args: Optional[tuple]
    signal_func_kwargs: Optional[dict]

    params: Dict
    stop_queue: asyncio.Queue

    class Config:
        arbitrary_types_allowed = True  # 允许任意类型


class TrailingGridExecutorConfig(ExecutorConfigBase):
    type = "trailing_grid_executor"
    trading_pair: str
    connector_name: str
    executor_interval: int = 60

    amount_quote: Decimal = 50
    leverage: int = 10
    interval: str = "1m"
    max_records: int = 100

    grid_max: int = 100
    grid_open: Decimal = Decimal(0.02)
    grid_close: Decimal = Decimal(0.01)
    mart_open: Decimal = Decimal(0.25)
    side: TradeType = TradeType.BUY

    open_order_type: OrderType = OrderType.MARKET
    take_profit_order_type: OrderType = OrderType.MARKET

    signal_func: Optional[Callable]
    signal_func_args: Optional[tuple]
    signal_func_kwargs: Optional[dict]


