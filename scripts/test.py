import yaml
from pprint import pprint


if __name__ == "__main__":
    full_path = "/root/hummingbot/conf_grid/controllers/trailing_grid_v1.yaml"
    with open(full_path, 'r') as file:
        config_data = yaml.safe_load(file)
    
    pprint (config_data)
    
    ## 这里读取参数更新配置文件
    para_path = "/root/hummingbot/conf_grid/para_config/grid_config.yaml"
    with open(para_path, "r") as file:
        para_data = yaml.safe_load(file)

    pprint (para_data)
    config_data["candles_config"] = []
    for market, pairs in config_data["markets"].items():
        for pair in pairs:
            if pair in para_data:
                config_data["params"].update({
                    pair: para_data[pair]
                })
                config_data["candles_config"].append(
                    (market,
                     pair,
                     para_data[pair]["interval"],
                     para_data[pair]["max_records"]
                    )

                )
            else:
                raise IndexError
    
    pprint (config_data)
    