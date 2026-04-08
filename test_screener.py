# -*- coding: utf-8 -*-
"""选股引擎链路测试脚本"""

import time
from screener import (
    get_stock_list, basic_filter, trend_confirm,
    sector_and_money_filter, get_concept_board_data,
    bonus_scoring, final_ranking,
    get_stock_concepts_eastmoney, get_stock_money_flow,
    check_market_environment, run_screener
)

print("=" * 60)
print("  选股引擎链路验证测试")
print("=" * 60)

# 大盘环境
market = check_market_environment()
print("\n[大盘环境]", market["description"])
print("  状态:", market["status"])

# 获取股票列表
stocks = get_stock_list()
print("\n[全市场股票] 共", len(stocks), "只")

# 取前200只测试
test_stocks = stocks[:200]
print("[测试样本] 取前", len(test_stocks), "只")

# Step2: 基础过滤
daily_cache = {}
basic_cache = {}
t0 = time.time()
after_basic = basic_filter(test_stocks, daily_cache, basic_cache)
print("\n[Step2 基础过滤] 通过:", len(after_basic), "只  耗时:", round(time.time()-t0, 1), "s")

# Step3: 趋势确认
after_trend = trend_confirm(after_basic)
print("[Step3 趋势确认] 通过:", len(after_trend), "只")
for p in after_trend[:5]:
    print("  ", p["ts_code"], p["name"],
          "MA20:", p["ma20"], "偏离:", p["deviation"], "%",
          "斜率:", p["ma20_slope"], "趋势分:", p["trend_score"])

# 板块数据
boards = get_concept_board_data()
top3_boards = sorted(boards, key=lambda x: x["change_pct"], reverse=True)[:3]
print("\n[板块数据] 共", len(boards), "个概念板块")
print("  Top3涨幅板块:")
for b in top3_boards:
    print("   ", b["concept_name"], b["change_pct"], "%")

# Step4: 板块资金过滤
if after_trend:
    sample = after_trend[:10]
    t0 = time.time()
    after_sector = sector_and_money_filter(sample, boards)
    print("\n[Step4 板块资金过滤] 样本", len(sample), "->", len(after_sector), "只  耗时:", round(time.time()-t0, 1), "s")

    # 诊断未通过的原因
    if len(after_sector) == 0:
        print("  [诊断] 逐一检查未通过原因:")
        board_map = {b["concept_name"]: b["change_pct"] for b in boards}
        for c in sample[:5]:
            concepts = get_stock_concepts_eastmoney(c["ts_code"])
            max_pct = max((board_map.get(cn, 0) for cn in concepts), default=0)
            flow = get_stock_money_flow(c["ts_code"], 5)
            total = sum(m["main_net_in"] for m in flow)
            days_in = sum(1 for m in flow if m["main_net_in"] > 0)
            board_ok = max_pct > 0.5
            money_ok = total > 0 and days_in >= 2
            print("   ", c["ts_code"], c["name"])
            print("      概念:", concepts[:3], "最强板块涨幅:", max_pct, "[OK]" if board_ok else "[FAIL](需>0.5%)")
            print("      5日净流入:", round(total/10000, 1), "万 days:", days_in,
                  "[OK]" if money_ok else "[FAIL](需>0且days>=2)")

    if after_sector:
        scored = bonus_scoring(after_sector, boards)
        results = final_ranking(scored)
        print("\n[最终结果]", len(results), "只")
        for i, r in enumerate(results):
            print("  #%d %s(%s) 总分:%d 板块:%s(%.1f%%) 净流入:%s万" % (
                i+1, r["name"], r["ts_code"], r["total_score"],
                r.get("max_board_name", ""), r.get("max_board_pct", 0),
                r.get("total_net_in", 0)
            ))
else:
    print("[Step4] 跳过（趋势确认后0只）")

print("\n" + "=" * 60)
print("  测试完成")
print("=" * 60)
