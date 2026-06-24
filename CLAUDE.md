# MyInvestmentSystem 项目规范
请先阅读：@docs/ai/project-facts.md

## 开发角色
- 你是主开发者
- 先分析，再计划，再修改
- 默认优先最小改动
- 完成后给出：
  - 修改文件清单
  - 修改摘要
  - 风险点
  - 已完成验证
  - 建议我下一步在 Codex 中执行 `/diff` 和 `/review`
## 项目概述

本项目是一套**量化投资管理系统**，主要面向 A 股市场，包含以下核心能力：

- **数据管道层**（ETL）：从 tushare / akshare / 通达信 获取行情和财务数据，写入 `market.db`
- **打分选股引擎**：基于 YAML 配置的行业锚点 + PB/PE/距离低点 加权打分
- **Web 管理界面**：持仓管理、交易记录、资产分析、优选池、行情概览
- **定时任务**：每日自动更新行情、计算因子、拍快照、刷新打分

---

## 目录结构

```
myinvestmentsystem/
├── config/                    # 配置文件（scoring.yaml 等）
│   └── scoring.yaml           # 打分引擎主配置：正向因子权重、行业锚点、负向剔除规则
├── scripts/                   # 数据管道 & 打分脚本
│   ├── etl/                   # ETL 脚本（行情入库、因子计算、行业同步）
│   ├── scoring/               # 打分脚本（全市场扫描、候选打分、组合构建）
│   ├── tools/                 # 调试/检查/辅助工具脚本
│   └── tests/                 # 脚本层测试
├── web/
│   ├── backend/               # FastAPI 后端
│   │   ├── api/               # 16 个 API 路由模块
│   │   ├── main.py            # 应用入口 + 中间件 + Router 注册
│   │   ├── database.py        # SQLite 连接 + init_db 建表
│   │   ├── scheduler.py       # APScheduler 定时任务（5 个 Job）
│   │   ├── config.py          # 路径常量（PROJECT_ROOT、各 DB 路径）
│   │   ├── response.py        # 统一响应格式 + 全局异常处理
│   │   └── requirements.txt   # 后端 Python 依赖
│   └── frontend/              # Vue 3 前端
│       ├── src/
│       │   ├── views/         # 12 个页面视图（.vue）
│       │   ├── components/    # 可复用组件
│       │   ├── services/      # API 调用服务层（TypeScript）
│       │   ├── stores/        # Pinia 状态管理
│       │   ├── composables/   # Vue Composables
│       │   ├── i18n/          # 国际化
│       │   └── router/        # Vue Router
│       └── package.json       # 前端依赖
├── pytdx3/                    # Git 子模块：通达信数据接口
├── docs/                      # 项目文档（SOP、实施计划、投资理念）
├── data/                      # 运行时数据目录（.gitignore）
├── logs/                      # 日志目录（.gitignore）
├── market.db                  # 行情数据库 ~3GB（.gitignore）
├── user_data.db               # 用户数据库基线种子文件（仓库跟踪）
├── docker-compose.yml         # Docker 部署编排
├── .env.example               # 环境变量模板（TUSHARE_TOKEN）
└── .gitignore
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3 + FastAPI + Uvicorn |
| **数据库** | SQLite（`market.db` 行情 / `user_data.db` 用户数据） |
| **前端** | Vue 3 + TypeScript + Vite + Element Plus + ECharts |
| **状态管理** | Pinia |
| **定时任务** | APScheduler |
| **数据源** | tushare（需 Token）、akshare、pytdx3（通达信） |
| **部署** | Docker Compose（后端 :8001、前端 :8080） |

---

## 关键数据库

### market.db（行情库，~3GB）

- `daily_price`：日线行情（open, high, low, close, vol, amount）
- `fundamentals`：估值因子（pb, pe_ttm, total_mv 等）
- 由 ETL 脚本维护，只读使用

### user_data.db（用户库）

- `accounts`：证券账户
- `brokers`：券商费率
- `holdings`：当前持仓
- `transactions`：交易记录
- `transactions_archive`：已清仓归档
- `asset_history`：净值快照
- `stock_pool`：优选池
- `capital_flows`：资金流水
- `system_settings`：系统配置

---

## 定时任务编排

| 任务 | 默认时间 | 说明 |
|------|---------|------|
| `market_update` | 17:00 | 拉取日线行情写入 `market.db` |
| `factor_build` | 17:15 | 重算 PB/PE 因子，生成 `market_enriched.csv` |
| `asset_snapshot` | 17:20 | 记录各账户净值 |
| `scoring` | 17:30 | YAML 驱动的全市场打分 |
| `monthly_sync` | 18:00 | 大盘月度数据同步 |

时间和启用状态可通过系统设置页面调整，配置持久化在 `system_settings` 表中。

---

## 前端页面

| 路由 | 页面 | 说明 |
|------|------|------|
| `/industry` | 行业分类 | 行业分类管理（默认首页） |
| `/import-flow` | 导入流程 | 数据导入向导 |
| `/market-overview` | 市场概况 | 大盘统计 / PB / 月度走势 |
| `/portfolio` | 投资组合 | 基于上传数据的持仓展示 |
| `/holdings` | 持仓分析 | 打分 / 权重 / 买卖建议（最复杂页面） |
| `/brokers` | 券商管理 | CRUD 券商佣金费率 |
| `/accounts-manage` | 账户管理 | 多账户 + 资金转入转出 |
| `/archived` | 已清仓归档 | 归档记录 + 手动补录 |
| `/stock-pool` | 大数优选池 | YAML 驱动的选股结果 |
| `/capital-flows` | 资金流水 | 资金明细 |
| `/system-settings` | 系统设置 | 参数 / 主题 / 定时任务 / 备份恢复 |

---

## 后端 API 模块（`web/backend/api/`）

16 个模块，全部挂载在 `/api` 前缀下：

| 模块 | 功能 |
|------|------|
| `upload.py` | 文件上传（持仓 Excel 解析） |
| `portfolio.py` | 投资组合查询 |
| `analysis.py` | 持仓分析 + 打分扫描 |
| `system.py` | 系统管理（备份/恢复/行业同步/定时任务控制） |
| `brokers.py` | 券商管理 |
| `accounts_db.py` | 账户管理 |
| `transactions.py` | 交易操作（买入/卖出/分红） |
| `transactions_archive.py` | 归档管理 |
| `market.py` | 市场数据查询 + 月度同步 |
| `market_kline.py` | K 线数据 |
| `settings.py` | 系统设置读写 |
| `assets.py` | 资产快照 + 净值查询 |
| `quotes.py` | 实时行情轮询 |
| `stock_pool.py` | 优选池管理 |
| `risk.py` | 风险指标 |
| `import_flow.py` | 导入流程 API |

---

## 打分引擎（`config/scoring.yaml`）

打分体系由 YAML 配置驱动，核心要素：

- **正向因子**（`positives`）：PB（40%）、PE_TTM（40%）、距离一年低点（20%）
- **行业锚点**（`anchors_by_industry`）：每个行业有独立的 PB/PE 基准值
- **分桶评分**（`tiers`）：基于与锚点的偏差进行分段打分
- **负向剔除**（`negatives`）：负债率、商誉、质押、重大事件
- **排除规则**（`exclusions`）：ST、最低价格、最低市值

---

## 开发约定

### 开发前置步骤

1. 确认当前所在目录是否为本项目根目录
2. 查看 `git status`，确认当前分支和改动
3. 快速熟悉涉及的模块和文件
4. 先给出修改计划，确认后再执行

### 修改原则

- **优先最小改动**，不要随意扩大范围
- **不擅自改技术栈**，遵循现有 Vue 3 + Element Plus + FastAPI + SQLite 结构
- **不随意安装新依赖**，需要时先说明理由
- **不直接提交 commit**，除非明确要求
- **不动敏感文件**（`.env`、token 配置），除非明确要求
- 修改后端 API 时注意前端相应的 `services/*.ts` 是否需要同步更新
- 修改数据库表结构时，需同步更新 `database.py` 中的 `init_db()` 和 `migrate.py`

### 常用路径

| 用途 | 路径 |
|------|------|
| 项目根 | `/Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem/` |
| 后端入口 | `web/backend/main.py` |
| 配置常量 | `web/backend/config.py` |
| 建表逻辑 | `web/backend/database.py` |
| 定时任务 | `web/backend/scheduler.py` |
| 打分配置 | `config/scoring.yaml` |
| 前端入口 | `web/frontend/src/main.ts` |
| 路由配置 | `web/frontend/src/router/index.ts` |
| 全局样式 | `web/frontend/src/style.css` |

---

## 运行方式

### 本地开发

```bash
# 后端（项目根目录）
cd /Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem
python -m uvicorn web.backend.main:app --reload --port 8000

# 前端
cd web/frontend
npm install
npm run dev
```

### Docker 部署

```bash
# 需要先配置 .env（从 .env.example 复制）
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN

docker-compose up -d --build
# 后端: http://localhost:8001
# 前端: http://localhost:8080
```

### 运行 ETL 脚本

```bash
# 示例：行情入库
python scripts/etl/get_market_data_db_robust.py --end-target today --db market.db

# 示例：生成 market_enriched.csv
python scripts/etl/make_market_enriched_from_db.py --base stock_industry_base.csv --db market.db --out market_enriched.csv

# 示例：全市场打分
python scripts/scoring/score_universe.py
```

### 运行测试

```bash
# 后端脚本测试
python -m pytest scripts/tests/

# 前端测试
cd web/frontend && npm run test
```

---

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `TUSHARE_TOKEN` | Tushare 数据接口 Token | 是（行情入库） |
| `ts_token` | 同上（兼容旧写法） | 否 |
| `CORS_ORIGINS` | CORS 允许的来源 | 否（默认 localhost） |

---

## 输出要求

- 默认用**中文**解释
- 命令、路径、配置项、代码标识符保留原文
- 改动说明包含：改了什么、改了哪些文件、为什么、风险点、如何验证
- 回答直白，适合偏实操的用户
- 完成修改后提醒交给 Codex 做 review

---

## Git 子模块

- `pytdx3/`：通达信数据接口库（`https://github.com/lierwang19/pytdx3.git`）
- 克隆项目后需要执行 `git submodule update --init` 初始化

---

## 注意事项

- `market.db` 约 3GB，不纳入 Git 管理
- `user_data.db` 在仓库中保留一份基线种子库，用于 fresh clone / Docker Compose 启动；本地真实业务数据不要直接提交回仓库
- `stock_industry_base.csv`、`market_enriched.csv` 等运行时生成的 CSV 不纳入 Git 管理
- 前端使用 `vue-tsc` 做类型检查，修改 `.vue` 文件时注意类型标注
- 后端使用 `python -m` 方式运行以确保 `web.backend` 包路径正确

## Deploy Status

- 当前仓库未检测到 `.github/workflows/`、Vercel / Netlify / Fly / Render / Railway 配置文件，也没有已记录的 production URL
- 当前可确认的运行方式是本地 `docker-compose up -d --build backend frontend`
- 当前可确认的本地健康检查：
  - 后端：`curl -sS -i http://127.0.0.1:8001/`
  - 前端：`curl -sS -i http://127.0.0.1:8080/`
- 在补充真实部署平台、生产地址和健康检查接口之前，`gstack /land-and-deploy` 只能完成 merge，不能完成自动 deploy canary
- 如果后续接入真实部署，请先用 `gstack /setup-deploy` 把平台、URL、health check 和 deploy status 命令写回本文件

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
