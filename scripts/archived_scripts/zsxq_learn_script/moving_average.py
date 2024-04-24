from collections import deque
from decimal import Decimal
from statistics import mean
from hummingbot.core.data_type.common import OrderType
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class MovingAverage(ScriptStrategyBase):
    exchange = "binance_paper_trade"
    trading_pair = "BTC-USDT"
    markets = {exchange: {trading_pair}}
    amount = Decimal(0.01)
    de_fast_ma = deque([], maxlen=5)
    de_mid_ma = deque([], maxlen=10)
    de_slow_ma = deque([], maxlen=60)
    pingpong = 0

    def on_tick(self):
        price = self.connectors[self.exchange].get_price(self.trading_pair, True)

        self.de_fast_ma.append(price)
        self.de_mid_ma.append(price)
        self.de_slow_ma.append(price)
        fast_ma = mean(self.de_fast_ma)
        mid_ma = mean(self.de_mid_ma)
        slow_ma = mean(self.de_slow_ma)

        if ((fast_ma > mid_ma) & (mid_ma > slow_ma)) & (self.pingpong == 0):
            self.buy(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=self.amount,
                order_type=OrderType.MARKET,
            )
            self.logger().info(f'{"发现开仓信号，买入"}')
            self.pingpong = 1
        elif (slow_ma > fast_ma) & (self.pingpong == 1):
            self.sell(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=self.amount,
                order_type=OrderType.MARKET,
            )
            self.logger().info(f'{"发现卖出信号，卖出"}')
            self.pingpong = 0

        else:
            self.logger().info(f'{"等待交易信号"}')
