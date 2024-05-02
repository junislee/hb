import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Set
import pandas_ta as ta

from pydantic import Field

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig

from hummingbot.smart_components.controllers.grid_controller_base import (
    GridControllerBase,
    GridControllerConfigBase,
)
from hummingbot.smart_components.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.smart_components.models.executor_actions import ExecutorAction, StopExecutorAction


class GridConfig(GridControllerConfigBase):
    controller_name = "Grid_test"

    markets: Dict[str, Set[str]] = {
        "binance_perpetual": set(
            ["DOGE-USDT", "WIF-USDT", "ONG-USDT"]
        )
    }




class GridController(GridControllerBase):
    def __init__(self, config: GridConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config

    def get_signal(self) -> int:
        ## controller内部执行逻辑
        return 1


