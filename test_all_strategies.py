# -*- coding: utf-8 -*-
"""测试运行三套选股策略"""

from screener import run_screener, run_sector_leader_screener, run_oversold_bounce_screener

def test_trend_break():
    """测试趋势突破策略"""
    print("\n" + "="*60)
    print("【策略1】趋势突破策略")
    print("="*60)
    result = run_screener(top_n=10, silent=False, force=True)
    print("\n--- 最终入选 ---")
    for i, r in enumerate(result.get('top_stocks', [])):
        print(f"{i+1}. {r['ts_code']} {r.get('name','')} - 评分:{r.get('total_score',0)}")
    if not result.get('top_stocks'):
        print("无入选股票")
    return result

def test_sector_leader():
    """测试板块龙头首板策略"""
    print("\n" + "="*60)
    print("【策略2】板块龙头首板策略")
    print("="*60)
    result = run_sector_leader_screener(top_n=10, silent=False)
    print("\n--- 最终入选 ---")
    for i, r in enumerate(result.get('top_stocks', [])):
        print(f"{i+1}. {r['ts_code']} {r.get('name','')} - 评分:{r.get('total_score',0)}")
    if not result.get('top_stocks'):
        print("无入选股票")
    return result

def test_oversold_bounce():
    """测试超跌反弹策略"""
    print("\n" + "="*60)
    print("【策略3】超跌反弹策略")
    print("="*60)
    result = run_oversold_bounce_screener(top_n=10, silent=False)
    print("\n--- 最终入选 ---")
    for i, r in enumerate(result.get('top_stocks', [])):
        print(f"{i+1}. {r['ts_code']} {r.get('name','')} - 评分:{r.get('total_score',0)}")
    if not result.get('top_stocks'):
        print("无入选股票")
    return result

if __name__ == "__main__":
    import sys
    
    # 运行所有策略
    print("\n" + "#"*60)
    print("# 开始测试三套选股策略")
    print("#"*60)
    
    # 策略1: 趋势突破
    r1 = test_trend_break()
    
    # 策略2: 板块龙头
    r2 = test_sector_leader()
    
    # 策略3: 超跌反弹
    r3 = test_oversold_bounce()
    
    # 汇总
    print("\n" + "#"*60)
    print("# 测试结果汇总")
    print("#"*60)
    print(f"趋势突破策略: 入选 {len(r1.get('top_stocks', []))} 只")
    print(f"板块龙头策略: 入选 {len(r2.get('top_stocks', []))} 只")
    print(f"超跌反弹策略: 入选 {len(r3.get('top_stocks', []))} 只")
