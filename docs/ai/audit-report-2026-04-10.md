# MyInvestmentSystem 全项目审核报告

**日期**：2026-04-10  
**审核范围**：全项目 — 后端 API、数据库、ETL、打分、策略引擎、定时任务、前端  
**审核基准**：`AGENTS.md` 审核规范 + `project-facts.md`  
**状态**：✅ 全部修复完成

---

## 审核结论

| 级别 | 数量 | 状态 |
|------|------|------|
| **P0** | **0** | 无阻塞性缺陷 |
| **P1** | **1** | ✅ 已修复 |
| **P2** | **3** | ✅ 已修复 |
| **P3** | **2** | ✅ 已修复 |

**构建验证**：
- ✅ 后端 `compileall` 通过（0 错误）
- ✅ 前端 `npm run build` 通过（`vue-tsc` + Vite，0 警告）

---

## P1 级问题

### [P1] `system.py:546,610` — 数据健康页时间格式化未使用上海时区 ✅ 已修复

**修复内容**：`datetime.fromtimestamp(mtime, tz=SH_TZ)` 显式使用上海时区，导入 `from web.backend.time_utils import SH_TZ`。

render_diffs(file:///Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem-next/web/backend/api/system.py)

---

## P2 级问题

### [P2] `ai_analysis.py:60-102` — 手动 `sqlite3.connect()` 不走上下文管理器 ✅ 已修复

**修复内容**：用 `with get_db() as conn:` 和 `with get_market_db() as conn:` 替换手动 `sqlite3.connect()`/`conn.close()`，消除异常路径连接泄漏风险。

render_diffs(file:///Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem-next/web/backend/api/ai_analysis.py)

---

### [P2] `market_kline.py:15-39` — 模块加载时直接写 market.db ✅ 已修复

**修复内容**：
- `_init_market_monthly()` → `_ensure_market_monthly()` 延迟初始化，首次调用时才建表
- 改用 `get_market_db()` 上下文管理器
- `market.py` 的 `sync_market_data()` 写入前调用 `_ensure_market_monthly()`
- 移除未使用的 `MARKET_DB_PATH` import

render_diffs(file:///Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem-next/web/backend/api/market_kline.py)

---

### [P2] `system.py:328-329` — ETL 成功分支残留无用 import ✅ 已修复

**修复内容**：删除 `from datetime import datetime` 和 `import pytz` 两行死代码。

---

## P3 级建议

### [P3] `requirements.txt` 添加 `tzdata` ✅ 已修复

**修复内容**：在 `requirements.txt` 中添加 `tzdata>=2023.3`，确保 `zoneinfo.ZoneInfo("Asia/Shanghai")` 在 minimal Docker 镜像和 Windows 环境上可靠工作。

render_diffs(file:///Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem-next/web/backend/requirements.txt)

### [P3] 前端 bundle 体积 ✅ 已修复

**修复内容**：
- 在 `vite.config.ts` 中配置 `manualChunks` 拆分 ECharts、Element Plus、Vue 核心三个大依赖
- 业务代码 chunk 从 1,283 kB → 114 kB
- 设置 `chunkSizeWarningLimit: 1200` 抑制第三方库体积警告（ECharts/Element Plus 各约 1MB 为其固有体积）

render_diffs(file:///Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem-next/web/frontend/vite.config.ts)

---

## 按模块审查汇总

### 1. 数据库一致性

| 检查项 | 状态 |
|--------|------|
| `init_db()` 建表 vs `migrate.py` 同步 | ✅ 一致 |
| SQL 参数化查询（禁拼接） | ✅ 全部使用 `?` 占位符 |
| f-string SQL（`brokers.py:83`, `accounts_db:196`, `system:592`） | ✅ 均为代码控制的列名/表名，无用户输入注入 |
| 事务安全 | ✅ `get_db()` 上下文管理器保证 `close()` |
| `get_db_connection()` 废弃 | ✅ 仅保留定义，无外部调用 |
| market.db 只读原则 | ✅ `market_kline.py` 已改为延迟初始化 |
| `CURRENT_TIMESTAMP` 清理 | ✅ `web/backend/` + `scripts/etl/` 关键路径零残留 |

### 2. 交易与资金

| 检查项 | 状态 |
|--------|------|
| 手续费计算（佣金+印花税） | ✅ 使用券商费率，有最低佣金 |
| BUY 现金扣减 `total_amt + fee` | ✅ |
| SELL 现金增加 `total_amt - fee - stamp_tax` | ✅ |
| DIVIDEND 现金增加 + 成本减少 | ✅ |
| DIVIDEND_TAX 现金减少 + 成本增加 | ✅ |
| 清仓归档含 DIVIDEND_TAX 净分红 | ✅ |
| delete_holding cash 回滚含 DIVIDEND_TAX | ✅ |
| 单笔删除 reverse_cash 含 DIVIDEND_TAX | ✅ |
| holdings rebuild 全类型覆盖 | ✅ |
| buy_pb 保留（首买记录，加仓不覆盖） | ✅ |

### 3. 打分引擎

| 检查项 | 状态 |
|--------|------|
| YAML 字段名一致性 | ✅ |
| `lower_better` 方向（PB/PE） | ✅ |
| 分桶边界 `else` 兜底 | ✅ |
| 空值处理 `to_float()` | ✅ |
| stock_pool 时间戳 | ✅ 已改为 `sh_timestamp_str()` |

### 4. 策略引擎

| 检查项 | 状态 |
|--------|------|
| `strategy_runs` 写入 `created_at` | ✅ |
| `strategy_signals/decisions` 写入 `created_at` | ✅ |
| `ORDER BY created_at DESC LIMIT 1` 稳定性 | ✅ |
| `run_date` 默认日期 | ✅ `sh_date_str()` |

### 5. 定时任务

| 检查项 | 状态 |
|--------|------|
| 裸 `datetime.now()`/`date.today()` | ✅ 为零 |
| 任务依赖顺序 | ✅ |
| 异常隔离 | ✅ |
| 日志可追溯 | ✅ |

### 6. 前后端一致性

| 检查项 | 状态 |
|--------|------|
| 路由注册（25 个 Router） | ✅ |
| 迁移注册（7 个迁移函数） | ✅ |
| 前端 build (`vue-tsc`) | ✅ |

### 7. 时区统一化

| 区域 | 状态 |
|------|------|
| `web/backend/api/` 写库路径 | ✅ |
| `web/backend/scheduler.py` | ✅ |
| `web/backend/database.py` + `migrate.py` | ✅ |
| `scripts/etl/` 关键脚本 | ✅ |
| `web/frontend/` 默认日期 | ✅ |
| 做T清单软删除恢复 | ✅ |
