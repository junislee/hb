import time
from decimal import Decimal
from typing import Dict, List, Optional, Set

from pydantic import Field, validator
import asyncio
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

    @staticmethod
    def gen_executor_signal():
        """
        这个方法需要实现, 传递给executor
        """
        raise NotImplementedError

    ## 将str side转换为对应内部数据格式
    @validator('params', pre=True, always=True)
    def parse_params_config(cls, v) -> Dict[str, Dict]:
        if isinstance(v, Dict):
            for pair, value in v.items():
                if value["side"] == "BUY":
                    v[pair]["side"] = TradeType.BUY
                elif value["side"] == "SELL":
                    v[pair]["side"] = TradeType.SELL
                else:
                    raise ValueError
            return v
        else:
            raise TypeError
        
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
        self.stop_queue = asyncio.Queue()
        self.listen_to_executor_stop_task: asyncio.Task = asyncio.create_task(self.listen_to_executor_stop())


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

    def on_stop(self):
        self.listen_to_executor_stop_task.cancel()

    async def listen_to_executor_stop(self):
        """
        Asynchronously listen to actions from the controllers and execute them.
        """
        while True:
            try:
                ## 在这里收到executor shutdown事件后，关闭对应pair的数据
                pair = await self.stop_queue.get()
                self.market_data_provider.candles_feeds[pair].stop()
            except Exception as e:
                self.logger().error(f"Error executing action: {e}", exc_info=True)

    def create_actions_proposal(self) -> List[ExecutorAction]:
        """
        Create actions based on the provided executor handler report.
        """
        create_actions = []
        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:
                if self.processed_data["signal"] == 1 and trading_pair not in self._open:
                    self.logger().info(
                        f"{connector_name}-{trading_pair}进入逻辑，开启网格"
                    )
                    create_actions.append(CreateExecutorAction(
                        executor_config=TrailingGridExecutorConfig(
                            trading_pair=trading_pair,
                            connector_name=connector_name,
                            signal_func=self.config.gen_executor_signal,
                            signal_func_args=(),
                            signal_func_kwargs=self.config[trading_pair]["signal_func_args"],
                            stop_queue=self.stop_queue,
                            **self.config[trading_pair]
                        )))
                    self._open.append(trading_pair)
        return create_actions


    def change_candles(self):
        # todo
        ## 启动需要的candles
        for connector_name, pairs in self.config.markets.items():
            for pair in pairs:
                if pair not in [ex.config.trading_pair for ex in self.executors_info if ex.config.connector_name == connector_name]:
                    self.market_data_provider.initialize_candles_feed(pair)


    def stop_actions_proposal(self) -> List[ExecutorAction]:
        """
        Stop actions based on the provided executor handler report.
        """
        stop_actions = []
        stop_actions.extend(self.executors_to_stop())
        return stop_actions

    def executors_to_stop(self):
        executors_to_stop = []
        for ex in self.executors_info:
            if ex.config.trading_pair not in self.config.markets[ex.config.connector_name]:
                executors_to_stop.append(ex)
                self._open.remove(ex.config.trading_pair)

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
            setattr(self.config, field.name, getattr(new_config, field.name))
        ## 在这里切换币对？
        self.change_candles()

