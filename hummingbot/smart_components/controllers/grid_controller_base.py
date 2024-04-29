import time
from decimal import Decimal
from typing import Dict, List, Optional, Set

from pydantic import Field, validator

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType, TradeType
from hummingbot.smart_components.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.smart_components.executors.position_executor.data_types import (
    PositionExecutorConfig,
    TrailingStop,
    TripleBarrierConfig,
)
from hummingbot.smart_components.executors.trailing_grid_executor.data_types import (
    TrailingGridExecutorConfig
)

from hummingbot.smart_components.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction


class GridControllerConfigBase(ControllerConfigBase):
    """
    This class represents the configuration required to run a Directional Strategy.
    """
    markets: Dict[str, Set[str]] = {
    }

    controller_type = "directional_trading"

    position_mode: PositionMode = PositionMode.HEDGE

    ## 策略参数存储
    params: Dict[str, Dict] = {}

    # ===================================
    #  需要新增参数 todo

    def update_markets(self, markets: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        for connector_name in self.markets.keys():
            if connector_name not in markets:
                markets[connector_name] = set()
                for pair in self.markets[connector_name]:
                    markets[connector_name].add(pair)
            else:
                for pair in self.markets[connector_name]:
                    markets[connector_name].add(pair)
        return markets



class GridControllerBase(ControllerBase):
    """
    This class represents the base class for a Directional Strategy.
    """
    def __init__(self, config: GridControllerConfigBase, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config
        self._open = []


    def determine_executor_actions(self) -> List[ExecutorAction]:
        """
        Determine actions based on the provided executor handler report.
        """
        actions = []
        actions.extend(self.create_actions_proposal())
        actions.extend(self.stop_actions_proposal())
        return actions

    async def update_processed_data(self):
        """
        Update the processed data based on the current state of the strategy.
        """
        signal = self.get_signal()
        self.processed_data = {"signal": signal}

    def get_signal(self) -> int:
        """
        Get the signal for the strategy.
        """
        raise NotImplementedError


    def create_actions_proposal(self) -> List[ExecutorAction]:
        """
        Create actions based on the provided executor handler report.
        """
        create_actions = []
        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:
                if trading_pair not in self._open:
                    self.logger().info(
                        f"{connector_name}-{trading_pair}进入逻辑，开启网格"
                    )
                    create_actions.append(CreateExecutorAction(
                        executor_config=TrailingGridExecutorConfig(
                            timestamp=self.current_timestamp,
                            leverage=self.config.leverage,
                            trading_pair=trading_pair,
                            connector_name=connector_name,
                            side=TradeType.BUY,
                            amount_quote=self.config.order_amount_quote,
                            trailing_grid_config=self.config,
                            signal_func=self.grid_siganl,
                            signal_func_args=(),
                            signal_func_kwargs={'k': 17, 'd': 4}
                        )))
                    self._open.append(trading_pair)
        return create_actions


    def change_candles(self):
        # todo
        ## 关闭不需要的candles

        ## 启动需要的candles
        for candles_config in self.config.candles_config:
            self.market_data_provider.initialize_candles_feed(candles_config)


    def stop_actions_proposal(self) -> List[ExecutorAction]:
        """
        Stop actions based on the provided executor handler report.
        """
        stop_actions = []
        stop_actions.extend(self.executors_to_stop())
        return stop_actions

    def executors_to_stop(self):
        # todo
        executors_to_stop = []
        for ex in self.executors_info:
            if ex.config.markets not in self.config.markets:
                executors_to_stop.append(ex)

        return [StopExecutorAction(
            controller_id=self.config.id,
            executor_id=executor.id) for executor in executors_to_stop]

    # todo
    def update_config(self, new_config: ControllerConfigBase):
        """
        Update the controller configuration. With the variables that in the client_data have the is_updatable flag set
        to True. This will be only available for those variables that don't interrupt the bot operation.
        """
        for field in self.config.__fields__.values():
            client_data = field.field_info.extra.get("client_data")
            if client_data and client_data.is_updatable:
                setattr(self.config, field.name, getattr(new_config, field.name))

    # ===============================================================================
    def can_create_executor(self, signal: int) -> bool:
        """
        Check if an executor can be created based on the signal, the quantity of active executors and the cooldown time.
        """
        active_executors_by_signal_side = self.filter_executors(
            executors=self.executors_info,
            filter_func=lambda x: x.is_active and (x.side == TradeType.BUY if signal > 0 else TradeType.SELL))
        max_timestamp = max([executor.timestamp for executor in active_executors_by_signal_side], default=0)
        active_executors_condition = len(active_executors_by_signal_side) < self.config.max_executors_per_side
        cooldown_condition = time.time() - max_timestamp > self.config.cooldown_time
        return active_executors_condition and cooldown_condition



    def get_executor_config(self, trade_type: TradeType, price: Decimal, amount: Decimal):
        """
        Get the executor config based on the trade_type, price and amount. This method can be overridden by the
        subclasses if required.
        """
        return PositionExecutorConfig(
            timestamp=time.time(),
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            side=trade_type,
            entry_price=price,
            amount=amount,
            triple_barrier_config=self.config.triple_barrier_config,
            leverage=self.config.leverage,
        )
