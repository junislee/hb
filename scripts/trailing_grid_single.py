import logging
from decimal import Decimal
from typing import List, Dict
import pandas as pd
from datetime import datetime
import pandas_ta as ta
import numpy as np
import time
import json
import requests

from hummingbot.client.ui.interface_utils import format_df_for_printout
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType, PriceType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent, BuyOrderCreatedEvent, SellOrderCreatedEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig, CandlesFactory

class TrailingGrid(ScriptStrategyBase):

    trading_pair = "ETH-USDT"
    exchange = "binance_perpetual"
    # 1m 3m 5m 15m 1h
    # k线
    interval = "1m"
    # Here you can use for example the LastTrade price to use in your strategy
    price_source = PriceType.BestBid

    # 配置k和d值
    # Smart sto的参数
    k: int = 17
    d: int = 4

    # 面板报告打印间隔
    # 主交易逻辑执行间隔
    # 注意：主交易执行间隔<= interval
    report_interval = 60 * 3
    executor_interval = 60 

    # 定义参数
    # 下单数量和网格设置
    amount_usd = Decimal(600)
    grid_max = Decimal(100)
    grid_open = Decimal(0.02) # 开仓保护
    grid_close = Decimal(0.01) # 止盈距离
    mart_open = Decimal(0.25) # 马丁开仓保护


    commission_rate = Decimal(0.04 / 100)  # 手续费(%)

    # 杠杆
    leverage: int = 10

    columns_to_show = ["trading_pair", "timestamp", "long", "short", "SMI", "SMIsignal", "open", "high", "low", "close"]

    markets = {exchange: {trading_pair}}
    candles = [CandlesFactory.get_candle(CandlesConfig(connector=exchange, trading_pair=trading_pair, interval=interval, max_records=200))]

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        # Is necessary to start the Candles Feed.
        super().__init__(connectors)
        self.last_time_reported = 0
        self.last_executor_time = 0
        self.set_leverage_flag = None
        self.position_mode: PositionMode = PositionMode.HEDGE
        self.last_signal_time = 0
        self.last_order_id = None

        self.sizes = []
        self.prices = []
        self.avg_price = Decimal(0.0)
        self.profit = Decimal(0.0)
        self.commission = Decimal(0.0)

        for candle in self.candles:
            candle.start()
            
    @staticmethod
    def send_msg(content):
        """艾特全部，并发送指定信息"""
        wx_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=6c47c115-0cf0-4c56-8386-d0ab71acb617"
        data = json.dumps({"msgtype": "text", "text": {"content": content}})
        r = requests.post(wx_url, data, auth=('Content-Type', 'application/json'))

    @property
    def all_candles_ready(self):
        """
        Checks if the candlesticks are full.
        """
        return all([candle.ready for candle in self.candles])

    @property
    def is_perpetual(self):
        """
        Checks if the exchange is a perpetual market.
        """
        return "perpetual" in self.exchange
    
    @property
    def open_trades(self):
        return len(self.sizes)

    @property
    def total_size(self):
        return sum(self.sizes)

    @property
    def nominal_value(self):
        if self.avg_price == 0.0:
            return Decimal(0)
        return self.avg_price * self.total_size

    @property
    def min_price(self):
        if len(self.sizes) > 0:
            return min(self.prices)
        return Decimal(0.0)
    
    def on_stop(self):
        for candle in self.candles:
            candle.stop()

    def on_tick(self):
        if self.is_perpetual:
            self.check_and_set_leverage()

        if self.all_candles_ready and (self.current_timestamp - self.last_time_reported > self.report_interval):
            self.last_time_reported = self.current_timestamp
            self.notify_hb_app(self.get_formatted_market_analysis())

        if  (self.current_timestamp - self.last_executor_time > self.executor_interval):
            self.last_executor_time = self.current_timestamp

            proposal = []
            signal = self.get_signal()
            if self.last_signal_time != signal[signal['trading_pair']==self.trading_pair]['timestamp'].iloc[-2]:
                self.logger().info(
                    f"信号更新"
                )
                self.last_signal_time = signal[signal['trading_pair']==self.trading_pair]['timestamp'].iloc[-2]

                latest_close = Decimal(self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source))
                min_price = self.min_price  # 从Grid对象获取最小价格
                num = self.open_trades  # 获取当前开仓数量
                msg = (f"进行策略判断循环,sizes:{num},min_price:{round(min_price, 2)},best_bid:{round(latest_close, 2)},size_sum:{self.total_size},avg_price:{self.avg_price}\
                       timestamp:{signal[signal['trading_pair']==self.trading_pair]['timestamp'].iloc[-2]},\
                       long:{signal[signal['trading_pair']==self.trading_pair]['long'].iloc[-2]},short:{signal[signal['trading_pair']==self.trading_pair]['short'].iloc[-2]},\
                       open:{signal[signal['trading_pair']==self.trading_pair]['open'].iloc[-2]},high:{signal[signal['trading_pair']==self.trading_pair]['high'].iloc[-2]},\
                        low:{signal[signal['trading_pair']==self.trading_pair]['low'].iloc[-2]},close:{signal[signal['trading_pair']==self.trading_pair]['close'].iloc[-2]}")
                self.logger().info(
                        msg
                    )
                self.send_msg(msg)

                # 检查是否符合开仓条件
                # 开仓条件
                if num == 0   and signal[signal['trading_pair']==self.trading_pair]['long'].iloc[-2]:
                    ## 开仓
                    msg = (f"信号符合，开仓")
                    self.send_msg(msg)
                    self.notify_hb_app_with_timestamp(msg)
                    self.open_position(price=latest_close, trading_pair=self.trading_pair, 
                                                        amount_asset=self.amount_usd / latest_close)

                # 检查是否符合加仓条件
                # 加仓条件
                if num > 0   and signal[signal['trading_pair']==self.trading_pair]['long'].iloc[-2] and \
                    (latest_close - min_price) / min_price < -self.grid_open and num < self.grid_max:
                    ## 加仓
                    msg = (f"信号符合，加仓")
                    self.send_msg(msg)
                    self.notify_hb_app_with_timestamp(msg)
                    self.incr_position(price=latest_close, trading_pair=self.trading_pair)

                # 检查是否符合平仓条件
                if num == 1   and signal[signal['trading_pair']==self.trading_pair]['short'].iloc[-2] and \
                    (latest_close - min_price) / min_price > self.grid_close:
                    ## 平仓
                    msg = (f"信号符合，平仓")
                    self.send_msg(msg)
                    self.notify_hb_app_with_timestamp(msg)
                    self.close_position(price=latest_close, trading_pair=self.trading_pair)

                # 检查是否符合减仓条件
                if num > 1   and signal[signal['trading_pair']==self.trading_pair]['short'].iloc[-2] and \
                    (latest_close - min_price) / min_price > self.grid_close:
                    ## 减仓
                    msg = (f"信号符合，减仓")
                    self.send_msg(msg)
                    self.notify_hb_app_with_timestamp(msg)
                    self.decr_position(price=latest_close, trading_pair=self.trading_pair)
                    
                self.cancel_all_orders()
                # if len(proposal) > 0:
                #     self.execute_orders_proposal(proposal)
            else:
                pass

    def open_position(self, price, trading_pair, amount_asset):
        # buy_order = OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
        #                     order_side=TradeType.BUY, amount=amount_asset, price=price)
        # return buy_order

        # Order_type修改订单模式，LIMIT还是MARKET
        # 如果是LIMIT模式
        buy_order = self.buy(connector_name=self.exchange,
                                trading_pair=trading_pair, 
                                order_type=OrderType.MARKET,
                                amount=amount_asset, 
                                price=price,
                                position_action=PositionAction.OPEN)
        return buy_order

    def incr_position(self, price, trading_pair):

        min_price = self.min_price
        roe = (price - min_price) / min_price if min_price else 0
        
        # 判断是否根据mart_open调整大小，如果roe小于-mart_open，使用特定的计算方式
        size = (self.avg_price * self.total_size / price) if roe < -self.mart_open else self.amount_usd / price
        
        # buy_order = OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
        #                     order_side=TradeType.BUY, amount=size, price=price)
        # return buy_order
        buy_order = self.buy(connector_name=self.exchange,
                        trading_pair=trading_pair, 
                        order_type=OrderType.MARKET,
                        amount=size, 
                        price=price,
                        position_action=PositionAction.OPEN)
        return buy_order
    
    def close_position(self, price, trading_pair):

        if  len(self.sizes) == 0:
            print("No open positions to close.")
            return
        self.sizes = [] # 移除成交量
        self.prices = []  # 移除对应的价格
        self.avg_price = Decimal(0.0)  # 重置平均价格
        self.profit = Decimal(0.0)  # 重置利润
        self.commission = Decimal(0.0)  # 重置佣金
        for trading_pair, position in self.connectors[self.exchange].account_positions.items():
            sell_order = self.sell(connector_name=self.exchange,
                                    trading_pair=position.trading_pair, 
                                    order_type=OrderType.MARKET,
                                    amount=position.amount, 
                                    price=price,
                                    position_action=PositionAction.CLOSE)
        return sell_order

    def decr_position(self, price, trading_pair):
        num = self.open_trades
        size = Decimal(0.0)
        if not self.avg_price or self.avg_price == 0:
            return  # 防止除以0

        roe = (price - self.avg_price) / self.avg_price
        # 检查是否满足平仓条件
        if (self.nominal_value * roe + self.profit) / self.nominal_value > self.grid_close:
            self.sizes = []
            self.prices = []
            self.avg_price = Decimal(0.0)
            self.profit = Decimal(0.0)

            for trading_pair, position in self.connectors[self.exchange].account_positions.items():
                sell_order = self.sell(connector_name=self.exchange,
                                        trading_pair=position.trading_pair, 
                                        order_type=OrderType.MARKET,
                                        amount=position.amount, 
                                        price=price,
                                        position_action=PositionAction.CLOSE)
        else:
            amount = Decimal(0.0)
            for i in range(num - 1, -1, -1):
                open_price = self.prices[i]
                if (price - open_price) / open_price > self.grid_close:

                    size += self.sizes.pop(i)
                    
                    amount += size * self.prices.pop(i)

            comm = size * price * self.commission_rate
            self.profit += size * (price - self.avg_price) - comm
            self.commission += comm

            if len(self.sizes) > 0:
                self.logger().info(
                f'{{"symbol":"{trading_pair}",减仓"}}'
                )
                for trading_pair, position in self.connectors[self.exchange].account_positions.items():
                    sell_order = self.sell(connector_name=self.exchange,
                            trading_pair=position.trading_pair, 
                            order_type=OrderType.MARKET,
                            amount=size, 
                            price=price,
                            position_action=PositionAction.CLOSE)
                
            else:
                self.avg_price = Decimal(0.0)
                self.profit = Decimal(0.0)
                self.logger().info(
                    f"异常平仓"
                )
                for trading_pair, position in self.connectors[self.exchange].account_positions.items():
                    sell_order = self.sell(connector_name=self.exchange,
                                            trading_pair=position.trading_pair, 
                                            order_type=OrderType.MARKET,
                                            amount=position.amount, 
                                            price=price,
                                            position_action=PositionAction.CLOSE) 
        return sell_order

    def execute_orders_proposal(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.exchange):
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
        self.send_msg(msg)
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
        if event.trade_type == TradeType.BUY:
            size = event.amount 
            self.avg_price = (self.avg_price * self.total_size + size * event.price) / (self.total_size + size)
            comm = event.amount *event.price * self.commission_rate
            self.commission += comm
            self.profit -= comm
            self.sizes.append(size)
            self.prices.append(event.price)
            self.last_order_id = event.order_id

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        msg = (f"Created BUY order {event.order_id},{event.type},{round(event.price, 2)},{round(event.amount, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
        self.send_msg(msg)

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        msg = (f"Created SELL order {event.order_id},{event.type},{round(event.price, 2)},{round(event.amount, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
        self.send_msg(msg)

    def get_formatted_market_analysis(self):
        SMI_metrics_df = self.get_market_analysis()
        volatility_metrics_pct_str = format_df_for_printout(
            SMI_metrics_df[self.columns_to_show],
            table_format="psql")
        return volatility_metrics_pct_str

    def get_signal(self):
        # market_metrics = {}
        merged_df = pd.DataFrame()
        for candle in self.candles:
            df = candle.candles_df
            df["trading_pair"] = self.trading_pair
            df["interval"] = self.interval
            df['timestamp'] = df['timestamp'].apply(lambda x:datetime.fromtimestamp(x / 1000.0))
            df = df.sort_values(by='timestamp')

            ll = df['low'].rolling(window=self.k).min()
            hh = df['high'].rolling(window=self.k).max()
            diff = hh - ll
            rdiff = df['close'] - (hh + ll) / 2
            avgrel = ta.ema(ta.ema(rdiff, length=self.d), self.d)
            avgdiff = ta.ema(ta.ema(diff, length=self.d), self.d)

            df['ll'] = ll
            df['hh'] = hh
            df['avgrel'] = avgrel
            df['avgdiff'] = avgdiff

            df['SMI'] = ((avgrel* 100) / (avgdiff / 2) ) 
            df['SMIsignal'] = ta.ema(df['SMI'], length=self.d)

            df['long'] = ta.cross(df['SMI'], df['SMIsignal'], above=True)
            df['short'] = ta.cross(df['SMI'], df['SMIsignal'], above=False)
            merged_df = pd.concat([merged_df, df.iloc[-20:]])

        merged_df.reset_index(drop=True, inplace=True)
        return merged_df
    
    def get_market_analysis(self):

        return self.get_signal()

    def check_and_set_leverage(self):
        if not self.set_leverage_flag:
            for connector in self.connectors.values():
                for trading_pair in connector.trading_pairs:
                    connector.set_position_mode(self.position_mode)
                    connector.set_leverage(trading_pair=trading_pair, leverage=self.leverage)
            self.set_leverage_flag = True


    def format_status(self) -> str:
        if self.all_candles_ready():
            lines = []
            lines.extend(["", "SMI Metrics", ""])
            lines.extend([self.get_formatted_market_analysis()])
            return "\n".join(lines)
        else:
            return "Candles not ready yet!"