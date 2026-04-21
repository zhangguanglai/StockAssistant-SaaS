#!/usr/bin/env python3
"""简化版回测逻辑测试"""
import sys
sys.path.insert(0, 'c:\\Users\\13826\\WorkBuddy\\Claw\\stock-portfolio')

from helpers import pro
from screener import get_recent_trade_dates
import pandas as pd
from datetime import datetime

print("=== 选股回测逻辑分析 ===\n")

# 获取交易日历
total_days = 15 + 5 + 10  # days_back + hold_days + buffer
all_dates = get_recent_trade_dates(total_days)
print(f"获取到 {len(all_dates)} 个交易日")

if not all_dates:
    print("无法获取交易日历")
    sys.exit(1)

all_dates_asc = list(reversed(all_dates))
eval_end_idx = max(0, len(all_dates_asc) - 5 - 3)
eval_dates = all_dates_asc[max(0, eval_end_idx - 15): eval_end_idx]

print(f"回测评估日期数: {len(eval_dates)}")
print(f"评估日期范围: {eval_dates[0] if eval_dates else 'N/A'} ~ {eval_dates[-1] if eval_dates else 'N/A'}")
print(f"持有期: 5天\n")

# 模拟筛选逻辑
def simulate_screening(date_str):
    """模拟某一天的选股逻辑"""
    try:
        df = pro.daily_basic(
            trade_date=date_str,
            fields="ts_code,trade_date,close,pct_chg,turnover_rate,circ_mv"
        )
        if df is None or df.empty:
            return 0, 0
        
        # 筛选条件（与回测一致）
        candidates = df[
            (df["pct_chg"].between(-2, 3)) &
            (df["turnover_rate"] > 1.0) &
            (df["circ_mv"] > 50 * 10000) &
            (~df["ts_code"].str.contains("BJ"))
        ]
        
        return len(df), len(candidates)
    except Exception as e:
        print(f"  获取 {date_str} 数据失败: {e}")
        return 0, 0

# 测试最近3天的筛选情况
print("最近3个交易日筛选情况:")
test_dates = eval_dates[-3:] if len(eval_dates) >= 3 else eval_dates
for date in test_dates:
    total, passed = simulate_screening(date)
    print(f"  {date}: 全市场 {total} 只 -> 筛选通过 {passed} 只")

print("\n=== 回测逻辑评估 ===")
print("""
当前回测逻辑说明:
1. 筛选条件:
   - 日涨跌幅在 -2% ~ +3% 之间
   - 换手率 > 1%
   - 流通市值 > 50亿
   - 排除北交所股票

2. 回测机制:
   - 在每一天筛选符合条件的股票
   - 模拟买入当天收盘价
   - 持有5天后按收盘价卖出
   - 计算收益率

3. 输出指标:
   - 胜率: 盈利交易占比
   - 平均收益: 所有交易平均收益率
   - 盈亏比: 平均盈利/平均亏损
   - 最大回撤: 累计收益最大回落
""")

print("=== 设计问题分析 ===")
print("""
当前设计存在的问题:

1. 【策略不匹配】
   - 回测使用固定筛选条件（涨跌幅-2%~3%）
   - 与前端"趋势突破/板块龙头/超跌反弹"三套策略无关
   - 用户无法知道当前回测对应哪套策略

2. 【价值不明确】
   - 用户不清楚回测结果如何指导实际操作
   - "胜率55%"对用户的决策意义是什么？
   - 没有与当前市场环境的关联

3. 【使用方式模糊】
   - "周期15天/持有5天"的参数含义不清
   - 用户不知道何时应该运行回测
   - 回测结果不会自动更新

4. 【交互问题】
   - 需要手动点击"开始回测"
   - 没有默认展示，用户可能忽略此功能
   - 交易明细折叠，不易发现
""")
