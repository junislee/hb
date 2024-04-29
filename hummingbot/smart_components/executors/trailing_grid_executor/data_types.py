from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Callable

from pydantic import BaseModel

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.smart_components.executors.data_types import ExecutorConfigBase


class TrailingGridConfig(BaseModel):
    grid_max: Optional[Decimal]
    grid_open: Optional[Decimal]
    grid_close: Optional[Decimal]
    mart_open: Optional[Decimal]

    open_order_type: OrderType = OrderType.MARKET
    take_profit_order_type: OrderType = OrderType.MARKET


class TrailingGridExecutorConfig(ExecutorConfigBase):
    type = "trailing_grid_executor"
    trading_pair: str
    connector_name: str
    executor_interval: int = 60

    amount_quote: Decimal
    leverage: int = 10
    interval: str 
    max_records: int 

    grid_max: int
    grid_open: Decimal
    grid_close: Decimal
    mart_open: Decimal
    side: TradeType

    open_order_type: OrderType = OrderType.MARKET
    take_profit_order_type: OrderType = OrderType.MARKET

    signal_func: Optional[Callable]
    signal_func_args: Optional[tuple]
    signal_func_kwargs: Optional[dict]

