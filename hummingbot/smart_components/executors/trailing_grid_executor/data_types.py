from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Callable, Dict
import asyncio
from pydantic import BaseModel, Field

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.smart_components.executors.data_types import ExecutorConfigBase



class TrailingGridExecutorConfig(ExecutorConfigBase):
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


