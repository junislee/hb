
from typing import Dict, List, Optional, Set
import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Set
import pandas_ta as ta

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
                asyncio.sleep(5)
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
                            timestamp=float(0.0),
                            trading_pair=trading_pair,
                            connector_name=connector_name,
                            signal_func=gen_executor_signal,
                            signal_func_args=(),
                            stop_queue=self.stop_queue,
                            params=self.config.params
                        )))
                    self._open.append(trading_pair)
        return create_actions


    async def change_candles(self):
        ## 启动需要的candles
        for connector_name, pairs in self.config.markets.items():
            for pair in pairs:
                if pair not in [ex.config.trading_pair for ex in self.executors_info if ex.config.connector_name == connector_name]:
                    self.market_data_provider.initialize_candles_feed(pair)
                    await self.actions_queue.put((pair, self.config[pair]["leverage"]))


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


    async def update_config(self, new_config: ControllerConfigBase):
        """
        Update the controller configuration. With the variables that in the client_data have the is_updatable flag set
        to True. This will be only available for those variables that don't interrupt the bot operation.
        """
        for field in self.config.__fields__.values():
            setattr(self.config, field.name, getattr(new_config, field.name))
        ## 在这里切换币对？
        await self.change_candles()

