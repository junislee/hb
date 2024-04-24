from decimal import Decimal
from typing import Dict

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionSide, PositionAction
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.smart_components.controllers.dman_v3 import DManV3, DManV3Config
from hummingbot.smart_components.strategy_frameworks.data_types import (
    ExecutorHandlerStatus,
    TripleBarrierConf,
)
from hummingbot.smart_components.strategy_frameworks.market_making.market_making_executor_handler import (
    MarketMakingExecutorHandler,
)
from hummingbot.smart_components.utils.distributions import Distributions
from hummingbot.smart_components.utils.order_level_builder import OrderLevelBuilder
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class DManV3MultiplePairs(ScriptStrategyBase):
    # Account configuration
    exchange = "binance_perpetual"
    trading_pairs = ["WLD-USDT","STRAX-USDT","TRB-USDT","PYTH-USDT","ORDI-USDT"]
    leverage = 5

    # Candles configuration
    candles_exchange = "binance_perpetual"
    candles_interval = "3m"
    candles_max_records = 300
    bollinger_band_length = 200
    bollinger_band_std = 3.0

    # Orders configuration
    order_amount = Decimal("50")
    n_levels = 4
    start_spread = 0.6  # percentage of the bollinger band (0.5 means that the order will be between the bollinger mid-price and the upper/lower band)
    end_spread = 2.0 # percentage of the bollinger band (1.5 means that the last order will be 50% above the upper band and 50% below the lower band)

    # Triple barrier configuration
    stop_loss = Decimal("0.2")
    take_profit = Decimal("0.03")
    time_limit = 60 * 60 * 6
    trailing_stop_activation_price_delta = Decimal("0.008")
    trailing_stop_trailing_delta = Decimal("0.004")

    # Advanced configurations
    side_filter = True
    dynamic_spread_factor = True
    dynamic_target_spread = False
    smart_activation = True
    activation_threshold = Decimal("0.001")

    # Applying the configuration
    order_level_builder = OrderLevelBuilder(n_levels=n_levels)
    order_levels = order_level_builder.build_order_levels(
        amounts=order_amount,
        spreads=Distributions.linear(n_levels=n_levels, start=start_spread, end=end_spread),
        triple_barrier_confs=TripleBarrierConf(
            stop_loss=stop_loss, take_profit=take_profit, time_limit=time_limit,
            trailing_stop_activation_price_delta=trailing_stop_activation_price_delta,
            trailing_stop_trailing_delta=trailing_stop_trailing_delta),
    )
    controllers = {}
    markets = {}
    executor_handlers = {}

    for trading_pair in trading_pairs:
        config = DManV3Config(
            exchange=exchange,
            trading_pair=trading_pair,
            order_levels=order_levels,
            candles_config=[
                CandlesConfig(connector=candles_exchange, trading_pair=trading_pair,
                              interval=candles_interval, max_records=candles_max_records),
            ],
            bb_length=bollinger_band_length,
            bb_std=bollinger_band_std,
            side_filter=side_filter,
            dynamic_spread_factor=dynamic_spread_factor,
            dynamic_target_spread=dynamic_target_spread,
            smart_activation=smart_activation,
            activation_threshold=activation_threshold,
            leverage=leverage,
        )
        controller = DManV3(config=config)
        markets = controller.update_strategy_markets_dict(markets)
        controllers[trading_pair] = controller

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        for trading_pair, controller in self.controllers.items():
            self.executor_handlers[trading_pair] = MarketMakingExecutorHandler(strategy=self, controller=controller)

    @property
    def is_perpetual(self):
        """
        Checks if the exchange is a perpetual market.
        """
        return "perpetual" in self.exchange

    def on_stop(self):
        if self.is_perpetual:
            self.close_open_positions()
        for executor_handler in self.executor_handlers.values():
            executor_handler.stop()

    def close_open_positions(self):
        # we are going to close all the open positions when the bot stops
        for connector_name, connector in self.connectors.items():
            for trading_pair, position in connector.account_positions.items():
                if trading_pair in self.markets[connector_name]:
                    if position.position_side == PositionSide.LONG:
                        self.sell(connector_name=connector_name,
                                  trading_pair=position.trading_pair,
                                  amount=abs(position.amount),
                                  order_type=OrderType.MARKET,
                                  price=connector.get_mid_price(position.trading_pair),
                                  position_action=PositionAction.CLOSE)
                    elif position.position_side == PositionSide.SHORT:
                        self.buy(connector_name=connector_name,
                                 trading_pair=position.trading_pair,
                                 amount=abs(position.amount),
                                 order_type=OrderType.MARKET,
                                 price=connector.get_mid_price(position.trading_pair),
                                 position_action=PositionAction.CLOSE)

    def on_tick(self):
        """
        This shows you how you can start meta controllers. You can run more than one at the same time and based on the
        market conditions, you can orchestrate from this script when to stop or start them.
        """
        for executor_handler in self.executor_handlers.values():
            if executor_handler.status == ExecutorHandlerStatus.NOT_STARTED:
                executor_handler.start()

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        for trading_pair, executor_handler in self.executor_handlers.items():
            lines.extend(
                [f"Strategy: {executor_handler.controller.config.strategy_name} | Trading Pair: {trading_pair}",
                 executor_handler.to_format_status()])
        return "\n".join(lines)