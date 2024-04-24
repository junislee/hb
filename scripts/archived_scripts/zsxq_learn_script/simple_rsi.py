from decimal import Decimal

from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.strategy.directional_strategy_base import DirectionalStrategyBase

import pandas as pd
import pandas_ta as ta  # noqa: F401

class RSI(DirectionalStrategyBase):

    directional_strategy_name: str = "RSI"
    # Define the trading pair and exchange that we want to use and the csv where we are going to store the entries
    trading_pair: str = "TRB-USDT"
    exchange: str = "binance_perpetual"
    order_amount_usd = Decimal("100")
    leverage = 10

    # Configure the parameters for the position
    stop_loss: float = 0.02
    take_profit: float = 0.02
    time_limit: int = 60 * 60
    trailing_stop_activation_delta = 0.008
    trailing_stop_trailing_delta = 0.004
    cooldown_after_execution = 15

    candles = [CandlesFactory.get_candle(connector=exchange,
                                         trading_pair=trading_pair,
                                         interval="1m", max_records=400)]
    markets = {exchange: {trading_pair}}


    def get_signal(self):
        """
        Generates the trading signal based on the RSI indicator.
        Returns:
            int: The trading signal (-1 for sell, 0 for hold, 1 for buy).
        """

        candles_df = self.get_processed_df()
        ema_40 = candles_df.iat[-1, -3]
        ema_400 = candles_df.iat[-1, -2]
        rsi_value = candles_df.iat[-1, -1]
        rsi_pre_value = candles_df.iat[-2, -1]




        if rsi_value > 70 and rsi_value < rsi_pre_value:
            if ema_40 > ema_400:
                self.trailing_stop_activation_delta = 0.005
                self.trailing_stop_trailing_delta = 0.002
            elif ema_40 < ema_400:
                self.railing_stop_activation_delta = 0.008
                self.trailing_stop_trailing_delta = 0.004
            return -1
        elif rsi_value < 30 and rsi_value > rsi_pre_value:
            if ema_40 > ema_400:
                self.trailing_stop_activation_delta = 0.008
                self.trailing_stop_trailing_delta = 0.004
            elif ema_40 < ema_400:
                self.railing_stop_activation_delta = 0.005
                self.trailing_stop_trailing_delta = 0.002
            return 1
        else:
            return 0


    def get_processed_df(self):
        """
        Retrieves the processed dataframe with RSI values.
        Returns:
            pd.DataFrame: The processed dataframe with RSI values.
        """
        candles_df = self.candles[0].candles_df
        candles_df.ta.ema(length=40, append=True)
        candles_df.ta.ema(length=400, append=True)
        candles_df.ta.rsi(length=14, append=True)


        return candles_df

    def market_data_extra_info(self):
        """
        Provides additional information about the market data.
        Returns:
            List[str]: A list of formatted strings containing market data information.
        """
        lines = []
        columns_to_show = ["timestamp", "open", "low", "high", "close", "volume"]
        candles_df = self.get_processed_df()
        lines.extend([f"Candles: {self.candles[0].name} | Interval: {self.candles[0].interval}\n"])
        lines.extend(self.candles_formatted_list(candles_df, columns_to_show))
        return lines
