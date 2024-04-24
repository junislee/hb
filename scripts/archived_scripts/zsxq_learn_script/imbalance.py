from decimal import Decimal
from typing import List

from hummingbot.connector.exchange_base import ExchangeBase
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class Imbalance(ScriptStrategyBase):

    strategy = {
        "test_volume": 100000,
        "bid_spread": .5,
        "ask_spread": .5,
        "amount": 1000,
        "order_refresh_time": 3,
        "market": "binance_paper_trade",
        "pair": "FRONT-BUSD",
        "min_spread": .1,
        "min_amount": 50,
    }

    markets = {strategy["market"]: {strategy["pair"]}}

    @property
    def connector(self) -> ExchangeBase:
        return self.connectors[self.strategy["market"]]

    def on_tick(self):
        active_orders = self.get_active_orders(self.strategy["market"])

        active_bid = None
        active_ask = None
        for order in active_orders:
            if order.is_buy:
                active_bid = order
            else:
                active_ask = order
        proposal: List(OrderCandidate) = []
        if active_bid is None:
            proposal.append(self.create_order(True))
        if active_ask is None:
            proposal.append(self.create_order(False))
        if (len(proposal) > 0):
            adjusted_proposal: List(OrderCandidate) = self.connector.budget_checker.adjust_candidates(proposal, all_or_none=False)
            insufficient_funds = False
            for order in adjusted_proposal:
                if (order.amount <= self.strategy["min_amount"]):
                    insufficient_funds = True
                    adjusted_proposal.remove(order)
            if (insufficient_funds):
                for order in adjusted_proposal:
                    if order.order_side == TradeType.BUY:
                        self.buy(self.strategy["market"], order.trading_pair, order.amount, order.order_type, Decimal(order.price))
                    elif order.order_side == TradeType.SELL:
                        self.sell(self.strategy["market"], order.trading_pair, order.amount, order.order_type, Decimal(order.price))
            else:
                for order in adjusted_proposal:
                    if order.order_side == TradeType.BUY:
                        self.buy(self.strategy["market"], order.trading_pair, order.amount,  order.order_type, Decimal(order.price))
                    elif order.order_side == TradeType.SELL:
                        self.sell(self.strategy["market"], order.trading_pair, order.amount, order.order_type, Decimal(order.price))

        for order in active_orders:
            if (order.age() > self.strategy["order_refresh_time"]):
                self.cancel(self.strategy["market"], self.strategy["pair"], order.client_order_id)

    def create_order(self, is_bid: bool) -> OrderCandidate:

        mid_price = Decimal(self.adjusted_mid_price())
        bid_spread = Decimal(self.strategy["bid_spread"])
        ask_spread = Decimal(self.strategy["ask_spread"])
        bid_price = mid_price - mid_price * bid_spread * Decimal(.01)
        ask_price = mid_price + mid_price * ask_spread * Decimal(.01)

        best_ask_price = self.connector.get_price(self.strategy["pair"],True) *( 1 + Decimal(self.strategy["min_spread"])* Decimal(.01))
        best_bid_price = self.connector.get_price(self.strategy["pair"],False) * (1 - Decimal(self.strategy["min_spread"])* Decimal(.01))

        if bid_price > best_bid_price:
            bid_price = best_bid_price
        elif ask_price < best_ask_price:
            ask_price = best_ask_price

        price = bid_price if is_bid else ask_price
        price = self.connector.quantize_order_price(self.strategy["pair"], Decimal(price))
        order = OrderCandidate(
            trading_pair=self.strategy["pair"],
            is_maker=False,
            order_type=OrderType.LIMIT_MAKER,
            order_side=TradeType.BUY if is_bid else TradeType.SELL,
            amount=Decimal(self.strategy["amount"]),
            price=price)
        return order

    def adjusted_mid_price(self):
        ask_result = self.connector.get_quote_volume_for_base_amount(self.strategy["pair"], True, self.strategy["test_volume"])
        bid_result = self.connector.get_quote_volume_for_base_amount(self.strategy["pair"], False, self.strategy["test_volume"])
        average_ask = ask_result.result_volume / ask_result.query_volume
        average_bid = bid_result.result_volume / bid_result.query_volume
        return average_bid + ((average_ask - average_bid) / 2)

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        warning_lines = []
        warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
        actual_mid_price = self.connector.get_mid_price(self.strategy["pair"])
        adjusted_mid_price = self.adjusted_mid_price()
        lines.extend(["", "  Adjusted mid price: " + str(adjusted_mid_price)] + ["  Actual mid price: " + str(actual_mid_price)])
        balance_df = self.get_balance_df()
        lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])
        try:
            df = self.active_orders_df()
            lines.extend(["", "  Orders:"] + ["    " + line for line in df.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["", "  No active maker orders."])

        warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)