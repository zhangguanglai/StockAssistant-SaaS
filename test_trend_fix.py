# -*- coding: utf-8 -*-
"""测试趋势突破策略 - 验证板块数据过期检测"""

from screener import run_screener

result = run_screener(top_n=5, silent=False, force=True)

print("\n" + "="*60)
print("最终结果")
print("="*60)
print(f"入选: {len(result.get('top_stocks', []))} 只")
print(f"消息: {result.get('stats', {}).get('message', 'N/A')}")
print(f"各阶段通过数:")
print(f"  - 基础过滤: {result.get('stats', {}).get('after_basic', 0)}")
print(f"  - 趋势确认: {result.get('stats', {}).get('after_trend', 0)}")
print(f"  - 板块资金: {result.get('stats', {}).get('after_sector', 0)}")
print(f"  - 最终入选: {result.get('stats', {}).get('final_count', 0)}")
