import asyncio
import logging
import math
from decimal import Decimal
from typing import Dict, List, Optional, Union, Tuple

from hummingbot.core.data_type.common import OrderType, PositionAction, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate, PerpetualOrderCandidate
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketOrderFailureEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.logger import HummingbotLogger
from hummingbot.smart_components.executors.executor_base import ExecutorBase
from hummingbot.smart_components.executors.trailing_grid_executor.data_types import TrailingGridExecutorConfig
from hummingbot.smart_components.models.base import SmartComponentStatus
from hummingbot.smart_components.models.executors import CloseType, TrackedOrder
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class TrailingGridExecutor(ExecutorBase):
    _logger = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, strategy: ScriptStrategyBase, config: TrailingGridExecutorConfig, 
                 update_interval: float = 1.0, max_retries: int = 10 ):
        """
        Initialize the PositionExecutor instance.

        :param strategy: The strategy to be used by the PositionExecutor.
        :param config: The configuration for the PositionExecutor, subclass of PositionExecutoConfig.
        :param update_interval: The interval at which the PositionExecutor should be updated, defaults to 1.0.
        :param max_retries: The maximum number of retries for the PositionExecutor, defaults to 5.
        """

        super().__init__(strategy=strategy, config=config, connectors=[config.connector_name], update_interval=update_interval)
        self.config: TrailingGridExecutorConfig = config

        # Order tracking
        self._open_orders: List[TrackedOrder] = []
        self._close_orders: List[Tuple[List[str], TrackedOrder]] = []
        self._close_all_orders: List[TrackedOrder] = []
        self._failed_orders: List[TrackedOrder] = []
        self._trailing_stop_trigger_pct: Optional[Decimal] = None

        self._total_executed_amount_backup: Decimal = Decimal("0")
        self._current_retries = 0
        self._max_retries = max_retries
        self._last_executor_time = 0

        self.stop_queue = self.config.stop_queue
        # 保存信号状态变量
        self._signal: int = 1
        self._signal_update: bool = True
        self._last_signal_time = None

        # 保存策略需要变量
        self._pos_avg_price: Decimal = Decimal(0.0)
        self._trade_profit: Decimal = Decimal(0.0)
        self._last_price: Decimal = Decimal(0.0)

    @property
    def singnal(self) -> int:
        return self._signal

    @property
    def signal_update(self) -> bool:
        return self._signal_update

    @property
    def is_perpetual(self) -> bool:
        """
        Check if the exchange connector is perpetual.

        :return: True if the exchange connector is perpetual, False otherwise.
        """
        return self.is_perpetual_connector(self.config.connector_name)
    
    @property
    def is_trading(self):
        """
        Check if the position is trading.

        :return: True if the position is trading, False otherwise.
        """
        return self.status == SmartComponentStatus.RUNNING and self.open_filled_amount > Decimal("0")

    @property
    def active_open_orders(self) -> List[TrackedOrder]:
        return self._open_orders

    @property
    def active_close_orders(self) -> List[TrackedOrder]:
        return self._close_orders

    '''
    下面四个属性是策略需要的
    '''
    @property
    def open_nums(self) -> int:
        return len(self._open_orders)

    @property
    def open_pos_min_price(self) -> Decimal:
        if self.open_nums == 0:
            return Decimal(0.0)
        else:
            return min([order.average_executed_price for order in self._open_orders])
    
    @property
    def open_pos_avg_price(self) -> Decimal:
        return self._pos_avg_price

    @property
    def last_trade_profit(self) -> Decimal:
        return self._trade_profit

    @property
    def open_order_type(self) -> OrderType:
        return OrderType.LIMIT if self.config.open_order_type == OrderType.LIMIT else OrderType.MARKET

    @property
    def close_order_type(self) -> OrderType:
        return OrderType.MARKET

    @property
    def open_filled_amount(self) -> Decimal:
        return sum([order.executed_amount_base for order in self.active_open_orders])

    @property
    def open_filled_amount_quote(self) -> Decimal:
        return self.open_filled_amount * self.current_position_average_price

    @property
    def close_filled_amount(self) -> Decimal:
        return sum([order.executed_amount_base for _, order in self.active_close_orders])

    @property
    def close_filled_amount_quote(self) -> Decimal:
        return self.close_filled_amount * Decimal(0.0)

    @property
    def filled_amount(self) -> Decimal:
        """
        Get the filled amount of the position.
        """
        return self.open_filled_amount + self.close_filled_amount

    @property
    def filled_amount_quote(self) -> Decimal:
        """
        Get the filled amount of the position in quote currency.
        """
        return self.open_filled_amount_quote + self.close_filled_amount_quote

    @property
    def current_position_average_price(self) -> Decimal:
        return sum([order.average_executed_price * order.executed_amount_base for order in self._open_orders]) / \
            self.open_filled_amount if self._open_orders and self.open_filled_amount > Decimal("0") else Decimal("0")

    @property
    def current_market_price(self) -> Decimal:
        """
        根据 TrailingGridExecutorConfig.side选择报价
        """
        price_type = PriceType.BestBid if self.config.side == TradeType.BUY else PriceType.BestAsk
        return self.get_price(self.config.connector_name, self.config.trading_pair, price_type=price_type)

    def get_net_pnl_quote(self) -> Decimal:
        """
        Returns the net profit or loss in quote currency.
        """
        return Decimal(1.0)

    def get_net_pnl_pct(self) -> Decimal:
        """
        Returns the net profit or loss in percentage.
        """
        return Decimal(1.0)

    def get_cum_fees_quote(self) -> Decimal:
        """
        Returns the cumulative fees in quote currency.
        """
        return Decimal(1.0)

    async def control_task(self):
        """
        This method is responsible for controlling the task based on the status of the executor.

        :return: None
        """
        if self.status == SmartComponentStatus.RUNNING:
            if  (self._strategy.current_timestamp - self._last_executor_time > self.config.executor_interval):
                self._last_executor_time = self._strategy.current_timestamp
                self._last_price = self.get_price(connector_name=self.config.connector_name,trading_pair=self.config.trading_pair,price_type=PriceType.LastTrade)

                self.control_signal()
                if self.signal_update:
                    self._strategy.logger().info(
                    f"{self.config.trading_pair}信号更新进入开闭仓判断, 当前信号为:{self._signal}, 当前sizes:{self.open_nums},当前最新价:{round(self._last_price,4)}\
                        当前min_price:{round(self.open_pos_min_price,4)},当前持仓均价:{round(self.open_pos_avg_price,4)},信号为:{self.singnal}"
                    )
                    self.control_open_order()
                    self.control_close_order()
                
        elif self.status == SmartComponentStatus.SHUTTING_DOWN:
            await self.control_shutdown_process()
        self.evaluate_max_retries()

    def control_signal(self):
        candles = self._strategy.market_data_provider.get_candles_df(
            self.config.connector_name, 
            self.config.trading_pair, 
            self.config.interval, 
            self.config.max_records)
        signal = self.config.signal_func(df=candles, 
                                                   *self.config.signal_func_args, 
                                                   **self.config.signal_func_kwargs)
        self._strategy.logger().info(
            f"信号时间:{signal['timestamp']}, last_sigal_time:{self._last_signal_time}"
        )
        if self._last_signal_time == signal['timestamp']:
            self._signal_update = False
            return
        else:
            self._signal_update = True
            self._last_signal_time = signal['timestamp']
            self._signal = signal['signal']



    def evaluate_max_retries(self):
        """
        This method is responsible for evaluating the maximum number of retries to place an order and stop the executor
        if the maximum number of retries is reached.

        :return: None
        """
        if self._current_retries > self._max_retries:
            self.close_type = CloseType.FAILED
            self.stop()

    def on_start(self):
        """
        This method is responsible for starting the executor and validating if the position is expired. The base method
        validates if there is enough balance to place the open order.

        :return: None
        """
        super().on_start()

    def control_open_order(self):
        """
        This method is responsible for controlling the open order. It checks if the open order is not placed and if the
        close price is within the activation bounds to place the open order.

        :return: None
        """
        if self.open_nums == 0 and self.singnal == 1:
            self.open_position(open_price=self._last_price)
        elif self.open_nums > 0 and self.singnal == 1 and \
        (self._last_price - self.open_pos_min_price) / self.open_pos_min_price <  -self.config.grid_open and \
        self.open_nums < self.config.grid_max:
            self.incr_position(self._last_price)
            
    def open_position(self, open_price):
        """
        This method is responsible for placing the open order.

        :return: None
        """
        self._strategy.logger().info(
            f"进入了开仓逻辑"
        )
        order_id = self.place_order(
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            order_type=self.config.open_order_type,
            amount=self.config.amount_quote/open_price,
            price=open_price,
            side=self.config.side,
            position_action=PositionAction.OPEN,
        )
        if order_id:
            self._open_orders.append(TrackedOrder(order_id=order_id))
        self.logger().debug("Placing open order")

    def incr_position(self, open_price):
        self._strategy.logger().info(
            f"进入了加仓逻辑"
        )
        roe = ((open_price - self._pos_avg_price) / self._pos_avg_price)\
              if self._pos_avg_price else 0
        
        # 判断是否根据mart_open调整大小，如果roe小于-mart_open，使用特定的计算方式
        size = math.sqrt(self.open_pos_avg_price * self.open_filled_amount / open_price)\
              if roe < -self.config.mart_open \
              else self.config.amount_quote / open_price
        
        order_id = self.place_order(
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            order_type=self.config.open_order_type,
            amount=size,
            price=open_price,
            side=self.config.side,
            position_action=PositionAction.OPEN,
        )
        if order_id:
            self._open_orders.append(TrackedOrder(order_id=order_id))
        self.logger().debug("Placing open order")


    def control_close_order(self):
        if self.open_nums== 1 and  self.singnal == -1 and \
        (self._last_price - self.open_pos_min_price) / self.open_pos_min_price > self.config.grid_close:
            self.close_position(self._last_price)
        elif self.open_nums > 1 and  self.singnal == -1 and \
        (self._last_price - self.open_pos_min_price) / self.open_pos_min_price > self.config.grid_close:
            self.decr_position(self._last_price)


    def close_position(self, close_price):
        self._strategy.logger().info(
            f"进入了平仓逻辑"
        )
        delta_amount_to_close = self.open_filled_amount
        min_order_size = self.connectors[self.config.connector_name].trading_rules[self.config.trading_pair].min_order_size
        if delta_amount_to_close >= min_order_size:
            order_id = self._strategy.sell(connector_name=self.config.connector_name,
                                            trading_pair=self.config.trading_pair,
                                            order_type=OrderType.MARKET,
                                            amount=delta_amount_to_close, 
                                            price=close_price,
                                            position_action=PositionAction.CLOSE)
            self._close_all_orders.append(TrackedOrder(order_id=order_id))


    def decr_position(self, close_price):
        size = Decimal(0.0)
        if not self.open_pos_avg_price or self.open_pos_avg_price == 0:
            return  # 防止除以0
        roe = (close_price - self.open_pos_avg_price) / self.open_pos_avg_price
        # 检查是否满足平仓条件
        if (self.open_pos_avg_price * self.open_filled_amount * roe + self.last_trade_profit) /\
              (self.open_pos_avg_price * self.open_filled_amount) > self.config.grid_close:
            self._strategy.logger().info(
                f"价格超过持仓均价的盈利线,avg_price{self.open_pos_avg_price},open_amount:{self.open_filled_amount},last_profit:{self.last_trade_profit}"
            )
            self.close_position(close_price)
        else:
            open_ids = []
            for ord in self._open_orders:
                if (close_price - ord.average_executed_price) / ord.average_executed_price\
                      > self.config.grid_close:
                    size += ord.executed_amount_base
                    open_ids.append(ord.order_id)
            if self.open_nums > 0:
                self._strategy.logger().info(
                    f"仅出掉适合的单子,size{size}"
                )
                order_id = self._strategy.sell(connector_name=self.config.connector_name,
                                                trading_pair=self.config.trading_pair,
                                                order_type=OrderType.MARKET,
                                                amount=size, 
                                                price=close_price,
                                                position_action=PositionAction.CLOSE)
                if order_id:
                    self._close_orders.append((open_ids, TrackedOrder(order_id=order_id)))
            else:
                self.close_position(close_price)

    def place_close_order_and_cancel_open_orders(self, price: Decimal = Decimal("NaN")):
        """
        This method is responsible for placing the close order
        """
        self.cancel_open_orders()
        self.close_position(price)
        self._status = SmartComponentStatus.SHUTTING_DOWN
        self.close_timestamp = self._strategy.current_timestamp

    def cancel_open_orders(self):
        for tracked_order in self._open_orders:
            if tracked_order.order and tracked_order.order.is_open:
                self._strategy.cancel(connector_name=self.config.connector_name, trading_pair=self.config.trading_pair,
                                      order_id=tracked_order.order_id)

    def early_stop(self):
        """
        This method allows strategy to stop the executor early.
        """
        self.close_type = CloseType.EARLY_STOP
        self.place_close_order_and_cancel_open_orders()

    def update_tracked_orders_with_order_id(self, order_id: str):
        '''
        针对open, close, close_all fill 事件进入处理逻辑
        open事件更新avg_price
        close事件, 
        close_all事件清空open序列, avg_price置0
        '''
        # close_all 事件
        active_order = next((order for order in self._close_all_orders if order.order_id == order_id), None)
        if active_order:
            self._strategy.logger().info(
                f"触发close_all事件"
            )
            in_flight_order = self.get_in_flight_order(self.config.connector_name, order_id)
            if in_flight_order:
                active_order.order = in_flight_order
                # 清空open序列
                self._open_orders = []
                self._close_orders = []
                # 持仓平均成本清零
                self._pos_avg_price = Decimal(0)
                # 交易利润清空
                self._trade_profit = Decimal(0)

        # close 事件
        active_order_list, active_order = next(((id_list, tracked_order) for id_list, tracked_order in self._close_orders if tracked_order.order_id == order_id), (None, None))
        if active_order and active_order_list:
            in_flight_order = self.get_in_flight_order(self.config.connector_name, order_id)
            if in_flight_order:
                # 更新交易利润
                self._trade_profit = self._trade_profit +\
                      sum(order.executed_amount_base for ord_id in active_order_list for order in self._open_orders if order.order_id == ord_id) *\
                      (in_flight_order.average_executed_price - self._pos_avg_price) -\
                      (sum(order.cum_fees_quote for ord_id in active_order_list for order in self._open_orders if order.order_id == ord_id) +\
                       in_flight_order.cumulative_fee_paid(in_flight_order.quote_asset))
                # 更新对应open_orders(剔除了被配对的Open_order)
                active_order.order = in_flight_order
                self._open_orders = [order for order in self._open_orders if order.order_id not in active_order_list]

        # open 事件
        active_order = next((order for order in self._open_orders if order.order_id == order_id), None)
        if active_order:
            in_flight_order = self.get_in_flight_order(self.config.connector_name, order_id)
            if in_flight_order:
                
                # 更新持仓平均成本
                self._strategy.logger().info(
                    f"更新均价,此时均价为:{self._pos_avg_price},仓位:{self.open_filled_amount},订单价格:{active_order.average_executed_price }\
                        订单成交量:{active_order.executed_amount_base}"
                )
                self._pos_avg_price = (self._pos_avg_price * self.open_filled_amount +\
                                        in_flight_order.average_executed_price * in_flight_order.executed_amount_base)/ \
                                        (self.open_filled_amount + in_flight_order.executed_amount_base)
                self._strategy.logger().info(
                    f"更新均价,更新后均价为:{self._pos_avg_price}"
                )
                active_order.order = in_flight_order


    def process_order_created_event(self, _, market, event: Union[BuyOrderCreatedEvent, SellOrderCreatedEvent]):
        """
        订单被创建成功后,更新本地订单序列
        """
        # all_orders = self._open_orders + self._close_all_orders
        # active_order = next((order for order in all_orders if order.order_id == event.order_id), None)
        # if active_order:
        #     in_flight_order = self.get_in_flight_order(self.config.connector_name, event.order_id)
        #     if in_flight_order:
        #         active_order.order = in_flight_order
        
        # active_order = next((tracked_order for _, tracked_order in self._close_orders if tracked_order.order_id == event.order_id), None)
        # if active_order:
        #     in_flight_order = self.get_in_flight_order(self.config.connector_name, event.order_id)
        #     if in_flight_order:
        #         active_order.order = in_flight_order
        pass

    def process_order_filled_event(self, _, market, event: OrderFilledEvent):
        """
        This method is responsible for processing the order filled event. Here we will update the value of
        _total_executed_amount_backup, that can be used if the InFlightOrder
        is not available.
        """

        self._total_executed_amount_backup += event.amount
        self.update_tracked_orders_with_order_id(event.order_id)

    def process_order_failed_event(self, _, market, event: MarketOrderFailureEvent):
        """
        This method is responsible for processing the order failed event. Here we will add the InFlightOrder to the
        failed orders list.
        """
        open_order = next((order for order in self._open_orders if order.order_id == event.order_id), None)
        if open_order:
            self._failed_orders.append(open_order)
            self._open_orders.remove(open_order)
            self.logger().error(f"Order {event.order_id} failed.")
        close_order = next((order for _, order in self._close_orders if order.order_id == event.order_id), None)
        if close_order:
            self._failed_orders.append(close_order)
            self._close_orders.remove(close_order)
            self.logger().error(f"Order {event.order_id} failed.")
            self._current_retries += 1

    async def control_shutdown_process(self):
        """
        This method is responsible for shutting down the process, ensuring that all orders are completed.
        """
        if math.isclose(self.open_filled_amount, self.close_filled_amount):
            self.close_execution_by(self.close_type)
        elif len(self.active_close_orders) > 0:
            self.logger().info(f"Waiting for close order ")
        else:
            self.logger().info(f"Open amount")
            self.place_close_order_and_cancel_open_orders()
            self._current_retries += 1
        await asyncio.sleep(1.0)
        await self.stop_queue.put(self.config.trading_pair)

    def close_execution_by(self, close_type):
        self.close_type = close_type
        self.close_timestamp = self._strategy.current_timestamp
        self.stop()
    
    def get_custom_info(self) -> Dict:
        return {
            "side": self.config.side,
            "current_retries": self._current_retries,
            "max_retries": self._max_retries
        }

    def to_format_status(self, scale=1.0):
        lines = []
        lines.extend(["-----------------------------------------------------------------------------------------------------------"])
        return lines

    def validate_sufficient_balance(self):
        if self.is_perpetual:
            order_candidate = PerpetualOrderCandidate(
                trading_pair=self.config.trading_pair,
                is_maker=OrderType.LIMIT,
                order_type=OrderType.LIMIT,
                order_side=self.config.side,
                amount=self.config.amount_quote,
                price=self.current_market_price,
                leverage=Decimal(self.config.leverage),
            )
        else:
            order_candidate = OrderCandidate(
                trading_pair=self.config.trading_pair,
                is_maker=OrderType.LIMIT,
                order_type=OrderType.LIMIT,
                order_side=self.config.side,
                amount=self.config.amount_quote,
                price=self.current_market_price,
            )
        adjusted_order_candidates = self.adjust_order_candidates(self.config.connector_name, [order_candidate])
        if adjusted_order_candidates[0].amount == Decimal("0"):
            self.close_type = CloseType.INSUFFICIENT_BALANCE
            self.logger().error("Not enough budget to open position.")
            self.stop()
