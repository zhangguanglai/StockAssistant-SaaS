# CLAUDE.md — A股投资助手 项目规范

> 本文件供 AI 助手（Claude/CodeBuddy）阅读，确保后续开发遵循统一规范。

## 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| **前端框架** | Vue.js | 2.7 (CDN) | Composition API (`setup()` + `ref`/`computed`/`watch`) |
| **UI 渲染** | 原生 HTML 模板 | — | `templates/index.html`，非 SPA 路由 |
| **图表** | ECharts | 5.x (CDN) | K线/饼图/柱状图/仪表盘 |
| **后端框架** | Flask | Python 3 | Blueprint 模块化架构 |
| **数据源** | Tushare + 新浪财经 | — | 二级降级链：新浪→Tushare |
| **数据库** | SQLite | — | `database.py` CRUD |
| **认证** | JWT (HS256) | — | access_token 72h + refresh_token 7天 |

## 项目结构

```
stock-portfolio/
├── server.py              # Flask 入口（~68行）
├── helpers.py             # 公共工具函数（行情/缓存/价格锚点等）
├── database.py            # SQLite 操作层
├── auth.py                # JWT 认证
├── screener.py            # 选股引擎（三套策略 ~3400行）
├── routes/
│   ├── position_bp.py     # 持仓/交易/复盘/回测
│   ├── screen_bp.py       # 选股引擎 API
│   ├── watch_bp.py        # 观察池/策略效果
│   ├── data_bp.py         # 数据源扩展 (19个API)
│   └── auth_bp.py         # 认证路由
├── templates/index.html   # Vue 单页应用模板
├── static/js/app.js       # Vue3 应用逻辑
└── static/css/style.css   # 全局样式
```

## 代码规范

### JavaScript (app.js)

1. **Vue 2.7 Composition API 风格**
   - 使用 `ref()`, `computed()`, `watch()`, `onMounted()` 等组合式 API
   - 所有响应式变量必须在 `setup()` 中定义，并在 `return` 对象中导出
   - **禁止遗漏导出**：新增变量必须同时加入 `return { ... }`

2. **命名约定**
   - 变量：`camelCase`（如 `threeViewsCheck`, `watchStats`）
   - CSS 类：`kebab-case`（如 `threeview-status-dot`, `sell-check-section`）
   - API 路由：`kebab-case`（如 `/api/check-three-views`）
   - 常量：`UPPER_SNAKE_CASE` 或描述性对象字面量

3. **字符串拼接 vs 模板字符串**
   - ⚠️ **优先使用字符串拼接**（`+`），避免模板字符串（`` `${}` ``）
   - 原因：本项目在部分浏览器环境下模板字符串存在兼容性异常
   - 允许使用模板字符串的场景：仅限 HTML 模板中的 Vue 插值（`{{ }}`）

4. **错误处理模式**
   ```javascript
   // 正确：完整的三段式错误处理
   threeViewsError.value = '';  // 先清空
   try {
       const r = await fetchWithAuth(url);
       if (r.ok) { data.value = await r.json(); }
       else { err.value = '数据获取失败'; }  // 设置错误消息
   } catch (e) {
       err.value = '网络异常';
   } finally { loading.value = false; }
   ```

5. **表单重置必须彻底**
   - `resetForm()` 必须清除所有关联状态（包括弹窗内嵌的检查结果、错误提示）

### Python (后端)

1. **Flask Blueprint 注册**：新模块必须在 `server.py` 中注册
2. **Tushare API 调用**：必须通过 `helpers.py` 的封装函数调用，不直接 import pro
3. **缓存策略**：交易时段 30s TTL，非交易时段 5min TTL
4. **Windows 编码**：控制台输出禁止 emoji（用 ASCII 替代），避免 `UnicodeEncodeError`

### HTML/CSS (index.html / style.css)

1. **A 股市场惯例**：
   - 🔴 **红色 = 涨**（正收益/盈利/上涨）
   - 🟢 **绿色 = 跌**（负收益/亏损/下跌）
   - 与欧美市场相反！

2. **CSS 变量体系**：使用 `var(--primary)`, `var(--bg-card)`, `var(--border)` 等语义变量

3. **版本号管理**：修改 JS/CSS 后必须 bump `app.js?v=N` 中的版本号

## 禁止事项 ❌

### 致命级（会导致页面崩溃）

| # | 禁止行为 | 后果 | 正确做法 |
|---|---------|------|---------|
| 1 | HTML 模板引用了未定义/未导出的 JS 变量 | Vue 运行时崩溃，整个功能区空白 | 新增变量 → 定义 + return 导出 + HTML 引用 三步闭环 |
| 2 | `<template>` 标签上使用 `:key` 属性 | Vue 编译错误 | 将 `:key` 移到实际渲染子元素上 |
| 3 | 在 app.js 中使用模板字符串 `` `${}` `` | 浏览器 SyntaxError（本项目已知兼容问题） | 用字符串拼接 `'...' + var + '...'` |
| 4 | `replace_in_file` 时匹配到重复代码片段 | 改错了地方 | 用 rsplit / 更多上下文精确定位最后一个 |

### 严重级（功能异常）

| # | 禁止行为 | 后果 | 正确做法 |
|---|---------|------|---------|
| 5 | 新增功能只写了后端/前端之一 | 功能不闭环，用户反馈缺失 | 前后端必须同步验证 |
| 6 | 审计数据中泄露 Python 内部变量 | 弹窗显示 `_td.get()`, `f-string` 代码 | 预提取可读文本，翻译为中文 |
| 7 | Tushare concept_detail 返回值直接用于板块评分 | 无区分度标签（融资融券/深股通）导致评分≈0 | 黑名单过滤噪声标签，用 ths_member 预加载替代 |
| 8 | 在评分循环内调用无缓存的 API 函数 | 1456 次重复请求，性能灾难 | 进程内缓存或预加载 |
| 9 | Windows 控制台输出 emoji | UnicodeEncodeError 导致服务崩溃 | 用 ASCII 替代（⭐→[星], ✅→[OK]） |

### 规范级（可维护性）

| # | 禁止行为 | 建议 |
|---|---------|------|
| 10 | 推送 GitHub 前不询问用户 | **必须先确认** |
| 11 | 修改 .workbuddy 目录下的文件 | 该目录是系统目录，不要动 |
| 12 | 删除个人目录（Desktop/Downloads/Documents）下的任何内容 | 高危操作，绝对禁止 |

## 常见修复速查

### 自选Tab胜率为0
- 根因：`win_rate = profit/(profit+loss)` 分母不含平盘项
- 修复：加入 `flat` 统计，分母改为 `(profit+loss+flat)||1`

### 买入弹窗价格为0
- 根因：`quickBuyFromWatch` 未校验价格有效性，未设置默认数量
- 修复：Number转换+>0校验，默认 buy_volume=100

### 三看预览区域空白
- 根因：HTML 引用了 `threeViewsError` 但 JS 未定义/未导出
- 修复：定义 ref + return 导出 + loadThreeViews 失败时设值 三步到位

### screener.py 精确定位修改
- 多策略有相同的 `passed.append({...})` 模式
- `replace_in_file` 会匹配到第一个
- 解决：用 rsplit 定位最后一个，或写脚本精确替换

## 工作流

1. **先读记忆**：每次开工前读 `MEMORY.md` 和当日日志
2. **改完验证**：linter 检查 + 语法检查 + 版本号 bump
3. **写记忆**：完成后追加当日日志，重要发现更新 MEMORY.md
4. **推送前问**：GitHub 操作必须用户确认
