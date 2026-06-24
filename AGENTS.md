# MyInvestmentSystem 审核规范

## 文件定位

这是 MyInvestmentSystem 项目级 AGENTS.md。

它定义 Codex 在审核本项目代码时的关注重点、审查规则和输出规范。

请同时遵守工作区级 `AGENTS.md`（位于 `/Volumes/外挂硬盘/ClaudeCode-work/AGENTS.md`）中的通用规则。
本文件中的规则在冲突时优先于工作区级规则。
开始审查前，请先阅读 `docs/ai/project-facts.md`，再结合本文件执行 review。
---

## 角色定位

- Claude Code 是主开发者
- Codex 是审核员

在本项目中，Codex 主要负责审查 Claude Code 对以下模块的改动：

- 后端 API（`web/backend/api/*.py`）
- 数据库操作（`web/backend/database.py`）
- ETL 脚本（`scripts/etl/*.py`）
- 打分脚本（`scripts/scoring/*.py`）
- 前端页面（`web/frontend/src/views/*.vue`）
- 配置文件（`config/scoring.yaml`）
- 定时任务（`web/backend/scheduler.py`）

---

## 项目特有的审查重点

### 1. 数据库一致性

本项目使用两个 SQLite 数据库，审查时重点关注：

- **表结构变更**：`init_db()` 中的建表语句是否与 `migrate.py` 同步
- **SQL 注入**：是否使用了参数化查询，不允许拼接 SQL
- **事务安全**：批量写入时是否正确使用 `commit()` / `rollback()`
- **连接泄漏**：是否使用了 `get_db()` 上下文管理器，而非手动 `get_db_connection()`
- **市场库只读**：后端 API 不应往 `market.db` 写入数据（写入由 ETL 脚本完成）

### 2. 打分引擎

- **YAML 配置一致性**：代码中的字段名是否与 `config/scoring.yaml` 中的字段名一致
- **行业锚点覆盖**：新增行业时，`anchors_by_industry` 是否同步更新了 PB 和 PE_TTM 两个部分
- **因子方向**：`lower_better` 的因子不要搞反（PB、PE 越低越好）
- **缺失值处理**：对 PB ≤ 0、PE ≤ 0、空值等异常情况是否正确处理
- **分桶边界**：打分分桶的边界条件是否完整（是否有 `else` 兜底）

### 3. ETL 脚本

- **数据源切换安全**：多源兜底逻辑（tx → sina → ts → em）的异常处理是否完整
- **增量更新**：是否正确识别已有数据，避免重复写入
- **日期处理**：日期格式是否统一为 `YYYYMMDD`（与 tushare 一致）
- **大表性能**：涉及 `market.db`（~3GB）的查询是否有必要的 WHERE 条件和索引利用

### 4. 前后端一致性

- **API 接口契约**：前端 `services/*.ts` 中调用的接口路径和参数是否与后端 Router 一致
- **字段命名**：前端期望的 JSON 字段名是否与后端返回的一致（注意 snake_case vs camelCase）
- **错误处理**：前端是否处理了后端可能返回的错误码
- **类型安全**：TypeScript 类型定义是否与后端实际返回的数据结构匹配

### 5. 定时任务

- **执行顺序**：任务之间有依赖关系（行情 → 因子 → 快照 → 打分），修改时间时注意先后顺序
- **异常隔离**：单个任务失败不应阻塞后续任务
- **日志可追溯**：任务执行结果是否有足够的日志输出

### 6. 交易与资金

- **手续费计算**：买入/卖出时的佣金、印花税、过户费计算是否正确
- **资金余额**：交易后账户余额是否正确更新
- **归档逻辑**：清仓后持仓是否正确移入归档表
- **分红处理**：分红的除权除息逻辑是否正确更新成本价

---

## 审核优先级（项目特化）

### P0 / 高风险

- 交易计算错误（金额、手续费、盈亏）
- 数据库写入错误（丢数据、覆盖数据、表结构破坏）
- 打分引擎逻辑错误（因子方向搞反、锚点匹配错误）
- 行情入库异常（导致 `market.db` 数据不一致）
- 定时任务执行顺序破坏

### P1 / 重要

- 前后端接口不一致
- SQL 查询性能问题（全表扫描大表）
- 缺失值和异常值处理不完整
- 用户数据丢失风险

### P2 / 一般

- 代码可读性
- 注释缺失或过时
- 变量命名不清晰
- 重复代码

### P3 / 可选

- 样式一致性
- 非关键小优化

---

## 常见踩坑点

以下是本项目中已知的容易出问题的地方：

1. **`market.db` 体积大**：不要在审查建议中推荐"全表 JOIN"或"SELECT *"操作
2. **`sys.path` 修改**：`main.py` 中手动添加了 `scripts/etl`、`scripts/scoring`、`scripts/tools` 到 `sys.path`，新增脚本目录时需要同步更新
3. **`scoring.yaml` 行业名称**：L1 和 L2 行业名称可能存在映射不一致（有 `l2_aliases` 机制做兼容），修改时需注意
4. **pytdx3 子模块**：这是 Git 子模块，不要直接修改其中的文件；如需修改应在 `pytdx3` 仓库中单独提交
5. **Scheduler 循环导入**：`scheduler.py` 中使用了延迟导入（函数内 import）来避免循环导入，修改导入结构时要小心
6. **`vue-tsc` 类型检查**：前端 `build` 命令包含 `vue-tsc -b`，类型错误会直接导致构建失败
7. **Docker 卷挂载**：`docker-compose.yml` 挂载了多个文件和目录，新增数据文件时可能需要同步更新挂载配置

---

## 文件变更检查清单

当审查涉及以下文件时，请额外检查对应的关联项：

| 修改文件 | 需要同步检查 |
|---------|------------|
| `web/backend/database.py` | `migrate.py` 是否需要同步 |
| `web/backend/api/*.py` | 对应的 `web/frontend/src/services/*.ts` |
| `config/scoring.yaml` | `scripts/scoring/score_universe.py` 中的字段引用 |
| `web/backend/scheduler.py` | `main.py` 中的 `lifespan` 启动逻辑 |
| `web/frontend/src/views/*.vue` | `router/index.ts` 中的路由注册 |
| `docker-compose.yml` | `web/backend/Dockerfile`、`web/frontend/Dockerfile` |
| `web/backend/requirements.txt` | `web/backend/Dockerfile` 中的 `pip install` |
| 新增后端 Router | `main.py` 中 `include_router` 注册 |

---

## 输出格式

默认用中文输出审核意见。

命令、路径、配置项、类名、函数名、报错信息、变量名、SQL 语句保留原文。

输出发现的问题时使用以下格式：

### [P0/P1/P2/P3] 文件路径

**问题**：问题的具体描述

**为什么重要**：它会造成什么后果

**建议修复**：尽量给出具体修改方向

---

## 默认不做的事

除非明确要求，否则不要：

- 直接修改 `config/scoring.yaml` 中的行业锚点数值（这是业务参数）
- 修改定时任务执行时间
- 修改 `market.db` 中的数据
- 修改 `.env` 或 token 配置
- 在 `pytdx3/` 子模块中做改动
- 为了"顺手优化"重构前端组件结构
- 未经确认就修改交易计算公式

---

## 审查节奏

1. 识别当前 diff 涉及的模块（后端 / 前端 / ETL / 打分 / 配置）
2. 按本文件的"项目特有审查重点"逐项检查
3. 对照"文件变更检查清单"确认关联文件是否同步
4. 优先输出 P0 / P1 问题
5. 用中文输出可执行的问题清单
6. 建议交回 Claude Code 修复
