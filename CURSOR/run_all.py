#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
总脚本：依次执行项目内各子脚本。
- 成本填写页生成（product_cost）
- 订单利润页生成（Income_Data）
- 总利润页生成（Income_Data）
- 月度利润表生成（根目录）

在项目根目录运行：python3 run_all.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SCRIPTS = [
    ("generate_sku_cost_page.py", "生成成本填写页"),
    ("build_order_profit_page.py", "生成订单利润页"),
    ("build_total_profit_page.py", "生成总利润页"),
    ("build_profit_table.py", "生成月度利润表"),
]


def main():
    print("=" * 60)
    print("TikTok 店铺利润与成本 — 一键执行全部脚本")
    print("=" * 60)
    for rel_path, desc in SCRIPTS:
        script = ROOT / rel_path
        if not script.is_file():
            print(f"\n[跳过] {desc}: 未找到 {rel_path}")
            continue
        print(f"\n>>> {desc}: {rel_path}")
        print("-" * 50)
        ret = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
        )
        if ret.returncode != 0:
            print(f"[失败] {rel_path} 退出码 {ret.returncode}")
            return ret.returncode
    print("\n" + "=" * 60)
    print("全部完成。")
    print("  - 成本填写页: product_cost/sku_cost_input.html")
    print("  - 订单利润页: Income_Data/Output/Outprofit_*.html")
    print("  - 总利润页:   Income_Data/Output/TotalProfit.html")
    print("  - 月度利润表: profit_table.csv")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
