import asyncio
import importlib
import inspect
import os
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set

import pandas as pd
import yaml
from pydantic import Field, validator

from hummingbot.client import settings
from hummingbot.client.config.config_data_types import BaseClientModel, ClientFieldData
from hummingbot.client.ui.interface_utils import format_df_for_printout
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import PositionMode
from hummingbot.core.event.events import ExecutorEvent
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.exceptions import InvalidController
from hummingbot.smart_components.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.smart_components.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerConfigBase,
)
from hummingbot.smart_components.controllers.market_making_controller_base import MarketMakingControllerConfigBase
from hummingbot.smart_components.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.smart_components.models.base import SmartComponentStatus
from hummingbot.smart_components.models.executor_actions import (
    CreateExecutorAction,
    ExecutorAction,
    StopExecutorAction,
    StoreExecutorAction,
)
from hummingbot.smart_components.models.executors_info import ExecutorInfo
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.strategy.strategy_v2_base import  StrategyV2ConfigBase

class StrategyV3Config(BaseClientModel):
    """
    Base class for version 2 strategy configurations.
    """

    '''
    markets: Dict[str, Set[str]] = {
        "binance_perpetual": set(
            ["BTC-USDT"]
        )
    }
    '''
    markets: Dict[str, Set[str]] = {
    }
    controller_type: str 

    controller_name: str 

    para_config: str

    position_mode = PositionMode.HEDGE

    controllers_config: List[str] = Field(
        default=None,
        client_data=ClientFieldData(
            is_updatable=True,
            prompt_on_new=True,
            prompt=lambda mi: "Enter controller configurations (comma-separated file paths), leave it empty if none: "
        ))
    
    config_update_interval: int = Field(
        default=60,
        gt=0,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Enter the config update interval in seconds (e.g. 60): ",
        )
    )

    def load_controller_configs(self):
        # todo
        loaded_configs = []
        for config_path in self.controllers_config:
            full_path = os.path.join(settings.CONTROLLERS_CONF_GRID_DIR_PATH, config_path)
            with open(full_path, 'r') as file:
                config_data = yaml.safe_load(file)
            
            ## 这里读取参数更新配置文件
            para_path = os.path.join(settings.PARAMETERS_CONF_GRID_DIR_PATH, self.para_config)
            with open(para_path, "r") as file:
                para_data = yaml.safe_load(file)

            ## 这里将param_config文件中取值分配给params和candles
            config_data["candles_config"] = []
            for market, pairs in config_data["markets"].items():
                for pair in pairs:
                    if pair in para_data:
                        config_data["params"].update({
                            pair: para_data[pair]
                        })
                        config_data["candles_config"].append(CandlesConfig(
                                connector=market,
                                trading_pair=pair,
                                interval=para_data[pair]["interval"],
                                max_records=para_data[pair]["max_records"]
                        ))
                    else:
                        raise IndexError
            
            controller_type = self.controller_type
            controller_name = self.controller_name

            if not controller_type or not controller_name:
                raise ValueError(f"Missing controller_type or controller_name in {config_path}")

            module_path = f"{settings.CONTROLLERS_MODULE}.{controller_type}.{controller_name}"
            module = importlib.import_module(module_path)

            config_class = next((member for member_name, member in inspect.getmembers(module)
                                 if inspect.isclass(member) and member not in [ControllerConfigBase,
                                                                               MarketMakingControllerConfigBase,
                                                                               DirectionalTradingControllerConfigBase]
                                 and (issubclass(member, ControllerConfigBase))), None)
            if not config_class:
                raise InvalidController(f"No configuration class found in the module {controller_name}.")

            loaded_configs.append(config_class(**config_data))

        return loaded_configs



class StrategyV3Base(ScriptStrategyBase):
    """
    V2StrategyBase is a base class for strategies that use the new smart components architecture.
    """
    markets: Dict[str, Set[str]]
    _last_config_update_ts: float = 0
    closed_executors_buffer: int = 5

    @classmethod
    def init_markets(cls, config: StrategyV3Config):
        """
        Initialize the markets that the strategy is going to use. This method is called when the strategy is created in
        the start command. Can be overridden to implement custom behavior.
        """
        markets = config.markets
        controllers_configs = config.load_controller_configs()
        for controller_config in controllers_configs:
            markets = controller_config.update_markets(markets)
        cls.markets = markets

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[StrategyV3Config] = None):
        super().__init__(connectors, config)
        # Initialize the executor orchestrator
        self.config = config
        self.executor_orchestrator = ExecutorOrchestrator(strategy=self)

        self.executors_info: Dict[str, List[ExecutorInfo]] = {}

        # Create a queue to listen to actions from the controllers
        self.actions_queue = asyncio.Queue()
        self.listen_to_executor_actions_task: asyncio.Task = asyncio.create_task(self.listen_to_executor_actions())

        # Initialize the market data provider
        self.market_data_provider = MarketDataProvider(connectors)
        # self.market_data_provider.initialize_candles_feed_list(config.candles_config)
        self.controllers: Dict[str, ControllerBase] = {}
        self.initialize_controllers()

        self.loop = asyncio.get_event_loop()

    def initialize_controllers(self):
        """
        Initialize the controllers based on the provided configuration.
        """
        controllers_configs = self.config.load_controller_configs()
        for controller_config in controllers_configs:
            self.add_controller(controller_config)
            MarketsRecorder.get_instance().store_controller_config(controller_config)



    def add_controller(self, config: ControllerConfigBase):
        try:
            # todo
            for connector_name, connector in self.connectors.items():
                if self.is_perpetual(connector_name):
                    connector.set_position_mode(self.config.position_mode)
                    for trading_pair in config.markets[connector_name]:
                        connector.set_leverage(trading_pair, config.params[trading_pair]['leverage'])
            controller = config.get_controller_class()(config, self.market_data_provider, self.actions_queue)
            controller.start()
            ##  又改版了，服
            self.controllers[config.id] = controller
        except Exception as e:
            self.logger().error(f"Error adding controller: {e}", exc_info=True)

    def update_controllers_configs(self):
        """
        Update the controllers configurations based on the provided configuration.
        """
        if self._last_config_update_ts + self.config.config_update_interval < self.current_timestamp:
            self._last_config_update_ts = self.current_timestamp
            controllers_configs = self.config.load_controller_configs()
            for controller_config in controllers_configs:
                if controller_config.id in self.controllers:
                    ## 这样是否能够异步调用成功？
                    if not self.loop.is_running():
                        self.loop.run_until_complete(self.controllers[controller_config.id].update_config(controller_config))
                else:
                    self.add_controller(controller_config)

    async def listen_to_executor_actions(self):
        """
        Asynchronously listen to actions from the controllers and execute them.
        """
        while True:
            try:
                actions = await self.actions_queue.get()
                if isinstance(actions, ExecutorInfo):
                    self.executor_orchestrator.execute_actions(actions)
                    self.update_executors_info()
                    controller_id = actions[0].controller_id
                    controller = self.controllers.get(controller_id)
                    controller.executors_info = self.executors_info.get(controller_id, [])
                    controller.executors_update_event.set()
                ## 这里更新杠杆率
                elif isinstance(actions, tuple) and "-" in actions:
                    for connector_name, connector in self.connectors.items():
                        connector.set_leverage(actions[0], actions[1])
            except Exception as e:
                self.logger().error(f"Error executing action: {e}", exc_info=True)

    def update_executors_info(self):
        """
        Update the local state of the executors and publish the updates to the active controllers.
        """
        try:
            self.executors_info = self.executor_orchestrator.get_executors_report()
            for controllers in self.controllers.values():
                controllers.executors_info = self.executors_info.get(controllers.config.id, [])
        except Exception as e:
            self.logger().error(f"Error updating executors info: {e}", exc_info=True)

    @staticmethod
    def is_perpetual(connector: str) -> bool:
        return "perpetual" in connector

    def on_stop(self):
        self.executor_orchestrator.stop()
        self.market_data_provider.stop()
        self.listen_to_executor_actions_task.cancel()
        self.loop.close()
        for controller in self.controllers.values():
            controller.stop()

    def on_tick(self):
        self.update_executors_info()
        self.update_controllers_configs()
        
        if self.market_data_provider.ready:
            executor_actions: List[ExecutorAction] = self.determine_executor_actions()
            for action in executor_actions:
                self.executor_orchestrator.execute_action(action)

    def determine_executor_actions(self) -> List[ExecutorAction]:
        """
        Determine actions based on the provided executor handler report.
        """
        actions = []
        actions.extend(self.create_actions_proposal())
        actions.extend(self.stop_actions_proposal())
        actions.extend(self.store_actions_proposal())
        return actions

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        """
        Create actions proposal based on the current state of the executors.
        """
        raise NotImplementedError

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        """
        Create a list of actions to stop the executors based on order refresh and early stop conditions.
        """
        raise NotImplementedError

    def store_actions_proposal(self) -> List[StoreExecutorAction]:
        """
        Create a list of actions to store the executors that have been stopped.
        """
        potential_executors_to_store = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda x: x.is_done)
        sorted_executors = sorted(potential_executors_to_store, key=lambda x: x.timestamp, reverse=True)
        if len(sorted_executors) > self.closed_executors_buffer:
            return [StoreExecutorAction(executor_id=executor.id, controller_id=executor.controller_id) for executor in
                    sorted_executors[self.closed_executors_buffer:]]
        return []

    def get_executors_by_controller(self, controller_id: str) -> List[ExecutorInfo]:
        return self.executors_info.get(controller_id, [])

    def get_all_executors(self) -> List[ExecutorInfo]:
        return [executor for executors in self.executors_info.values() for executor in executors]

    def set_leverage(self, connector: str, trading_pair: str, leverage: int):
        self.connectors[connector].set_leverage(trading_pair, leverage)

    def set_position_mode(self, connector: str, position_mode: PositionMode):
        self.connectors[connector].set_position_mode(position_mode)

    @staticmethod
    def filter_executors(executors: List[ExecutorInfo], filter_func: Callable[[ExecutorInfo], bool]) -> List[ExecutorInfo]:
        return [executor for executor in executors if filter_func(executor)]

    @staticmethod
    def executors_info_to_df(executors_info: List[ExecutorInfo]) -> pd.DataFrame:
        """
        Convert a list of executor handler info to a dataframe.
        """
        df = pd.DataFrame([ei.dict() for ei in executors_info])
        # Convert the enum values to integers
        df['status'] = df['status'].apply(lambda x: x.value)

        # Sort the DataFrame
        df.sort_values(by='status', ascending=True, inplace=True)

        # Convert back to enums for display
        df['status'] = df['status'].apply(SmartComponentStatus)
        return df[["id", "timestamp", "type", "status", "net_pnl_pct", "net_pnl_quote", "cum_fees_quote", "is_trading",
                   "filled_amount_quote", "close_type"]]

    def format_status(self) -> str:
        original_info = super().format_status()
        columns_to_show = ["id", "type", "status", "net_pnl_pct", "net_pnl_quote", "cum_fees_quote",
                           "filled_amount_quote", "is_trading", "close_type", "age"]
        extra_info = []

        # Initialize global performance metrics
        global_realized_pnl_quote = Decimal(0)
        global_unrealized_pnl_quote = Decimal(0)
        global_volume_traded = Decimal(0)
        global_close_type_counts = {}

        # Process each controller
        for controller_id, controller in self.controllers.items():
            extra_info.append(f"\n\nController: {controller_id}")
            # Append controller market data metrics
            extra_info.extend(controller.to_format_status())
            executors_list = self.get_executors_by_controller(controller_id)
            if len(executors_list) == 0:
                extra_info.append("No executors found.")
            else:
                # In memory executors info
                executors_df = self.executors_info_to_df(executors_list)
                executors_df["age"] = self.current_timestamp - executors_df["timestamp"]
                extra_info.extend([format_df_for_printout(executors_df[columns_to_show], table_format="psql")])

            # Generate performance report for each controller
            performance_report = self.executor_orchestrator.generate_performance_report(controller_id)

            # Append performance metrics
            controller_performance_info = [
                f"Realized PNL (Quote): {performance_report.realized_pnl_quote:.2f} | Unrealized PNL (Quote): {performance_report.unrealized_pnl_quote:.2f}"
                f"--> Global PNL (Quote): {performance_report.global_pnl_quote:.2f} | Global PNL (%): {performance_report.global_pnl_pct:.2f}%",
                f"Total Volume Traded: {performance_report.volume_traded:.2f}"
            ]

            # Append close type counts
            if performance_report.close_type_counts:
                controller_performance_info.append("Close Types Count:")
                for close_type, count in performance_report.close_type_counts.items():
                    controller_performance_info.append(f"  {close_type}: {count}")

            # Aggregate global metrics and close type counts
            global_realized_pnl_quote += performance_report.realized_pnl_quote
            global_unrealized_pnl_quote += performance_report.unrealized_pnl_quote
            global_volume_traded += performance_report.volume_traded
            global_close_type_counts.update(performance_report.close_type_counts)
            extra_info.extend(controller_performance_info)

        main_executors_list = self.get_executors_by_controller("main")
        if len(main_executors_list) > 0:
            extra_info.append("\n\nMain Controller Executors:")
            main_executors_df = self.executors_info_to_df(main_executors_list)
            main_executors_df["age"] = self.current_timestamp - main_executors_df["timestamp"]
            extra_info.extend([format_df_for_printout(main_executors_df[columns_to_show], table_format="psql")])

        # Calculate and append global performance metrics
        global_pnl_quote = global_realized_pnl_quote + global_unrealized_pnl_quote
        global_pnl_pct = (global_pnl_quote / global_volume_traded) * 100 if global_volume_traded != 0 else Decimal(0)

        global_performance_summary = [
            "\n\nGlobal Performance Summary:",
            f"Global PNL (Quote): {global_pnl_quote:.2f} | Global PNL (%): {global_pnl_pct:.2f}% | Total Volume Traded (Global): {global_volume_traded:.2f}"
        ]

        # Append global close type counts
        if global_close_type_counts:
            global_performance_summary.append("Global Close Types Count:")
            for close_type, count in global_close_type_counts.items():
                global_performance_summary.append(f"  {close_type}: {count}")

        extra_info.extend(global_performance_summary)

        # Combine original and extra information
        format_status = f"{original_info}\n\n" + "\n".join(extra_info)
        return format_status
