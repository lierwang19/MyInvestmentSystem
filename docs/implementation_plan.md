# MyInvestmentSystem — 项目回顾与后续开发计划

## 一、项目现状总览

### 整体架构
```
MyInvestmentSystem/
├── 数据管道层（Python 脚本）      → 行情入库 / 因子计算 / 覆盖度核查 / 打分
├── Web 后端（FastAPI）            → 11 个 API 模块 + APScheduler 定时任务
├── Web 前端（Vue 3 + Element Plus）→ 9 个页面视图
├── market.db（SQLite ~2.7GB）      → 日线行情 + 估值因子
└── user_data.db（SQLite）          → 账户 / 持仓 / 交易 / 资产快照
```

### 已完成模块 ✅

| 模块 | 说明 |
|------|------|
| **行情入库** | [get_market_data_db_robust.py](file:///d:/MyInvestmentSystem/get_market_data_db_robust.py) — 多源兜底、增量更新 |
| **覆盖度核查** | [check_coverage.py](file:///d:/MyInvestmentSystem/check_coverage.py) — IPO 对齐、起始/尾部缺口分析 |
| **IPO 回灌** | [backfill_from_ipo.py](file:///d:/MyInvestmentSystem/backfill_from_ipo.py) + `rescue_backfill_failed*.py` |
| **因子 & 估值** | [build_fundamentals_and_valuation.py](file:///d:/MyInvestmentSystem/build_fundamentals_and_valuation.py) — BVPS/EPS_TTM/PB/PE |
| **打分脚本** | [score_universe.py](file:///d:/MyInvestmentSystem/score_universe.py) / [score_candidates.py](file:///d:/MyInvestmentSystem/score_candidates.py) — 行业锚点 + 分桶 |
| **Market Enriched** | [make_market_enriched_from_db.py](file:///d:/MyInvestmentSystem/make_market_enriched_from_db.py) — 生成 [market_enriched.csv](file:///d:/MyInvestmentSystem/market_enriched.csv) |
| **前端 - 数据中心** | Excel 上传 → 解析持仓 |
| **前端 - 投资组合** | 基于上传文件的组合展示 |
| **前端 - 市场概况** | 账户统计 / 大盘 PB / 目标仓位 / 月度走势图 |
| **前端 - 持仓分析** | 大数评分 / 权重 / 买卖建议 / 分红分析（1351 行，最复杂页面） |
| **前端 - 已清仓归档** | 归档记录 + 手动补录 |
| **前端 - 大数优选池** | 基于 [scan_stock_pool](file:///d:/MyInvestmentSystem/web/backend/api/analysis.py#478-558) 的手动扫描 |
| **前端 - 券商管理** | CRUD 券商佣金费率 |
| **前端 - 账户管理** | 多账户 + 关联券商 + 资金转入转出 |
| **前端 - 系统设置** | PB 参数 / 主题 / 备份恢复 |
| **交易系统** | 买入/卖出/分红/归档，自动计算手续费 (812 行) |
| **定时任务** | 每日 17:00 行情更新 + 每日 18:00 月度数据同步 |

---

## 二、发现的问题与缺失

### 🔴 Bug

1. **[main.py](file:///d:/MyInvestmentSystem/web/backend/main.py) 重复注册 Router** — `market.router` 被 include 了两次（第 44、45 行），会导致路由重复
2. **Sidebar 标记 "ANALYSIS (Demo)"** — 给用户造成"未完成"的印象

### 🟠 SOP 中提到但尚未实现的脚本

| SOP 引用名 | 实际状态 |
|------------|----------|
| `process_tdx_industry_v3.py` | 只有 [process_tdx_industry_simple.py](file:///d:/MyInvestmentSystem/process_tdx_industry_simple.py)（功能可能不完整） |
| `etl_financials.py` | **不存在** — 风险标记的 ETL |
| `scoring.py` | **不存在** — SOP 日常 SOP 第 5 步引用 |
| `rank_candidates.py` | **不存在** — SOP 打分替代脚本 |

### 🟡 功能缺失 / 未打通

3. **风险标记（三负 + 一重大）完全缺失** — SOP 第六节描述了 `risk_flags`（负债率、商誉、质押、重大事项），但没有实现脚本，也没有数据库表
4. **打分流程未与 Web 端打通** — [score_universe.py](file:///d:/MyInvestmentSystem/score_universe.py) / [score_candidates.py](file:///d:/MyInvestmentSystem/score_candidates.py) 是离线脚本，优选池扫描 [scan_stock_pool()](file:///d:/MyInvestmentSystem/web/backend/api/analysis.py#478-558) 用的是简化的本地逻辑，与YAML打分体系脱节
5. **净值走势自动化不完整** — [asset_history](file:///d:/MyInvestmentSystem/web/backend/api/assets.py#116-145) 表存在，[snapshot_assets](file:///d:/MyInvestmentSystem/web/backend/api/assets.py#33-115) API 存在，但**没有自动触发**（不在 scheduler 中），需要手动调用
6. **资金流水（capital_flows）前端未展示** — 后端有表和部分逻辑，但前端账户管理页缺乏资金流水明细视图
7. **日常增量 SOP 未完全自动化** — Scheduler 只做了第 1 步（行情更新）和部分月度同步，缺步骤 4（因子推导）和步骤 5（打分）

---

## 三、后续开发计划（按优先级排序）

### P0 — 立即修复的 Bug

- [ ] **修复 [main.py](file:///d:/MyInvestmentSystem/web/backend/main.py) 重复 Router**：删除第 45 行重复的 `market.router`
- [ ] **Sidebar 去掉 "Demo" 标签**：将 "ANALYSIS (Demo)" 改为 "ANALYSIS"

---

### P1 — 打通核心数据流（打分 → Web）

> **目标**：让 Web 端的"大数优选池"真正使用 YAML 配置驱动的打分体系

- [ ] **统一打分入口脚本**：创建 `scoring.py`，封装 [score_universe.py](file:///d:/MyInvestmentSystem/score_universe.py) 的核心逻辑，支持命令行调用，结果写入 [market.db](file:///d:/MyInvestmentSystem/market.db) 或 [user_data.db](file:///d:/MyInvestmentSystem/user_data.db) 的 [stock_pool](file:///d:/MyInvestmentSystem/web/backend/api/analysis.py#559-569) 表
- [ ] **重构 [scan_stock_pool()](file:///d:/MyInvestmentSystem/web/backend/api/analysis.py#478-558) 后端 API**：从读 CSV 简化逻辑改为调用统一打分流程，读取 [scoring.yaml](file:///d:/MyInvestmentSystem/config/scoring.yaml) 配置
- [ ] **Scheduler 增加打分任务**：在每日 17:30（行情更新后）自动运行打分

---

### P2 — 风险标记体系

> **目标**：实现 SOP 第六节中的"三负 + 一重大"风险过滤

- [ ] **创建 `etl_financials.py`**：从 AkShare 抓取资产负债率、商誉/无形资产，生成风险标记
- [ ] **[user_data.db](file:///d:/MyInvestmentSystem/user_data.db) 新增 `risk_flags` 表**：`ts_code, ok_debt, ok_goodwill, ok_pledge, has_major_event`
- [ ] **优选池页面增加风险标记列**：红黄绿灯展示风险状态
- [ ] **持仓分析页面集成风险提示**

---

### P3 — 净值走势自动化

> **目标**：每日自动记录账户净值，形成完整的净值曲线

- [ ] **Scheduler 增加资产快照任务**：每日 17:15（行情更新后）自动调用 [snapshot_assets](file:///d:/MyInvestmentSystem/web/backend/api/assets.py#33-115)
- [ ] **MarketOverview 净资产走势图**：连接 [asset_history](file:///d:/MyInvestmentSystem/web/backend/api/assets.py#116-145) 数据，展示各账户净值曲线

---

### P4 — 资金流水 & 账户增强

- [ ] **账户管理页面增加资金流水明细**：展示 `capital_flows` 表的转入/转出记录
- [ ] **账户总览视图**：汇总展示各账户的总资产、持仓市值、现金、盈亏

---

### P5 — 个股详情页

> **目标**：点击任意股票代码，弹出/跳转到个股详情页

- [ ] **个股 K 线图**：从 `daily_price` 表读取历史行情，用 ECharts 绘制 K 线
- [ ] **个股估值走势**：PB / PE_TTM 历史走势图
- [ ] **交易记录 & 盈亏统计**：该账户对这只股票的所有交易汇总

---

### P6 — 系统健壮性

- [ ] **日志查看页面**：在系统设置中增加 scheduler 日志查看功能
- [ ] **数据状态仪表盘**：展示 [market.db](file:///d:/MyInvestmentSystem/market.db) 覆盖度、最后更新日期、API 配额状态
- [ ] **错误通知机制**：Scheduler 任务失败时有前端提示

---

## 四、建议的执行顺序

```
P0（Bug修复，30分钟）→ P1（打分打通，2-3天）→ P3（净值自动化，1天）
→ P2（风险标记，3-4天）→ P4（资金流水，1天）→ P5（个股详情，2-3天）→ P6（系统健壮性，2天）
```

> [!IMPORTANT]
> 以上计划需要您确认优先级是否符合您的需求。如果您有其他更紧迫的功能需求，可以调整顺序。
