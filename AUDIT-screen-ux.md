# 选股模块交互设计审查报告

> 审查日期：2026-04-15  
> 审查角色：UX架构师（技术架构+产品视角）  
> 当前版本：v3.0（趋势突破评分制重构后）

---

## 一、现状结构分析

### 1.1 当前页面布局（从上到下）

```
┌─────────────────────────────────────────────────────┐
│  Tab导航：持仓(5) | 🔍选股 | 复盘 | 自选            │
├──────────┬──────────┬───────────────────────────────┤
│ 🌍大盘环境 │ 🎯符合条件 │ ⏱️上次运行                │  ← 3张汇总卡片
│ 📈上升    │ 0只       │ 未运行                      │
├──────────┴──────────┴───────────────────────────────┤
│ 🤖 智能推荐提示条（动态显示/隐藏）                    │
├────────┬──────────┬───────────────────────────────┐
│📈趋势突破│🏆板块龙头│🔄超跌反弹                     │  ← 3张策略卡片
│✓选中   │         │                               │  （点击展开详情）
│买入信号│买入信号  │买入信号                        │
│持股周期│持股周期  │持股周期                        │
│止损位  │止损位    │止损位                          │
├────────┴──────────┴───────────────────────────────┤
│ [▶ 趋势突破] [📈胜率xx%] [🧪强制测试]              │  ← 操作栏
├─────────────────────────────────────────────────────┤
│ 全市场5200 → 基础800 → 趋势120 → 板块资金3 → 0只入选 │  ← 筛选漏斗
├─────────────────────────────────────────────────────┤
│ 结果表格（或空状态）                                 │  ← 主内容区
├─────────────────────────────────────────────────────┤
│ 📜 历史选股记录（近7天）                            │  ← 底部历史
└─────────────────────────────────────────────────────┘
```

### 1.2 数据依赖关系

| 组件 | 数据源 | 问题 |
|------|--------|------|
| 大盘环境卡 | `screenMarket` | 仅在选股完成后才填充，平时为null→显示`-` |
| 符合条件卡 | `screenStats.final_count` | 同上，未运行时永远0 |
| 上次运行卡 | `screenInfo.lastRun` | 未运行时显示"未运行"，信息量≈0 |
| 策略卡片 | `strategyListSafe`(静态) + `currentStrategy` | 不含任何执行状态/结果数据 |
| 结果表格 | `screenResults` | 只显示当前选中策略的结果 |

### 1.3 核心问题诊断

#### ❌ 问题1：顶部3张汇总卡片 = "死空间"

| 卡片 | 首次进入时显示 | 信息价值 | 占用面积 |
|------|---------------|---------|---------|
| 🌍 大盘环境 | `-`（无数据） | 0 | ~200px宽 |
| 🎯 符合条件 | `0只`（无意义） | 0 | ~200px宽 |
| ⏱️ 上次运行 | `未运行`（废话） | 0 | ~200px宽 |

**结论**：这3张卡片在90%的访问时间里都是空的或无意义的，白白占据首屏~180px高度。用户必须向下滚动才能看到策略选择器和操作按钮。

#### ❌ 问题2：策略卡片与执行结果脱节

当前策略卡片只展示**静态元数据**（名称/图标/适用场景/信号描述），不包含：
- 该策略上次运行时间
- 该策略上次筛选结果数量
- 该策略当前是否有可用结果
- 该策略是否正在后台运行中
- 该策略的历史胜率

**用户心智模型**："我选了趋势突破 → 点了开始 → 等了3分钟 → 看到结果"  
**实际体验**：3步操作，中间等待时间长，无法并行管理多策略。

#### ❌ 问题3：单策略单次执行模式

- 用户一次只能看到**一个策略**的结果
- 切换策略会清空之前的结果（`loadScreenResult()`按`currentStrategy`加载）
- 无法对比"趋势突破vs板块龙头今天各筛出了什么"
- 没有自动定时执行机制（完全手动驱动）

#### ❌ 问题4：信息层级混乱

当前页面的视觉权重分布不合理：

```
⬇️ 首屏可见（最重要）
  [空卡片×3] ← 无信息！浪费！

⬇️ 需要滚动才能看到
  [策略选择器] ← 应该最突出！
  [操作按钮]
  [筛选漏斗]
  [结果列表]

⬇️ 再往下
  [历史记录]
```

---

## 二、优化方案设计

### 2.1 方案A：渐进式优化（推荐首选 ✅）

**核心思路**：保留现有结构，做4个关键改动

#### 改动1：删除顶部3张汇总卡片 → 数据整合进策略卡片

**删除**：
```html
<!-- 删除 index.html L276-293 的整个 summary-card grid -->
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))...">
    <div class="summary-card">🌍 大盘环境...</div>
    <div class="summary-card">🎯 符合条件...</div>
    <div class="summary-card">⏱️ 上次运行...</div>
</div>
```

**迁移**：将3张卡片的信息分别整合：

| 原卡片信息 | 迁移位置 | 展示方式 |
|-----------|---------|---------|
| 大盘环境状态 | 策略卡片上方（全局提示条） | 保持现有`screenStrategyReason`提示条 |
| 符合条件数 | **每个策略卡片底部** | 新增"上次结果：N只"行 |
| 上次运行时间 | **每个策略卡片底部** | 新增"运行于 HH:mm"行 |
| 耗时 | **每个策略卡片底部** | 新增"耗时 Xs"行 |

#### 改动2：策略卡片增强 — 内嵌执行摘要

**新策略卡片结构**：

```
┌─────────────────────────────────────────┐
│ [推荐]                    [ ✓ ]        │  ← 头部（不变）
│ 📈 趋势突破                                  │
│ 中期趋势投资者                               │
├─────────────────────────────────────────┤
│ 📌 买入信号：MA20站稳+MACD多头+...        │  ← 展开（不变）
│ ⏱️ 持股周期：3-10个交易日                   │
│ 🛡️ 止损位：跌破MA20或亏损8%                 │
├─────────────────────────────────────────┤
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━          │  ← 新增！执行摘要区
│ 🔵 上次结果  5只  |  🕐 今天 14:30        │
│ ⏱️ 耗时 42s     |  📊 最高分 87           │  ← 点击展开详情
└─────────────────────────────────────────┘
```

**数据来源**：需要后端新增一个轻量API `/api/screen/summary` 或扩展 `/api/screen/strategies` 返回每策略的最新执行摘要。

#### 改动3：支持多策略结果切换（前端）

当前行为：切换策略 → 清空结果 → 需要重新运行  
**优化后**：切换策略 → 加载该策略缓存结果 → 直接显示（如果有的话）

实现方式：
- 前端新增 `allScreenResults = ref({})` 存储所有策略结果
- 切换策略时从 `allScreenResults[currentStrategy]` 读取
- 不再每次切换都调用 `loadScreenResult()`
- 已运行的策略直接展示，未运行的显示空状态

#### 改动4：结果列表下移 + 增加策略标签

将结果区域改为"跟随当前选中策略"的模式：
- 结果表格前增加大标题：`📈 趋势突破 - 筛选结果（5只）`
- 表格右上角增加"查看完整报告"入口
- 底部保留历史记录，但按策略分组

### 2.2 方案B：激进重构（可选后续迭代）

如果方案A验证效果好，可以进一步演进：

#### 特性1：三策略并行执行 + 定时任务

```
┌─────────────────────────────────────────────┐
│  📡 自动选股：已启用 | 下次执行 09:25       │  ← 全局控制
│  [立即全部运行]  [暂停自动]                 │
├────────┬──────────┬───────────────────────┐
│📈趋势突│🏆板块龙  │🔄超跌反弹             │
│破      │头                              │
│--------│----------│-----------------------│
│✅ 完成 │⏳ 运行中  │❓ 未运行             │
│5只入选 │62%...    │—                     │
│14:30   │预计2min  │—                     │
│[查看▼] │[进度条]  │[▶ 运行]              │
└────────┴──────────┴───────────────────────┘
```

**需要后端支持**：
- 定时任务调度（APScheduler / cron）
- 异步执行队列（Celery 或 asyncio.Task）
- 每策略独立缓存最新结果
- WebSocket/SSE 推送实时进度

**复杂度评估**：高（需后端架构变更），建议作为P1迭代。

---

## 三、方案A详细实施规格

### 3.1 HTML结构调整

#### 删除（L276-293）：3张汇总卡片
#### 修改策略卡片（L300-337）：新增执行摘要区

```html
<!-- 策略选择器 - 增强版 -->
<div v-for="(meta,key) in strategyListSafe" :key="key"
     class="strategy-card"
     :class="{active:currentStrategy===key,recommended:screenStrategyRecommended===key}"
     @click="selectStrategy(key)">
    
    <!-- 推荐标签 + 头部（保持不变） -->
    
    <!-- 展开详情（保持不变） -->
    
    <!-- ★ 新增：执行摘要区（始终显示） -->
    <div class="strategy-exec-summary">
        <!-- 有结果时 -->
        <template v-if="getStrategySummary(key)">
            <div class="exec-row">
                <span class="exec-label">📊 上次结果</span>
                <span class="exec-value text-blue">{{getStrategySummary(key).count}}只</span>
                <span class="exec-label" style="margin-left:auto">🕐</span>
                <span class="exec-value">{{getStrategySummary(key).time}}</span>
            </div>
            <div class="exec-row">
                <span class="exec-label">⏱️ 耗时</span>
                <span class="exec-value">{{getStrategySummary(key).duration}}s</span>
                <span class="exec-label" style="margin-left:auto">🏆</span>
                <span class="exec-value" :class="getStrategySummary(key).topScore>=75?'text-red':'text-yellow'">
                    {{getStrategySummary(key).topScore}}分
                </span>
            </div>
            <!-- 点击查看该策略详情 -->
            <button class="exec-view-btn" @click.stop="viewStrategyResult(key)"
                    v-if="currentStrategy!==key">
                👁 查看此策略结果
            </button>
        </template>
        <!-- 无结果/未运行时 -->
        <template v-else>
            <div class="exec-empty">
                <span class="exec-label" style="color:var(--text-muted)">暂无运行记录</span>
                <button class="exec-run-btn" @click.stop="selectAndRun(key)"
                        v-if="currentStrategy!==key">
                    ▶ 立即运行
                </button>
            </div>
        </template>
        
        <!-- 运行中的策略 -->
        <div v-if="runningStrategies&&runningStrategies.includes(key)" class="exec-running">
            <span class="pulse-dot"></span>
            <span>筛选进行中...</span>
            <div class="progress-bar"><div class="progress-fill"></div></div>
        </div>
    </div>
</div>
```

### 3.2 CSS新增

```css
/* ====== 策略执行摘要区 ====== */
.strategy-exec-summary {
    margin-top: var(--space-md);
    padding-top: var(--space-md);
    border-top: 1px solid var(--border);
}
.exec-row {
    display: flex;
    align-items: center;
    font-size: var(--font-xs);
    padding: 3px 0;
}
.exec-label {
    color: var(--text-secondary);
    font-size: var(--font-xs);
}
.exec-value {
    color: var(--text-primary);
    font-weight: 600;
    font-size: var(--font-xs);
    font-variant-numeric: tabular-nums;
}
.exec-view-btn,
.exec-run-btn {
    width: 100%;
    margin-top: var(--space-sm);
    padding: 6px 0;
    background: transparent;
    border: 1px dashed var(--border);
    border-radius: var(--radius-xs);
    color: var(--blue);
    font-size: var(--font-xs);
    cursor: pointer;
    transition: all .2s;
}
.exec-view-btn:hover,
.exec-run-btn:hover {
    border-color: var(--blue);
    background: var(--blue-bg);
}
.exec-empty {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
}
.exec-running {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
    color: var(--yellow);
    font-size: var(--font-xs);
}
.pulse-dot {
    width: 8px; height: 8px;
    background: var(--yellow);
    border-radius: 50%;
    animation: pulse 1.2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.8); }
}
.progress-bar {
    flex: 1;
    height: 3px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
}
.progress-fill {
    width: 60%; /* 动态 */
    height: 100%;
    background: var(--yellow);
    animation: progressIndeterminate 1.5s infinite;
}
@keyframes progressIndeterminate {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
}
```

### 3.3 JS逻辑调整

#### 新增数据结构

```javascript
// 所有策略的结果缓存（keyed by strategy name）
const allScreenResults = ref({});
// 各策略执行摘要
const strategySummaries = ref({}); // { trend_break: {count:5, time:'14:30', duration:42, topScore:87}, ... }
// 正在运行的策略列表
const runningStrategies = ref([]);
```

#### 新增计算属性

```javascript
// 单策略摘要获取器
const getStrategySummary = computed(() => {
    return (strategyKey) => strategySummaries.value[strategyKey] || null;
});
```

#### 新增方法

```javascript
// 切换到某策略并查看其结果
function viewStrategyResult(strategyKey) {
    selectStrategy(strategyKey);
    // 从缓存加载结果，不发请求
    if (allScreenResults.value[strategyKey]) {
        screenResults.value = allScreenResults.value[strategyKey].results || [];
        screenStats.value = allScreenResults.value[strategyKey].stats || null;
        screenInfo.value = allScreenResults.value[strategyKey].info || screenInfo.value;
    }
}

// 选择并运行
function selectAndRun(strategyKey) {
    selectStrategy(strategyKey);
    startScreen();
}

// 加载所有策略的摘要
async function loadAllStrategySummaries() {
    try {
        const res = await fetchWithAuth(`${API}/api/screen/all-summaries`);
        const data = await res.json();
        if (data.summaries) {
            strategySummaries.value = data.summaries;
        }
    } catch(e) {}
}
```

#### 生命周期钩子

```javascript
// 进入选股Tab时加载所有策略摘要
// 在 switchToScreener() 中追加:
function switchToScreener(){
    activeTab.value='screener';
    loadScreenResult();
    loadAllStrategySummaries(); // ★ 新增
}
```

### 3.4 后端API需求

#### 新增接口：`GET /api/screen/all-summaries`

返回格式：
```json
{
    "summaries": {
        "trend_break": {
            "count": 5,
            "last_run": "2026-04-15 14:30",
            "duration": 42.3,
            "top_score": 87,
            "market_status": "震荡",
            "has_result": true
        },
        "sector_leader": {
            "count": 12,
            "last_run": "2026-04-15 14:28",
            "duration": 38.1,
            "top_score": 92,
            "market_status": "震荡",
            "has_result": true
        },
        "oversold_bounce": null
    },
    "running": ["trend_break"]
}
```

**实现方式**：查询 `screen_history` 表取每组策略最近一条记录即可，无需额外存储。

---

## 四、影响范围评估

| 维度 | 影响程度 | 说明 |
|------|---------|------|
| **HTML模板** | 🟡 中 | 删除3张卡片(~18行)，增强策略卡片(~40行新增) |
| **CSS样式** | 🟢 低 | 新增~80行 `.strategy-exec-*` 相关样式 |
| **JS逻辑** | 🟡 中 | 新增数据结构+3个方法+1个computed，改`switchToScreener` |
| **后端API** | 🟡 中 | 新增1个`/api/screen/all-summaries`接口(~30行) |
| **数据库** | 🟢 无 | 复用现有`screen_history`表 |
| **响应式适配** | 🟢 低 | 策略卡片已是grid自适应，摘要区自然跟随 |
| **兼容风险** | 🟢 低 | 纯增量改动，不影响已有功能 |
| **开发工作量** | ~2h | 前端1h + 后端0.5h + 测试0.5h |

---

## 五、视觉对比

### 改造前 vs 改造后（首屏）

```
【改造前 - 首屏】                          【改造后 - 首屏】

┌──────┬──────┬──────┐                     ┌──────┬──────┬──────┐
│大盘  │符合  │上次  │  ← 空/无用          │📈趋  │🏆板  │🔄超  │
│环境  │条件  │运行  │                     │势突  │块龙  │跌反  │
│  -   │ 0只  │未运行│                     │破    │头    │弹    │
├──────┴──────┴──────┤                     ├──────┼──────┼──────┤
│智能推荐（有时隐藏）│                     │[内嵌摘要区]        │
├──────┬──────┬─────┤                     │5只 14:30 42s 87分│
│趋势突│板块龙│超跌反│                     │[查看▼]            │
│  ✓   │      │     │                     ├──────┼──────┼──────┤
│详情..│      │     │                     │12只 14:28 38s 92分│
├──────┴──────┴─────┤                     │[查看▼]   [▶运行]  │
│ [▶趋势突破] 按钮  │  ← 需要滚到这里     ├──────┴──────┴──────┤
├──────────────────┤                     │ [▶ 执行选中策略]   │
│ 结果/空状态       │  ← 更下面          ├────────────────────┤
│                  │                     │ 结果列表区域        │
│                  │                     │ (带策略标题)        │
└──────────────────┘                     └────────────────────┘

首屏有效信息：0                         首屏有效信息：★★★★★
需要滚动：是                           需要滚动：否（结果也在视野内）
```

---

## 六、待确认决策清单

| # | 决策项 | 推荐 | 说明 |
|---|-------|------|------|
| D1 | 是否删除3张顶部卡片？ | ✅ 是 | 整合进策略卡片，减少冗余 |
| D2 | 策略卡片是否内嵌执行摘要？ | ✅ 是 | 核心改进点 |
| D3 | 是否支持多策略结果缓存切换？ | ✅ 是 | 切换策略不再丢失结果 |
| D4 | 是否新增后端摘要API？ | ✅ 是 | 供策略卡片展示执行状态 |
| D5 | 定时自动执行（方案B）？ | ⏸️ 暂缓 | P1迭代，需后端架构调整 |
| D6 | 进度条/实时推送？ | ⏸️ 暂缓 | 依赖WebSocket/SSE，复杂度高 |

---

## 七、实施优先级

```
P0（本次实施）
├── 删除顶部3张汇总卡片
├── 策略卡片内嵌执行摘要区
├── 多策略结果缓存 + 切换不复失
└── 新增 /api/screen/all-summaries 接口

P1（下次迭代）
├── 三策略定时自动执行
├── 实时进度推送（SSE/WebSocket）
└── 策略间结果对比视图
```

---

*UX架构师审查完毕 · 待用户确认D1-D6后执行实施*
