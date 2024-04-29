import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Set
import pandas_ta as ta

from pydantic import Field

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.smart_components.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)
from hummingbot.smart_components.controllers.grid_controller_base import (
    GridControllerBase,
    GridControllerConfigBase,
)
from hummingbot.smart_components.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.smart_components.models.executor_actions import ExecutorAction, StopExecutorAction


class GridConfig(GridControllerConfigBase):
    controller_name = "Grid_test"

    markets: Dict[str, Set[str]] = {
        "binance_perpetual": set(
            ["DOGE-USDT", "WIF-USDT", "ONG-USDT"]
        )
    }

    def gen_executor_signal(df=None, k: int = 0, d: int = 0):
        '''
        这里构建网格叠加的信号，如果没有具体则是常规网格
        '''
        df['timestamp'] = df['timestamp'].apply(lambda x: datetime.fromtimestamp(x / 1000.0))
        df = df.sort_values(by='timestamp')

        ll = df['low'].rolling(window=k).min()
        hh = df['high'].rolling(window=k).max()
        diff = hh - ll
        rdiff = df['close'] - (hh + ll) / 2
        avgrel = ta.ema(ta.ema(rdiff, length=d), d)
        avgdiff = ta.ema(ta.ema(diff, length=d), d)

        df['ll'] = ll
        df['hh'] = hh
        df['avgrel'] = avgrel
        df['avgdiff'] = avgdiff

        df['SMI'] = ((avgrel * 100) / (avgdiff / 2))
        df['SMIsignal'] = ta.ema(df['SMI'], length=d)

        df['long'] = ta.cross(df['SMI'], df['SMIsignal'], above=True)
        df['short'] = ta.cross(df['SMI'], df['SMIsignal'], above=False)
        df['signal'] = 0  # 初始化 signal 列为0

        # 根据条件为 signal 赋值
        df.loc[(df['long'] == 1) & (df['short'] == 0), 'signal'] = 1
        df.loc[(df['long'] == 0) & (df['short'] == 0), 'signal'] = 0
        df.loc[(df['long'] == 0) & (df['short'] == 1), 'signal'] = -1
        return df.iloc[-2]


class GridController(GridControllerBase):
    def __init__(self, config: GridConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config

    def get_signal(self) -> int:
        ## controller内部执行逻辑
        return 1

    # ===================================================================================================================
    def first_level_refresh_condition(self, executor):
        if self.config.top_order_refresh_time is not None:
            if self.get_level_from_level_id(executor.custom_info["level_id"]) == 1:
                return time.time() - executor.timestamp > self.config.top_order_refresh_time
        return False

    def order_level_refresh_condition(self, executor):
        return time.time() - executor.timestamp > self.config.executor_refresh_time

    def executors_to_refresh(self) -> List[ExecutorAction]:
        executors_to_refresh = self.filter_executors(
            executors=self.executors_info,
            filter_func=lambda x: not x.is_trading and x.is_active and (self.order_level_refresh_condition(x) or self.first_level_refresh_condition(x)))
        return [StopExecutorAction(
            controller_id=self.config.id,
            executor_id=executor.id) for executor in executors_to_refresh]

    def get_executor_config(self, level_id: str, price: Decimal, amount: Decimal):
        trade_type = self.get_trade_type_from_level_id(level_id)
        return PositionExecutorConfig(
            timestamp=time.time(),
            level_id=level_id,
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            entry_price=price,
            amount=amount,
            triple_barrier_config=self.config.triple_barrier_config,
            leverage=self.config.leverage,
            side=trade_type,
        )
