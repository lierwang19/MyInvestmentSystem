# MyInvestmentSystem 项目事实清单

## 项目定位

MyInvestmentSystem 是一套面向 A 股场景的量化投资管理系统，核心能力包括：

- 数据采集与清洗（ETL）
- 因子打分与选股
- Web 管理界面
- 定时任务调度
- 持仓、交易、资金与资产分析

本文件只记录“相对稳定的项目事实”。
这里不写容易频繁变化的统计数字，不写一次性任务，也不写个人偏好。

---

## 总体架构

项目整体分为四层：

1. 数据层
   - 行情、估值、财务等市场数据
   - 用户账户、持仓、交易、快照、设置等业务数据

2. 计算层
   - ETL 脚本
   - 因子加工
   - 打分选股
   - 快照与统计计算

3. 服务层
   - FastAPI 后端
   - API 路由
   - 定时任务调度
   - 数据库访问与系统配置

4. 表现层
   - Vue 3 前端
   - 页面视图
   - 服务调用层
   - 状态管理与图表展示

---

## 目录边界

### config/
放配置文件。

重点文件：
- `config/scoring.yaml`：打分引擎主配置

这里的配置会直接影响选股结果，属于高敏感业务参数区域。

### scripts/
放数据处理和计算脚本。

主要分为：
- `scripts/etl/`：ETL、行情与基础数据加工
- `scripts/scoring/`：打分、候选池、组合构建
- `scripts/tools/`：调试、检查、辅助工具
- `scripts/tests/`：脚本层测试

### web/backend/
FastAPI 后端。

重点文件：
- `web/backend/main.py`：应用入口、路由注册、生命周期逻辑
- `web/backend/database.py`：SQLite 连接、建表、数据库初始化
- `web/backend/scheduler.py`：定时任务注册与调度
- `web/backend/config.py`：路径、数据库位置等常量
- `web/backend/response.py`：统一响应格式与异常处理
- `web/backend/api/`：按业务拆分的 API 路由模块

### web/frontend/
Vue 3 前端。

重点目录：
- `web/frontend/src/views/`：页面视图
- `web/frontend/src/components/`：复用组件
- `web/frontend/src/services/`：前端 API 调用层
- `web/frontend/src/stores/`：Pinia 状态管理
- `web/frontend/src/composables/`：组合式逻辑
- `web/frontend/src/router/`：前端路由
- `web/frontend/src/i18n/`：国际化

### pytdx3/
Git 子模块。

默认不要在主仓库里直接修改它。
如需修改，应在子模块仓库中单独处理。

### docs/
项目文档目录。

建议把 AI 相关文档统一收敛到：
- `docs/ai/project-facts.md`
- `docs/ai/dev-rules.md`
- `docs/ai/review-rules.md`

### data/ 与 logs/
运行时数据与日志目录。
通常不应纳入代码逻辑改动的核心范围。

---

## 数据库边界

项目使用两个 SQLite 数据库，职责必须分清。

### market.db
市场数据数据库。

用途：
- 日线行情
- 估值因子
- 市场基础数据
- 供分析、打分、行情查询只读使用

原则：
- 正常业务 API 不应直接向 `market.db` 写数据
- 对 `market.db` 的写入主要由 ETL 脚本负责
- 涉及大表查询时，必须注意筛选条件、索引利用和性能风险

### user_data.db
用户业务数据库。

用途：
- 账户
- 券商
- 持仓
- 交易记录
- 已清仓归档
- 资产快照
- 优选池
- 资金流水
- 系统设置

原则：
- 涉及表结构改动时，要同时检查建表与迁移逻辑
- 涉及交易、持仓、归档、分红时，要重点防止金额和状态错误

---

## 关键业务链路

### 1. ETL 链路
目标：
把外部数据源的数据写入市场数据库，并加工成后续可用的数据形态。

典型流程：
原始数据拉取 → 清洗标准化 → 入库/落盘 → 因子加工 → 提供给打分与展示层使用

高风险点：
- 数据源切换时的异常处理
- 增量更新重复写入
- 日期格式不一致
- 大表查询性能
- 缺失值和非法值处理

### 2. 打分链路
目标：
基于 `config/scoring.yaml` 生成候选打分结果。

核心依赖：
- 市场与估值数据
- 行业映射
- 行业锚点
- 正向因子与负向剔除规则

高风险点：
- 字段名与 YAML 配置不一致
- `lower_better` 方向写反
- 行业锚点覆盖不全
- 分桶边界遗漏
- 空值、异常值处理不完整

### 3. 交易与持仓链路
目标：
记录买卖、分红、资金变化，并正确更新持仓与归档状态。

高风险点：
- 手续费、印花税、过户费计算错误
- 账户余额更新错误
- 清仓后未正确归档
- 分红后成本价、持仓状态更新错误

### 4. 前后端联动链路
目标：
前端页面通过 `services` 调用后端 API，完成展示和交互。

高风险点：
- API 路径不一致
- 请求参数不一致
- 返回字段命名不一致
- TypeScript 类型与后端返回结构不一致
- 错误处理缺失

### 5. 定时任务链路
目标：
自动完成行情更新、因子加工、资产快照、打分刷新等任务。

原则：
任务之间存在依赖关系，修改执行逻辑时必须关注先后顺序。

高风险点：
- 上游未完成就触发下游
- 单任务失败导致后续任务受阻
- 日志不足，无法追溯问题
- 调度配置改动后未同步检查启动逻辑

---

## 稳定约定

以下约定默认视为当前项目的稳定事实：

- 后端主入口是 `web/backend/main.py`
- 数据库初始化逻辑在 `web/backend/database.py`
- 定时任务逻辑在 `web/backend/scheduler.py`
- 打分主配置在 `config/scoring.yaml`
- 前端主入口在 `web/frontend/src/main.ts`
- 前端路由配置在 `web/frontend/src/router/`
- 前后端联动时，后端改动通常需要同步检查前端 `services/`
- 数据库表结构改动通常需要同步检查迁移逻辑
- 新增后端 Router 时，通常需要同步检查路由注册
- 新增前端页面时，通常需要同步检查路由与服务层

---

## 常见关联关系

修改下面文件时，通常要联动检查：

- `web/backend/database.py`
  - 建表逻辑
  - 迁移逻辑
  - 调用方 SQL

- `web/backend/api/*.py`
  - `web/frontend/src/services/*.ts`
  - 前端页面字段使用
  - 后端响应结构

- `config/scoring.yaml`
  - `scripts/scoring/`
  - 行业映射
  - 字段名引用
  - 分桶逻辑

- `web/backend/scheduler.py`
  - `web/backend/main.py`
  - 各被调度脚本或服务
  - 系统设置中的任务控制逻辑

- `web/frontend/src/views/*.vue`
  - `web/frontend/src/router/`
  - `web/frontend/src/services/`
  - 类型定义与状态管理

- `docker-compose.yml`
  - 前后端 Dockerfile
  - 挂载路径
  - 环境变量与运行端口

---

## 不应轻易改动的区域

以下区域改动前应提高警惕：

- `config/scoring.yaml` 中的业务参数和行业锚点
- 交易计算公式
- 分红、归档、成本价更新逻辑
- 定时任务执行顺序
- `market.db` 的写入方式
- `.env`、token、证书、密钥类文件
- `pytdx3/` 子模块内容

---

## 建议的 AI 使用方式

### 给 Claude Code
更适合让 Claude Code 使用本文件来快速建立项目地图：
- 目录边界
- 数据边界
- 业务链路
- 关键关联关系

### 给 Codex
更适合让 Codex 基于本文件做 review 时重点检查：
- 高风险链路
- 文件联动关系
- 不应轻易改动的区域
- 容易遗漏的同步更新点

---

## 维护原则

更新本文件时，优先遵循：

- 记录长期稳定事实
- 少写容易过期的数字
- 少写一次性任务
- 少写个人操作习惯
- 多写模块边界、依赖关系、风险点、联动关系

如果某条信息经常变化，就不要放在这里；
应放到 README、SOP、任务说明或具体 issue 中。