import logging
import pandas as pd
from hummingbot.core.event.events import (
    OrderFilledEvent
)
from hummingbot.strategy.script_strategy_base import Decimal, ScriptStrategyBase
from hummingbot.core.data_type import common
from typing import Any, List
from hummingbot.core.data_type.common import OrderType


class MarketTaker(ScriptStrategyBase):
    last_ordered_ts = 0
    #交易间隔
    buy_interval = 60
    #交易对
    trading_pair = {"TRB-USDT"}
    #交易所
    connector_name = "binance_perpetual"
    #止盈比例
    take_profit = Decimal("0.01")
    #加仓间隔比例
    buy_more = Decimal("-0.02")
    #开仓金额
    order_amount_usd = Decimal("20")
    #多仓个数
    long_number = 0
    #空仓个数
    short_number = 0
    #最大多仓个数
    max_long_number = 1
    #最大空仓个数
    max_short_number = 0
    #最小下单数量
    min_amount = 0.1
    markets = {connector_name: trading_pair}
    order_type = OrderType.MARKET
    def on_tick(self):
        if  self.last_ordered_ts < (self.current_timestamp - self.buy_interval):
            try:
                self.trade()
            finally:
                self.last_ordered_ts = self.current_timestamp

    def trade(self):
        #获取当前仓位
        df = self.connectors[self.connector_name].account_positions
        self.long_number = 0
        self.short_number = 0
        for trading_pair in self.trading_pair:
            position_pair = trading_pair
            if position_pair in df:
                amount = Decimal(df[position_pair].amount)
                if amount > 0:
                    self.long_number = self.long_number + 1
                else:
                    self.short_number = self.short_number + 1
        #根据持仓下单
        for trading_pair in self.trading_pair:
            position_pair = trading_pair
            mid_price = self.connectors[self.connector_name].get_mid_price(trading_pair)
            if position_pair in df:
                self.logger().info(logging.INFO, "有仓位，止盈或补仓")
                amount = Decimal(df[position_pair].amount)
                entry_price = Decimal(df[position_pair].entry_price)
                unrealized_pnl = Decimal(df[trading_pair].unrealized_pnl)
                percentage = round(unrealized_pnl / (abs(amount) * entry_price), 4)

                if amount > 0:
                    #止盈单
                    if percentage > self.take_profit:
                        self.sell(self.connector_name, trading_pair, amount , self.order_type, mid_price, common.PositionAction.CLOSE)
                    #补仓单
                    elif percentage < self.buy_more:
                        buymore_amount = self.order_amount_usd / mid_price
                        self.buy(self.connector_name, trading_pair, buymore_amount, self.order_type, mid_price, common.PositionAction.OPEN)
            else:  #如果不存在仓位，开仓
                self.logger().info(logging.INFO, "无仓位，开仓")
                if (self.long_number < self.max_long_number):
                    amount = self.order_amount_usd/mid_price
                    self.buy(self.connector_name, trading_pair, amount, self.order_type, mid_price, common.PositionAction.OPEN)


    def did_fill_order(self, event: OrderFilledEvent):
        """
        Method called when the connector notifies that an order has been partially or totally filled (a trade happened)
        """
        self.logger().info(logging.INFO, f"The order {event.order_id} has been filled")

    def format_status(self) -> str:
        """
        Returns status of the current strategy on user balances and current active orders. This function is called
        when status command is issued. Override this function to create custom status display output.
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        try:
            warning_lines = []
            warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
            positions_df = self.get_positions_df()
            lines.extend(["", "  Positions:"] + ["    " + line for line in positions_df.to_string(index=False).split("\n")])
            orders_df = self.active_orders_df()
            lines.extend(["", "  Active Orders:"] + ["    " + line for line in orders_df.to_string(index=False).split("\n")])
            balance_df = self.get_balance_df()
            lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])
            warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        except ValueError:
            lines.extend(["", "  No active maker orders."])


        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)

    def get_positions_df(self) -> pd.DataFrame:
        """
        Returns a data frame for all asset balances for displaying purpose.
        """
        columns: List[str] = ["Exchange", "Trading Pair", "Amount", "Entry Price" , "Unrealized pnl", "Percentage"]
        data: List[Any] = []
        dc_position = self.connectors[self.connector_name].account_positions
        for trading_pair in dc_position:
            amount = Decimal(dc_position[trading_pair].amount)
            entry_price = Decimal(dc_position[trading_pair].entry_price)
            unrealized_pnl = Decimal(dc_position[trading_pair].unrealized_pnl)
            percentage = round(unrealized_pnl/(abs(amount)*entry_price),4)
            data.append([self.connector_name,
                             trading_pair,
                             amount,
                             entry_price,
                             unrealized_pnl,percentage])
        df = pd.DataFrame(data=data, columns=columns)
        df.sort_values(by=["Exchange", "Trading Pair"], inplace=True)
        return df