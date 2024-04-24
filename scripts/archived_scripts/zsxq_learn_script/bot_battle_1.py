from decimal import Decimal
from typing import Dict

from hummingbot.connector.exchange_base import ExchangeBase
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, TradeType, PositionMode, PositionAction
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig, CandlesFactory

import pandas as pd

from typing import Any, List

from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    OrderFilledEvent,

)

from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory


class BotBattle1(ScriptStrategyBase):


    config = {
        "bid_spread": .05,   # how far away from the mid price do you want to place the first bid order (1 indicated 1%)
        "ask_spread": .05,   # how far away from the mid price do you want to place the first bid order (1 indicated 1%)
        "amount": 0.005,       # the amount ien base currancy you want to buy or sell
        "order_refresh_time": 3,
        "market": "gate_io_perpetual",
        "pair": "WLD-USDT",
        "min_spread": .00,
        "min_amount": 0.01,
        "amount_usdt": 50,
        "max_amount_usdt": 500
    }

    enable_take_profit = True
    take_profit = 0.002
    enable_stop_loss = False
    stop_loss = -0.02
    _filled_order_delay = 15
    _create_timestamp = 0

    trading_pairs = {config["pair"]}

    markets = {config["market"]: {config["pair"]}}

    set_leverage_flag = None
    leverage = 10
    position_mode: PositionMode = PositionMode.ONEWAY

    candles = CandlesFactory.get_candle(CandlesConfig(connector="gate_io_perpetual", trading_pair=config["pair"], interval="5m", max_records=100))

    rsi_value = 50
    natr_value = 0
    rsi_filter = True


    first = 0
    last = 0
    diff_adj_mid = 0
    diff_fist_last = 0
    percentage = 0

    status = 1 #1正常状态，0平仓

    price_multiplier = 1
    spread_multiplier = 1

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        if self.rsi_filter:
            self.candles.start()
            print("start")

    def on_stop(self):
        self.candles.stop()

    @property
    def connector(self) -> ExchangeBase:
        return self.connectors[self.config["market"]]



    def check_and_set_leverage(self):
        if not self.set_leverage_flag:
            perp_connector = self.connector
            perp_connector.set_position_mode(PositionMode.ONEWAY)
            perp_connector.set_leverage(
                trading_pair=self.config["pair"], leverage=self.leverage
            )
            self.logger().info(
                f"Setting leverage to {self.leverage}x for {perp_connector} on {self.config['pair']}"
            )
            self.set_leverage_flag = True



    def on_tick(self):
        """
        Runs every tick_size seconds, this is the main operation of the strategy.
        This method does two things:
        - Refreshes the current bid and ask if they are set to None
        - Cancels the current bid or current ask if they are past their order_refresh_time
          The canceled orders will be refreshed next tic
        """

        self.check_and_set_leverage()
        self.get_balance()
        self.update_parameter()

        if self.rsi_filter:
            self.get_rsi()
        if self._create_timestamp >= self.current_timestamp:
            return

        active_orders = self.get_active_orders(self.config["market"])
        active_bid = None
        active_ask = None
        for order in active_orders:
            if order.is_buy:
                active_bid = order
            else:
                active_ask = order
        proposal: List(OrderCandidate) = []

        if active_bid is None and self.asset_value <= self.config["max_amount_usdt"] and self.rsi_value < 90 and self.status == 1:
            proposal.append(self.create_order(True))
        if active_ask is None and self.asset_value >= -1 * self.config["max_amount_usdt"] and self.rsi_value > 10 and self.status ==1:
            proposal.append(self.create_order(False))

        if (len(proposal) > 0):
            adjusted_proposal = proposal
            insufficient_funds = False
            if (insufficient_funds):
                pass
            else:
                buyaction = PositionAction.OPEN
                sellaction = PositionAction.OPEN
                if self.asset_value > 0:
                    sellaction = PositionAction.CLOSE
                elif self.asset_value < 0:
                    buyaction = PositionAction.CLOSE
                for order in adjusted_proposal:
                    if order.order_side == TradeType.BUY:
                        self.buy(self.config["market"], order.trading_pair, order.amount,  order.order_type, Decimal(order.price), buyaction)
                    elif order.order_side == TradeType.SELL:
                        self.sell(self.config["market"], order.trading_pair, order.amount, order.order_type, Decimal(order.price), sellaction)

        for order in active_orders:
            if (order.age() > self.config["order_refresh_time"]):
                self.cancel(self.config["market"], self.config["pair"], order.client_order_id)

        self._create_timestamp = self.current_timestamp

    def get_rsi(self):
        if self.candles.is_ready:
            candles_df = self.candles.candles_df
            candles_df.ta.rsi(length=14, append=True)
            self.rsi_value = candles_df.iat[-1, -1]
            candles_df.ta.natr(length=14, scalar=0.5, append=True)
            self.natr_value = candles_df.iat[-1, -1]




    def create_order(self, is_bid: bool) -> OrderCandidate:
        """
         Create a propsal for the current bid or ask using the adjusted mid price.
         """
        mid_price = Decimal(self.adjusted_mid_price())
        bid_spread = Decimal(self.config["bid_spread"])
        ask_spread = Decimal(self.config["ask_spread"])
        bid_price = Decimal(mid_price - mid_price * bid_spread * Decimal(.01)) * Decimal(1 - self.natr_value)
        ask_price = Decimal(mid_price + mid_price * ask_spread * Decimal(.01)) * Decimal(1 + self.natr_value)

        best_ask_price = self.connector.get_price(self.config["pair"],True)
        best_bid_price = self.connector.get_price(self.config["pair"],False)

        if bid_price > best_bid_price:
            bid_price = best_bid_price
        elif ask_price < best_ask_price:
            ask_price = best_ask_price
        if self.enable_take_profit:
            if self.asset_value >= self.config["max_amount_usdt"]/2 and self.percentage >= self.take_profit:
                ask_price = best_ask_price
            elif self.asset_value <= -1 * self.config["max_amount_usdt"]/2 and self.percentage >= self.take_profit:
                bid_price = best_bid_price

        if self.enable_stop_loss:
            if self.asset_value >= self.config["max_amount_usdt"]/2 and self.percentage <= self.stop_loss:
                ask_price = best_ask_price
            elif self.asset_value <= -1 * self.config["max_amount_usdt"]/2 and self.percentage <= self.stop_loss:
                bid_price = best_bid_price

        if self.status == 0:
            if self.asset_value >0 :
                ask_price = best_ask_price
            elif self.asset_value < 0:
                bid_price = best_bid_price
        ratio = Decimal(1)


        price = bid_price if is_bid else ask_price
        price = self.connector.quantize_order_price(self.config["pair"], Decimal(price))
        amount = Decimal(self.config["amount_usdt"]/price ) * ratio
        if self.status == 0:
            if amount > abs(self.asset_amount):
                amount = abs(self.asset_amount)
        order = OrderCandidate(
            trading_pair=self.config["pair"],
            is_maker=True,
            order_type=OrderType.LIMIT_MAKER,
            order_side=TradeType.BUY if is_bid else TradeType.SELL,
            amount=amount,
            price=price)
        return order

    #更新参数
    def update_parameter(self):
        df = self.get_balance_df()
        base_asset = float(df.loc[(df['Asset'] == "USDT") & (df['Exchange'] == self.config["market"]), 'Total Balance'])
        self.total_balance = Decimal(base_asset) + Decimal(self.unrealized_pnl)
        # print(self.total_balance)




    #获取当前持仓状况
    def get_balance(self):
        positions = self.connectors[self.config["market"]].account_positions
        # print(positions)
        for tp in self.trading_pairs:
            price = Decimal(self.connectors[self.config["market"]].get_mid_price(tp))
            if tp in positions :
                amount = Decimal(positions[tp].amount)
                entry_price = Decimal(positions[tp].entry_price)
                self.percentage = round((price - entry_price) / entry_price, 4)
                self.unrealized_pnl = Decimal(positions[tp].unrealized_pnl)
            else:
                amount = 0
                self.unrealized_pnl = 0
            self.asset_value = amount * price
            self.asset_amount = amount



    def close_position(self,amount,price):
        if amount > 0:
            self.sell(self.config["market"], self.config["pair"],
                        abs(amount),
                        OrderType.MARKET, price, PositionAction.CLOSE)
        elif amount < 0:
              self.buy(self.config["market"], self.config["pair"],
                       abs(amount),
                       OrderType.MARKET, price, PositionAction.CLOSE)




    def adjusted_mid_price(self):
        orderbook = self.connector.get_order_book(self.config["pair"])

        buy_orders = orderbook.snapshot[0].loc[:(10), ["price", "amount"]].values.tolist()
        sell_orders = orderbook.snapshot[1].loc[:(10), ["price", "amount"]].values.tolist()

        micro_price = Decimal(self.calculate_micro_price(buy_orders,sell_orders))

        return micro_price

    #简化版本
    def calculate_micro_price(self,buy_orders, sell_orders):

        total_buy_quantity = sum(quantity for price, quantity in buy_orders)
        total_sell_quantity = sum(quantity for price, quantity in sell_orders)

        weighted_buy_price = sum(price * (quantity / total_buy_quantity) for price, quantity in buy_orders)
        weighted_sell_price = sum(price * (quantity / total_sell_quantity) for price, quantity in sell_orders)

        micro_price = (weighted_buy_price * total_sell_quantity + weighted_sell_price * total_buy_quantity) / (
                    total_buy_quantity + total_sell_quantity)

        return micro_price


    def format_status(self) -> str:

        if not self.ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        warning_lines = []
        warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
        actual_mid_price = self.connector.get_mid_price(self.config["pair"])
        adjusted_mid_price = self.adjusted_mid_price()
        lines.extend(["", "  Adjusted mid price: " + str(adjusted_mid_price)] + ["  Actual mid price: " + str(actual_mid_price)])
        balance_df = self.get_balance_df()
        lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])
        positions_df = self.get_positions_df()
        lines.extend(["", "  Positions:"] + ["    " + line for line in positions_df.to_string(index=False).split("\n")])
        try:
            df = self.active_orders_df()
            lines.extend(["", "  Orders:"] + ["    " + line for line in df.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["", "  No active maker orders."])

        warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)

    def get_positions_df(self) -> pd.DataFrame:
        """
        Returns a data frame for all asset balances for displaying purpose.
        """
        columns: List[str] = ["Exchange", "Trading Pair", "Amount", "Entry Price" , "Unrealized pnl", "Percentage"]
        data: List[Any] = []
        dc_position = self.connectors[self.config["market"]].account_positions
        for trading_pair in dc_position:
            amount = Decimal(dc_position[trading_pair].amount)
            entry_price = Decimal(dc_position[trading_pair].entry_price)
            unrealized_pnl = Decimal(dc_position[trading_pair].unrealized_pnl)
            percentage = round(unrealized_pnl/(abs(amount)*entry_price),4)
            data.append([self.config["market"],
                             trading_pair,
                             amount,
                             entry_price,
                             unrealized_pnl,percentage])
        df = pd.DataFrame(data=data, columns=columns)
        df.sort_values(by=["Exchange", "Trading Pair"], inplace=True)
        return df

    def did_fill_order(self, event: OrderFilledEvent):
        self._create_timestamp = self.current_timestamp + self._filled_order_delay
