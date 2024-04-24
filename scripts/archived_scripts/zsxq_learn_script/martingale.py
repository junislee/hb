import logging
import pandas as pd
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent
)
from hummingbot.strategy.script_strategy_base import Decimal, ScriptStrategyBase
from hummingbot.core.data_type import common
from typing import Any, List
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.utils.async_utils import safe_ensure_future


class Martingale(ScriptStrategyBase):

    last_ordered_ts = 0
    #交易间隔
    buy_interval = 60
    #交易对
    trading_pair = {"CFX-USDT"}
    #交易所
    connector_name = "gate_io_perpetual"
    #止盈比例
    take_profit = Decimal("0.015")
    #加仓间隔比例
    buy_more = Decimal("0.025")
    #开仓金额
    order_amount_usd = Decimal("50")
    #加仓金额倍数
    multiple = Decimal("0.5")
    #加仓次数限制
    limit = Decimal("10")
    #最大持仓金额
    max_amount = Decimal("10000")
    #加仓间距比例
    grid_multiple = Decimal("1.2")

    #多仓个数
    long_number = 0
    #空仓个数
    short_number = 0
    #最大多仓个数
    max_long_number = 1
    #最大空仓个数
    max_short_number = 1

    markets = {connector_name: trading_pair}


    def on_tick(self):

        if  self.last_ordered_ts < (self.current_timestamp - self.buy_interval):
            try:
                self.cancel_all_order()
                self.get_balance()
                self.init = True
            finally:
                self.last_ordered_ts = self.current_timestamp


    def get_balance(self):

        df = self.connectors[self.connector_name].account_positions
        self.long_number = 0
        self.short_number = 0
        for trading_pair in self.trading_pair:
         #   position_pair = trading_pair.replace("-", "")
            position_pair = trading_pair
            if position_pair in df:
                amount = Decimal(df[position_pair].amount)
                if amount > 0:
                    self.long_number = self.long_number + 1
                else:
                    self.short_number = self.short_number + 1

        for trading_pair in self.trading_pair:
            mid_price = Decimal(self.connectors[self.connector_name].get_mid_price(trading_pair))
            position_pair = trading_pair
            if position_pair in df:
                self.logger().info(logging.INFO, "有仓位，挂止盈和补仓单")
                amount = Decimal(df[position_pair].amount)
                entry_price = Decimal(df[position_pair].entry_price)
                #止盈单
                if amount > 0 :
                    #止盈单
                    takeprofit_price = max(entry_price * (1+self.take_profit), Decimal(self.connectors[self.connector_name].get_price(trading_pair, True)))
                    self.sell(self.connector_name, trading_pair, amount , OrderType.LIMIT, takeprofit_price, common.PositionAction.CLOSE)
                    #补仓单
                    buymore_amount = Decimal(abs(amount) * self.multiple)
                    self.adjust_grid(buymore_amount * entry_price)
                    buymore_price = min(Decimal(entry_price * (1 - self.buy_more * self.grid_multiple)), Decimal(self.connectors[self.connector_name].get_price(trading_pair,False)))
                    self.buy(self.connector_name, trading_pair, buymore_amount, OrderType.LIMIT, buymore_price, common.PositionAction.OPEN)
                elif amount < 0 :
                    # 止盈单
                    takeprofit_price = min(entry_price * (1 - self.take_profit),Decimal(self.connectors[self.connector_name].get_price(trading_pair, False)))
                    self.buy(self.connector_name, trading_pair, abs(amount), OrderType.LIMIT,takeprofit_price, common.PositionAction.CLOSE)
                    #补仓单
                    buymore_amount = Decimal(abs(amount) * self.multiple)
                    self.adjust_grid(buymore_amount * entry_price)
                    buymore_price = max(Decimal(entry_price * (1 + self.buy_more * self.grid_multiple)),Decimal(self.connectors[self.connector_name].get_price(trading_pair, True)))

                    self.sell(self.connector_name, trading_pair, buymore_amount,OrderType.LIMIT, buymore_price, common.PositionAction.OPEN)
            else:  #如果不存在仓位，挂一个开仓单
                self.logger().info(logging.INFO, "无仓位，开仓挂单")
                if (self.long_number < self.max_long_number):
                    best_bid = Decimal(self.connectors[self.connector_name].get_price(trading_pair, False))
                    self.buy(self.connector_name, trading_pair, self.order_amount_usd/best_bid, OrderType.LIMIT, best_bid, common.PositionAction.OPEN)
                elif(self.short_number < self.max_short_number):
                    best_ask = Decimal(self.connectors[self.connector_name].get_price(trading_pair, True))
                    self.sell(self.connector_name, trading_pair, self.order_amount_usd/best_ask, OrderType.LIMIT, best_ask, common.PositionAction.OPEN)

    #调整加仓间距，持仓金额越大，加仓间距越大
    def adjust_grid(self,amount):
        if amount > 100 and amount < 500:
            self.grid_multiple = Decimal("1.2")
        elif amount > 500 and amount < 1000:
            self.grid_multiple = Decimal("1.2") * Decimal("1.2")
        elif amount > 1000 and amount < 2000:
            self.grid_multiple = Decimal("1.2") * Decimal("1.2") * Decimal("1.2")
        elif amount > 2000:
            self.grid_multiple = Decimal("1.2") * Decimal("1.2") * Decimal("1.2") * Decimal("1.2")

    def cancel_all_order(self):
         for exchange in self.connectors.values():
             safe_ensure_future(exchange.cancel_all(timeout_seconds=6))

    def did_fill_order(self, event: OrderFilledEvent):
        """
        Method called when the connector notifies that an order has been partially or totally filled (a trade happened)
        """
        self.logger().info(logging.INFO, f"The order {event.order_id} has been filled")

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        """
        Method called when the connector notifies a buy order has been completed (fully filled)
        """
        self.buy_more_times = self.buy_more_times[event.base_asset + event.quote_asset] + 1
        self.init = False
        self.logger().info(f"The buy order {event.order_id} has been completed")

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        """
        Method called when the connector notifies a sell order has been completed (fully filled)
        """
        self.init = False
       # self.buy_more_times[event.base_asset + event.quote_asset] = 0
        self.logger().info(f"The sell order {event.order_id} has been completed")

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