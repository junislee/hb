from decimal import Decimal

from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.utils.trading_pair_fetcher import TradingPairFetcher


class ArbitrageScout(ScriptStrategyBase):

    # 监视交易所
    arbitrage_markets = ["gate_io_paper_trade","kucoin_paper_trade"]
    # 最小利润
    min_profitability = Decimal('0.008')

    trading_pair_fetcher: TradingPairFetcher = TradingPairFetcher.get_instance()
    if trading_pair_fetcher.ready:
        trading_pairs_1 = trading_pair_fetcher.trading_pairs.get(arbitrage_markets[0], [])
        trading_pairs_2 = trading_pair_fetcher.trading_pairs.get(arbitrage_markets[1], [])

    # 全币种扫描会花比较长时间
    trading_pairs_set = list(set(trading_pairs_1).intersection(trading_pairs_2))
    # 过滤不想监控的交易对
    trading_pairs_set = [word for word in trading_pairs_set if all(letter not in word for letter in ['-ETH', '-BTC' , '-TRY', '5', '3'])]
    # 加快运行速度，只保留列表中前50个的代币
    trading_pairs_set = trading_pairs_set[:50]

    markets = {arbitrage_markets[0]: trading_pairs_set, arbitrage_markets[1]: trading_pairs_set}

    def on_tick(self):
        self.logger().info(f"Set of Trading Pairs {self.trading_pairs_set}")
        self.notify_hb_app_with_timestamp(f"Exchange_1: {self.arbitrage_markets[0]}; Exchange_2: {self.arbitrage_markets[1]}")

        for pair in self.trading_pairs_set:
            try:
                market_1_bid = self.connectors[self.arbitrage_markets[0]].get_price(pair, False)
                market_1_ask = self.connectors[self.arbitrage_markets[0]].get_price(pair, True)
                market_2_bid = self.connectors[self.arbitrage_markets[1]].get_price(pair, False)
                market_2_ask = self.connectors[self.arbitrage_markets[1]].get_price(pair, True)
                profitability_buy_2_sell_1 = market_1_bid / market_2_ask - 1
                profitability_buy_1_sell_2 = market_2_bid / market_1_ask - 1

                if profitability_buy_1_sell_2 > self.min_profitability:
                    self.notify_hb_app_with_timestamp(f"{pair}: Buy@1 & Sell@2: {profitability_buy_1_sell_2:.5f}")
                if profitability_buy_2_sell_1 > self.min_profitability:
                    self.notify_hb_app_with_timestamp(f"{pair}: Buy@2 & Sell@1: {profitability_buy_2_sell_1:.5f}")
            except BaseException:
                self.logger().info(f"{pair} has no bid or ask order book")

