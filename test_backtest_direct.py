#!/usr/bin/env python3
"""直接测试选股回测功能"""
import sys
sys.path.insert(0, 'c:\\Users\\13826\\WorkBuddy\\Claw\\stock-portfolio')

from flask import Flask
from database import init_db

app = Flask(__name__)
init_db(app)

with app.app_context():
    from routes.position_bp import run_backtest
    
    # 模拟请求上下文
    with app.test_request_context('/api/backtest?days=15&hold=5'):
        response = run_backtest()
        data = response.get_json()
        
        if 'error' in data:
            print(f"Error: {data['error']}")
        else:
            print('=== 回测结果 ===')
            print(f"周期: {data.get('period', 'N/A')}")
            print(f"持有天数: {data.get('hold_days', 'N/A')}天")
            print(f"信号数: {data.get('total_signals', 0)}")
            print(f"胜率: {data.get('win_rate', 0)}%")
            print(f"平均收益: {data.get('avg_return', 0)}%")
            print(f"盈亏比: {data.get('profit_loss_ratio', 0)}")
            print(f"最大回撤: {data.get('max_drawdown', 0)}%")
            print(f"扫描标的: {data.get('screened_total', 0)}")
            print(f"\n结论: {data.get('summary', '')}")
            print(f"\n交易明细数: {len(data.get('trades', []))}")
            
            # 显示前5笔交易
            trades = data.get('trades', [])
            if trades:
                print("\n前5笔交易:")
                for t in trades[:5]:
                    print(f"  {t['date']} {t['ts_code']}: 买入{t['buy_price']} -> 卖出{t['sell_price']} = {t['return_pct']}%")
