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
from hummingbot.strategy.strategy_v2_base_trailing_grid import StrategyV2ConfigTrailing, StrategyV2Trailing


class TrailingGridMultPairsConfig(StrategyV2ConfigTrailing):

    markets: Dict[str, Set[str]] = {
        "binance_perpetual": set(
            ["WIF-USDT", "DOGE-USDT", "ONG-USDT"]
        )
    }

    candles_config: List[CandlesConfig] = []
    '''
    [CandlesConfig(
                    connector="binance_perpetual",
                    trading_pair="ETH-USDT",
                    interval="1m",
                    max_records=500
                )]
    '''
    controllers_config: List[str] = []

    config_update_interval: int = 60

    # K candles的时间间隔, max_records是与markets配合使用的
    interval: str = "1m"
    order_amount_quote: Decimal = 50
    leverage: int = 10
    position_mode: PositionMode = PositionMode.HEDGE

    # 信号参数配置
    k: int = 17
    d: int = 4


    @property
    def trailing_grid_config(self) -> TrailingGridConfig:
        return TrailingGridConfig(
            grid_max=100,
            grid_open=0.02,
            grid_close=0.01,
            mart_open=0.10,
            open_order_type=OrderType.MARKET,
            take_profit_order_type=OrderType.MARKET
        )


def grid_siganl(df=None, k: int=0, d: int=0):
    '''
    这里构建网格叠加的信号，如果没有具体则是常规网格
    '''
    df['timestamp'] = df['timestamp'].apply(lambda x:datetime.fromtimestamp(x / 1000.0))
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

    df['SMI'] = ((avgrel* 100) / (avgdiff / 2) ) 
    df['SMIsignal'] = ta.ema(df['SMI'], length=d)

    df['long'] = ta.cross(df['SMI'], df['SMIsignal'], above=True)
    df['short'] = ta.cross(df['SMI'], df['SMIsignal'], above=False)
    df['signal'] = 0  # 初始化 signal 列为0

    # 根据条件为 signal 赋值
    df.loc[(df['long'] == 1) & (df['short'] == 0), 'signal'] = 1
    df.loc[(df['long'] == 0) & (df['short'] == 0), 'signal'] = 0
    df.loc[(df['long'] == 0) & (df['short'] == 1), 'signal'] = -1
    return df.iloc[-2]

class TrailingGridMultPairs(StrategyV2Trailing):
    account_config_set = False

    def __init__(self, connectors: Dict[str, ConnectorBase], config: TrailingGridMultPairsConfig):
        if len(config.candles_config) == 0:
            self.max_records = config.k + 20
            for connector_name, trading_pairs in config.markets.items():
                for trading_pair in trading_pairs:
                    config.candles_config.append(CandlesConfig(
                        connector=connector_name,
                        trading_pair=trading_pair,
                        interval=config.interval,
                        max_records=self.max_records
                    ))
        super().__init__(connectors, config)
        self.config = config

        self._open: List[str] = []

    def start(self, clock: Clock, timestamp: float) -> None:
        """
        Start the strategy.
        :param clock: Clock to use.
        :param timestamp: Current time.
        """
        self._last_timestamp = timestamp
        self.apply_initial_setting()



    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        '''
        这里的逻辑是, 针对每一个新的币对, 运行对应参数的trailing_grid executor
        (对应选币逻辑、多空对冲逻辑、以及参数实时更新)
        注意, 信号+网格并不在这里运行, 而是从这里传入信号函数给信号网格executor
        网格executor实现了网格变种，通过配置TrailingGridExecutorConfig不同参数
        选择多、空、中性、带底仓自平衡、trailing等
        '''
        create_actions = []

        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:

                if trading_pair not in self._open:
                    self.logger().info(
                        f"{trading_pair}进入多头逻辑，开启网格"
                    )
                    create_actions.append(CreateExecutorAction(
                        executor_config=TrailingGridExecutorConfig(
                            timestamp=self.current_timestamp,
                            leverage=self.config.leverage,
                            trading_pair=trading_pair,
                            connector_name=connector_name,
                            side=TradeType.BUY,
                            amount_quote=self.config.order_amount_quote,
                            trailing_grid_config= self.config.trailing_grid_config,
                            signal_func=grid_siganl,
                            signal_func_args=(),
                            signal_func_kwargs={'k':17, 'd':4}
                        )))
                    self._open.append(trading_pair)
        return create_actions

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        '''
        与上面创建的逻辑对应, 这里进行币对信号网格的停止逻辑
        '''
        stop_actions = []
        # for connector_name, trading_pairs in self.config.markets.items():
        #     for trading_pair in trading_pairs:


        return stop_actions


    # def get_signal(self, connector_name: str, trading_pair: str) -> Optional[float]:
    #     '''
    #     这里可以构造选币、多空开关
    #     '''
    #     candles = self.market_data_provider.get_candles_df(connector_name, trading_pair, self.config.interval, self.max_records)


    #     return candles.iloc[-1]["signal"] if not candles.empty else None

    def apply_initial_setting(self):
        if not self.account_config_set:
            for connector_name, connector in self.connectors.items():
                if self.is_perpetual(connector_name):
                    connector.set_position_mode(self.config.position_mode)
                    for trading_pair in self.market_data_provider.get_trading_pairs(connector_name):
                        connector.set_leverage(trading_pair, self.config.leverage)
            self.account_config_set = True
