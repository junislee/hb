import os
from decimal import Decimal
from typing import Dict, List, Optional, Set
from datetime import datetime
import pandas_ta as ta  # noqa: F401
from pydantic import Field, validator

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.clock import Clock
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType, TradeType
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.smart_components.executors.trailing_grid_executor.data_types import (
    TrailingGridExecutorConfig, TrailingGridConfig
)
from hummingbot.smart_components.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy.strategy_v3_base import StrategyV3Config, StrategyV3Base


class StrategyControllersCompo(StrategyV3Config):

    controller_type: str = "market_making"

    controller_name: str = "grid"

    controllers_config: str = ["trailing_grid_v1.yaml"]

    para_config: str = "grid_config.yaml"

    config_update_interval: int = 60




class TrailingGridMultPairs(StrategyV3Base):
    account_config_set = False

    def __init__(self, connectors: Dict[str, ConnectorBase], config: StrategyControllersCompo):
        super().__init__(connectors, config)
        self.config = config

    def start(self, clock: Clock, timestamp: float) -> None:
        """
        Start the strategy.
        :param clock: Clock to use.
        :param timestamp: Current time.
        """
        self._last_timestamp = timestamp
        # self.apply_initial_setting()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        '''
        这里的逻辑是, 针对每一个新的币对, 运行对应参数的trailing_grid executor
        (对应选币逻辑、多空对冲逻辑、以及参数实时更新)
        注意, 信号+网格并不在这里运行, 而是从这里传入信号函数给信号网格executor
        网格executor实现了网格变种，通过配置TrailingGridExecutorConfig不同参数
        选择多、空、中性、带底仓自平衡、trailing等
        '''
        create_actions = []
        return create_actions

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        '''
        与上面创建的逻辑对应, 这里进行币对信号网格的停止逻辑
        '''
        stop_actions = []
        # for connector_name, trading_pairs in self.config.markets.items():
        #     for trading_pair in trading_pairs:
        return stop_actions


    def apply_initial_setting(self):
        if not self.account_config_set:
            for connector_name, connector in self.connectors.items():
                if self.is_perpetual(connector_name):
                    connector.set_position_mode(self.config.position_mode)
                    for trading_pair in self.market_data_provider.get_trading_pairs(connector_name):
                        connector.set_leverage(trading_pair, self.config.leverage)
            self.account_config_set = True
