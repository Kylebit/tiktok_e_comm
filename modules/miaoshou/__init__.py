"""妙手开放平台客户端（仅 API 封装，不接入商品目录）。"""
from modules.miaoshou.client import generate_sign, get_shop_list, load_config, post_open

__all__ = ["generate_sign", "get_shop_list", "load_config", "post_open"]
