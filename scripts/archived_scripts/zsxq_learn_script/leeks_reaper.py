import pandas as pd
from typing import List
import requests
from hummingbot.strategy.script_strategy_base import Decimal

from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.event.events import  OrderType
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.event.events import (

    OrderFilledEvent,
)

from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import OrderBookEvent, OrderBookTradeEvent
from hummingbot.connector.connector_base import ConnectorBase

import logging
from collections import deque

class LeeksReaper(ScriptStrategyBase):

    connector_name = "binance_paper_trade"
    trading_pair = "BTC-USDT"
    burst_threshold = 0.0003
    burst_vol = 1
    min_stock = 0.00001
    buy_interval = 1
    rebalance_interval = 3

    vol = 0
    prices = []
    p = 0.5
    num_tick = 0
    trade_amount = 0
    bull = False
    bear = False
    init = False

    base_asset = 0
    base_value = 0
    quote_asset = 0
    price = 0

    markets = {connector_name: {trading_pair}}
    threshold = 0.01
    last_ordered_ts = 0
    status = 0
    filled_delay = 0
    trades_buffer = 15
    trades_price = deque(maxlen=trades_buffer)
    trades_amount = deque(maxlen=trades_buffer)
    subscribed_to_order_book_trade_event: bool = False

    def on_tick(self):
        """
        Runs every tick_size seconds, this is the main operation of the strategy.
        - Create proposal (a list of order candidates)
        - Check the account balance and adjust the proposal accordingly (lower order amount if needed)
        - Lastly, execute the proposal on the exchange
        """
        if not self.init:
            self._init()
            self.init = True

        if not self.subscribed_to_order_book_trade_event:
            self.subscribe_to_order_book_trade_event()

        if self.last_ordered_ts < (self.current_timestamp - self.buy_interval - self.filled_delay):
            self.reaper()
            self.last_ordered_ts = self.current_timestamp



    def _init(self):
        base, quote = split_hb_trading_pair(self.trading_pair)
        self.base = base
        self.quote = quote

    def subscribe_to_order_book_trade_event(self):
        self.order_book_trade_event = SourceInfoEventForwarder(self._process_public_trade)
        for market in self.connectors.values():
            for order_book in market.order_books.values():
                order_book.add_listener(OrderBookEvent.TradeEvent, self.order_book_trade_event)
        self.subscribed_to_order_book_trade_event = True

    def _process_public_trade(self, event_tag: int, market: ConnectorBase, event: OrderBookTradeEvent):
        self.trades_price.append(event.price)
        self.trades_amount.append(event.amount)

    def update_vols(self):
        qty = [*self.trades_amount]
        self.vol = 0.7*self.vol + 0.3*sum(qty)
        if len(self.prices) == 0:
            self.prices = [*self.trades_price]

    def cancel_all_order(self):
        active_orders = self.get_active_orders(self.connector_name)
        for order in active_orders:
            self.cancel(self.connector_name, self.trading_pair,order.client_order_id)






    def fetch_historical_trades(self, trading_pair: str, limit) -> List[Decimal]:
        """
        Fetches historical market trade data
        This is the API response data structure:
        [
            {
                "id": 28457,
                "price": "4.00000100",
                "qty": "12.00000000",
                "quoteQty": "48.000012",
                "time": 1499865549590,
                "isBuyerMaker": true,
                "isBestMatch": true
            },
        ]
        :param trading_pair: A market trading pair to
        :param limit: Trades to fetch, 1000 max
        :return: A list of daily close
        """
        url = "https://api.binance.com/api/v3/trades"
        params = {
            "symbol": trading_pair.replace("-", ""),
            "limit": f"{limit}"
        }
        trades = requests.get(url=url, params=params).json()
        return trades


    def update_orderBooks(self):

        orderBook = self.connectors[self.connector_name].get_order_book(self.trading_pair)
        ask_entries = orderBook.ask_entries()
        bid_entries = orderBook.bid_entries()
        Asks1 = float(next(ask_entries).price)
        Asks2 = float(next(ask_entries).price)
        Asks3 = float(next(ask_entries).price)

        Bids1 = float(next(bid_entries).price)
        Bids2 = float(next(bid_entries).price)
        Bids3 = float(next(bid_entries).price)

        Asks = Bids1*0.618 + Asks1*0.382 + 0.01
        Bids = Bids1*0.382 + Asks1*0.618 - 0.01

        PriceGetIn = (Bids1+Asks1)*0.35 + (Bids2+Asks2)*0.1 + (Bids3+Asks3)*0.05
        self.prices.pop(0)
        self.prices.append(PriceGetIn)

        return Asks,Bids,Asks1,Bids1


    def get_balance(self):

        df = self.get_balance_df()
        self.base_asset = float(df.loc[df['Asset'] == self.base, 'Total Balance'])
        self.price = float(self.connectors[self.connector_name].get_mid_price(self.trading_pair))
        self.base_value = self.base_asset * self.price
        self.quote_asset = float(df.loc[df['Asset'] == self.quote, 'Total Balance'])
        total_value = self.quote_asset + self.base_value

        if self.base_value >= total_value * 0.52:
            self.sell(self.connector_name, self.trading_pair, Decimal(total_value/self.price * self.threshold),
                      OrderType.LIMIT, Decimal(self.price * 1.0001))
        elif self.base_value < total_value * 0.48:
            self.buy(self.connector_name, self.trading_pair, Decimal(total_value/self.price * self.threshold),
                     OrderType.LIMIT, Decimal(self.price * 0.9999))


    def reaper(self):
        print('STEP 1 Start | Updating trading volume')
        self.update_vols()
        print('STEP 1 End | Updated trading volume')

        print('STEP 2 Start | Updating the order book')
        Asks, Bids, Asks1, Bids1 = self.update_orderBooks()
        print('SETP 2 End | Update the order book')

        self.cancel_all_order()
        print('STEP 3 Start | Rebalancing your assets')
        if self.status == 0:
            self.get_balance()
        print('STEP 3 End | Rebalanced')
        df = self.get_balance_df()
        self.num_tick += 1
        print('Enter round %s observation' % self.num_tick)
        burstPrice = self.prices[-1] * self.burst_threshold
        if (self.num_tick > 2) & (self.prices[-1] - max(self.prices[-6:-1]) > burstPrice) | (
                (self.prices[-1] - max(self.prices[-6:-2]) > burstPrice) & (self.prices[-1] > self.prices[-2])):
            print('Short-term BULL burst！')
            self.status = 1
            self.bull = True
            self.trade_amount = float(df.loc[df['Asset'] == self.quote, 'Available Balance']) / Bids * 0.99
            self.filled_delay = self.rebalance_interval

        elif (self.num_tick > 2) & (self.prices[-1] - min(self.prices[-6:-1]) < -burstPrice) | (
                (self.prices[-1] - min(self.prices[-6:-2]) < -burstPrice) & (self.prices[-1] < self.prices[-2])):
            print('Short-term BEAR burst！')
            self.status = 1
            self.bear = True
            self.trade_amount = float(df.loc[df['Asset'] == self.base, 'Available Balance'])
            self.filled_delay = self.rebalance_interval
        else:
            self.bull = False
            self.bear = False
            self.status = 0
            self.filled_delay = 0
            print('Neither BULL nor BEAR or observed less than 3 times, wait for the next observation.')
            return

        ## 下单力度调整
        if self.vol <= self.burst_vol:
            self.trade_amount *= self.vol / self.burst_vol

        if self.num_tick < 5:
            self.trade_amount *= 0.8
        if self.num_tick < 10:
            self.trade_amount *= 0.8
        if abs(self.prices[-1] - self.prices[-2]) > 2 * burstPrice:
            self.trade_amount *= 0.9
        if abs(self.prices[-1] - self.prices[-2]) > 3 * burstPrice:
            self.trade_amount *= 0.9
        if abs(self.prices[-1] - self.prices[-2]) > 4 * burstPrice:
            self.trade_amount *= 0.9

        if abs(Asks1 - Bids1) > 2 * burstPrice:
            self.trade_amount *= 0.9
        if abs(Asks1 - Bids1) > 3 * burstPrice:
            self.trade_amount *= 0.9
        if abs(Asks1 - Bids1) > 4 * burstPrice:
            self.trade_amount *= 0.9

        print('Order amount adjustment completed.')

        if self.trade_amount < self.min_stock:
            return
        if self.trade_amount >= self.min_stock:
            if self.bull:
                best_bid = self.connectors[self.connector_name].get_price(self.trading_pair,  False)
                self.buy(self.connector_name, self.trading_pair, Decimal(self.trade_amount), OrderType.LIMIT, best_bid)
            elif self.bear:
                best_ask = self.connectors[self.connector_name].get_price(self.trading_pair,  True)
                self.sell(self.connector_name, self.trading_pair, Decimal(self.trade_amount),OrderType.LIMIT, best_ask)

            self.trade_amount *= 0.98

            new_Asks, new_Bids, tmp1, tmp2 = self.update_orderBooks()
            while self.bull & (new_Bids - Bids > 0.1):
                self.trade_amount *= 0.99
                Bids += 0.1
            while self.bear & (new_Asks - Asks < -0.1):
                self.trade_amount *= 0.99
                Asks -= 0.1

        else:
            self.status = 0

        self.num_tick = 0
        print('Reset the number of observation！')


    def did_fill_order(self, event: OrderFilledEvent):
        """
        Method called when the connector notifies that an order has been partially or totally filled (a trade happened)
        """
     #   self.trade_amount = self.trade_amount - float(event.amount)
        self.logger().info(logging.INFO, f"The order {event.order_id} has been filled")