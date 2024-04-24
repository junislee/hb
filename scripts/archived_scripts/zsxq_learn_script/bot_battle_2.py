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
from hummingbot.core.data_type.common import OrderType,TradeType


class BotBattle2(ScriptStrategyBase):
    last_ordered_ts = 0
    #交易间隔
    buy_interval = 60
    #交易对
    trading_pair = {"TRB-USDT","BLZ-USDT"}
    #交易所
    connector_name = "binance_perpetual"
    take_profit = Decimal("0.01")
    #加仓间隔比例
    buy_more = Decimal("0.01")
    times = {}
    grid_increment = Decimal("1.2")
    amount_increment = Decimal("1.2")
    #开仓金额
    order_amount_usd = Decimal("5")
    #最大分批止盈金额
    max_take_profit_amount_usd = Decimal("20")
    #多仓个数
    long_number = 0
    #空仓个数
    short_number = 0
    #最大多仓个数
    max_long_number = 1
    #最大空仓个数
    max_short_number = 0
    #最大加仓次数
    max_times = 10
    #最小下单数量
    min_amount = {"BLZ-USDT": Decimal("23"), "TRB-USDT": Decimal("0.1"), "ETH-USDT": Decimal("0.001"), "LOOM-USDT": Decimal("10")}
    markets = {connector_name: trading_pair}
    order_type = OrderType.LIMIT_MAKER
    price_grid = {}
    batch_close = False
    init = False
    def on_tick(self):
        if  self.last_ordered_ts < (self.current_timestamp - self.buy_interval):
            try:
                self.cancel_all_order()
                self.get_balance()
            finally:
                self.last_ordered_ts = self.current_timestamp

    def get_balance(self):
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
        for trading_pair in self.trading_pair:
            position_pair = trading_pair
            if position_pair in df:
                self.logger().info(logging.INFO, "有仓位，挂止盈和补仓单")
                amount = Decimal(df[position_pair].amount)
                entry_price = Decimal(df[position_pair].entry_price)
                if self.init == False:
                    self.times[trading_pair] = Decimal(round((amount*entry_price) / (self.order_amount_usd*3) ,0))

                if amount > 0 and self.times[trading_pair] <= self.max_times :
                    #止盈单
                    takeprofit_price = max(entry_price * (1+self.take_profit  ), Decimal(self.connectors[self.connector_name].get_price(trading_pair, True)))
                    takeprofit_amount = self.order_amount_usd/takeprofit_price * 2
                    if (amount - takeprofit_amount) < self.min_amount[position_pair] or self.batch_close == False:
                        takeprofit_amount = amount
                    self.sell(self.connector_name, trading_pair, takeprofit_amount , self.order_type, takeprofit_price, common.PositionAction.CLOSE)
                    #补仓单
                    buymore_price = min(Decimal(entry_price * (1 - self.buy_more * (pow(self.grid_increment , self.times[trading_pair])))), Decimal(self.connectors[self.connector_name].get_price(trading_pair,False)))
                    buymore_amount = max(self.order_amount_usd * (pow(self.amount_increment , self.times[trading_pair])) /buymore_price, self.min_amount[position_pair])
                    self.buy(self.connector_name, trading_pair, buymore_amount, self.order_type, buymore_price, common.PositionAction.OPEN)
            else:  #如果不存在仓位，挂一个开仓单
                self.times[trading_pair] = 0
                self.logger().info(logging.INFO, "无仓位，开仓挂单")
                if (self.long_number < self.max_long_number):
                    best_bid = Decimal(self.connectors[self.connector_name].get_price(trading_pair, False))
                    self.buy(self.connector_name, trading_pair, max(round(self.order_amount_usd/best_bid,4), self.min_amount[position_pair]), self.order_type, best_bid, common.PositionAction.OPEN)

        self.init = True

    def cancel_all_order(self):
        for order in self.get_active_orders(connector_name=self.connector_name):
            self.cancel(self.connector_name, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        """
        Method called when the connector notifies that an order has been partially or totally filled (a trade happened)
        """
        self.times[event.trading_pair] = self.times[event.trading_pair] + 1
        self.logger().info(logging.INFO, f"The order {event.order_id} has been filled")

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        """
        Method called when the connector notifies a buy order has been completed (fully filled)
        """
        self.logger().info(f"The buy order {event.order_id} has been completed")

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        """
        Method called when the connector notifies a sell order has been completed (fully filled)
        """
        self.times[event.trading_pair] = 0
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
        columns: List[str] = ["Exchange", "Trading Pair", "Amount", "Entry Price" , "Unrealized pnl", "Percentage","Times"]
        data: List[Any] = []
        dc_position = self.connectors[self.connector_name].account_positions
        for trading_pair in dc_position:
            amount = Decimal(dc_position[trading_pair].amount)
            entry_price = Decimal(dc_position[trading_pair].entry_price)
            unrealized_pnl = Decimal(dc_position[trading_pair].unrealized_pnl)
            percentage = round(unrealized_pnl/(abs(amount)*entry_price),4)
            times = self.times[trading_pair]
            data.append([self.connector_name,
                             trading_pair,
                             amount,
                             entry_price,
                             unrealized_pnl,percentage,times])
        df = pd.DataFrame(data=data, columns=columns)
        df.sort_values(by=["Exchange", "Trading Pair"], inplace=True)
        return df