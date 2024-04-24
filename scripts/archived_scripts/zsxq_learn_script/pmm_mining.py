from collections import deque
from decimal import Decimal
from statistics import mean
from typing import Dict, List

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import OrderBookEvent, OrderBookTradeEvent, OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.connector.utils import split_hb_trading_pair


class PmmMining(ScriptStrategyBase):

    order_refresh_time = 60
    filled_order_delay = 180
    exchange = "gate_io_paper_trade"
    trading_pair = "XCAD-USDT"
    order_max_amount = Decimal(300.0)

    base, quote = split_hb_trading_pair(trading_pair)
    price_source = PriceType.MidPrice
    create_timestamp = 0
    ceiling_pct = 0.05
    floor_pct = 0.05
    trades_buffer = 100
    buy_trades_buffer = deque(maxlen=trades_buffer)
    sell_trades_buffer = deque(maxlen=trades_buffer)

    trades_event_initialized = False
    markets = {exchange: {trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        self._public_trades_forwarder: SourceInfoEventForwarder = SourceInfoEventForwarder(self._process_public_trades)

    @property
    def connector(self):
        return self.connectors[self.exchange]

    @property
    def sell_average_price(self):
        if len(self.sell_trades_buffer) > 0:
            return mean(self.sell_trades_buffer)
        else:
            return None

    @property
    def buy_average_price(self):
        if len(self.buy_trades_buffer) > 0:
            return mean(self.buy_trades_buffer)
        else:
            return None

    def on_tick(self):
        if not self.trades_event_initialized:
            for connector in self.connectors.values():
                for order_book in connector.order_books.values():
                    order_book.add_listener(OrderBookEvent.TradeEvent, self._public_trades_forwarder)
            self.trades_event_initialized = True
        if self.create_timestamp <= self.current_timestamp:
            self.cancel_all_orders()
            proposal: List[OrderCandidate] = self.create_proposal()
            if self.is_the_proposal_inside_the_bounds(proposal):
                proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
                self.place_orders(proposal_adjusted)
            self.create_timestamp = self.order_refresh_time + self.current_timestamp

    def create_proposal(self) -> List[OrderCandidate]:
        orderBook = self.connector.get_order_book(self.trading_pair)
        ask_entries = orderBook.ask_entries()
        bid_entries = orderBook.bid_entries()
        ask_list = [*ask_entries]
        bid_list = [*bid_entries]

        sell_price = (Decimal(ask_list[3].price) + Decimal(ask_list[4].price) + Decimal(ask_list[5].price)) / 3
        buy_price = (Decimal(bid_list[3].price) + Decimal(bid_list[4].price) + Decimal(bid_list[5].price)) / 3

        sell_amount = min(self.order_max_amount, (Decimal(ask_list[2].amount) + Decimal(ask_list[3].amount) + Decimal(ask_list[4].amount) + Decimal(ask_list[5].amount)) / 4)
        buy_amount = min(self.order_max_amount, (Decimal(bid_list[2].amount) + Decimal(bid_list[3].amount) + Decimal(bid_list[4].amount) + Decimal(bid_list[5].amount)) / 4)

        sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=sell_amount, price=sell_price)

        buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                   order_side=TradeType.BUY, amount=buy_amount, price=buy_price)

        return [buy_order, sell_order]

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        proposal_adjusted = self.connector.budget_checker.adjust_candidates(proposal, all_or_none=False)
        return proposal_adjusted

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            if order.amount > 0:
                self.place_order(connector_name=self.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(self.exchange):
            self.cancel(self.exchange, trading_pair=self.trading_pair, order_id=order.client_order_id)

    def is_the_proposal_inside_the_bounds(self, proposal):
        if self.sell_average_price and self.buy_average_price:
            inside_bounds = True
            floor_bound = self.buy_average_price * (1 - self.floor_pct)
            ceiling_bound = self.sell_average_price * (1 + self.ceiling_pct)
            for order in proposal:
                if order.order_side == TradeType.BUY:
                    if order.price > ceiling_bound:
                        inside_bounds = False
                        break
                elif order.order_side == TradeType.SELL:
                    if order.price < floor_bound:
                        inside_bounds = False
                        break
            return inside_bounds
        else:
            return False

    def _process_public_trades(self,
                               event_tag: int,
                               order_book: OrderBook,
                               event: OrderBookTradeEvent):
        if event.type == TradeType.SELL:
            self.sell_trades_buffer.append(event.price)
        elif event.type == TradeType.BUY:
            self.buy_trades_buffer.append(event.price)

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        if self.sell_average_price and self.buy_average_price:
            bid = self.connector.get_price(self.trading_pair, False)
            ask = self.connector.get_price(self.trading_pair, True)
            floor_bound = self.buy_average_price * (1 - self.floor_pct)
            ceiling_bound = self.sell_average_price * (1 + self.ceiling_pct)

            status = f"""
        Best bid: {bid:.2f} | Mid price: {(bid + ask) / 2:.2f} | Best ask: {ask:.2f}
        Price average of buys: {self.buy_average_price:.2f}
        Price average of sells: {self.sell_average_price:.2f}
        Floor Bound: {floor_bound}
        Ceiling Bound: {ceiling_bound}
            """


            return status
        else:
            return "The buffers are empty!"

    def did_fill_order(self, event: OrderFilledEvent):
        self.create_timestamp = self.create_timestamp + self.filled_order_delay