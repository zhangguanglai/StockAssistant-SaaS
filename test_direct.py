# -*- coding: utf-8 -*-
"""直接调用极简龙头策略函数测试（不走HTTP）"""
import sys
sys.path.insert(0, r'c:\Users\13826\WorkBuddy\Claw\stock-portfolio')

from screener import run_sector_leader_screener, STRATEGY_PARAMS

print('=== STRATEGY_PARAMS ===')
sp = STRATEGY_PARAMS['sector_leader']
print(f'params count: {len(sp)}')
for p in sp:
    print(f'  {p["key"]}: {p["label"]} = {p["value"]} (range: {p["min"]}-{p["max"]})')

print('\n=== RUN SCREENER ===')
result = run_sector_leader_screener(top_n=10, silent=False)

# 打印结果
if result:
    stats = result.get('stats', {})
    print(f'\nmessage: {stats.get("message")}')
    
    results = result.get('results', [])
    print(f'top results: {len(results)}')
    for i, x in enumerate(results[:9]):
        rec = x.get('recommendation', '?')
        name = x.get('name', '?')
        code = x.get('ts_code', '?')
        board = x.get('max_board_name', '?')
        bpct = x.get('max_board_pct', 0)
        pct = x.get('pct_chg', 0)
        turn = x.get('turnover_rate', 0)
        mv = x.get('circ_mv_yi', 0)
        reason = x.get('reason', '')
        print(f'  #{i+1} [{rec}] {name}({code}) | board:{board}({bpct:.1f}%) | pct:{pct:.2f}% | turn:{turn:.1f}% | mv:{mv:.0f}Yi')
        print(f'       reason: {reason}')

    # 审计验证
    if results:
        a = results[0].get('match_audit', {})
        print(f'\n=== AUDIT ===')
        print(f'strategy: {a.get("strategy")} v{a.get("version")}')
        for g in a.get('gates', []):
            print(f'  gate: {g["name"]} | actual={g["actual"]} | threshold={g["threshold"]} | passed={g["passed"]}')
