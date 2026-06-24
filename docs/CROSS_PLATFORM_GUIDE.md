# 跨平台开发指南：Windows ↔ Mac 切换

> 本文档记录如何在 Windows 和 Mac 之间无缝切换开发环境。

## 前提条件

本项目已具备跨平台基础：
- Git 远程仓库：`https://github.com/lierwang19/MyInvestmentSystem.git`
- Docker 化部署（`docker-compose.yml` + 前后端 Dockerfile）
- `.gitignore` 已排除平台特定文件

---

## 一、首次在新电脑上设置

### 1. 安装开发工具（Mac）

```bash
brew install git python@3.11 node
```

Docker Desktop 从 [docker.com](https://www.docker.com/products/docker-desktop/) 下载安装。

### 2. 克隆项目

```bash
git clone https://github.com/lierwang19/MyInvestmentSystem.git
cd MyInvestmentSystem
```

### 3. 创建 `.env` 文件

`.env` 被 gitignore 了，需手动创建：

```bash
echo 'TUSHARE_TOKEN=你的token' > .env
```

### 4. 迁移大数据文件

以下文件不在 Git 中，需通过 U盘/网盘/scp **手动拷贝**：

| 文件 | 大小 | 说明 |
|------|------|------|
| `market.db` | ~2.7 GB | SQLite 核心数据库（**最重要**） |
| `user_data.db` | ~127 KB | 用户数据 |
| `market_enriched.csv` | ~895 KB | 市场增强数据 |
| `data/` 目录 | — | 数据目录 |
| `stock_industry_base.csv` | ~346 KB | 行业分类数据 |
| `coverage_report.csv` | ~382 KB | 覆盖率报告 |

> ⚠️ `market.db` 有 2.7GB，建议用移动硬盘或云存储传输。

### 5. 启动项目

**方式 A：Docker（推荐）**

```bash
docker compose up --build
```

- 前端 → http://localhost:80
- 后端 → http://localhost:8000

**方式 B：本地开发环境**

```bash
# 后端
python3 -m venv venv
source venv/bin/activate          # Windows 用: venv\Scripts\activate
pip install -r web/backend/requirements.txt
uvicorn web.backend.main:app --reload --port 8000

# 前端
cd web/frontend
npm install
npm run dev
```

---

## 二、日常来回切换工作流

### 离开当前电脑前

```bash
git add -A
git commit -m "work in progress"
git push
```

### 到另一台电脑后

```bash
git pull
```

> 💡 `market.db` 不通过 Git 同步。如果数据库有更新，需手动拷贝。

---

## 三、注意事项

1. **换行符**：Git 会自动处理 Windows（CRLF）和 Mac（LF）的换行差异，无需担心。
2. **路径分隔符**：代码中使用 `os.path.join()` 或 `pathlib.Path` 而非硬编码 `\` 或 `/`。
3. **数据库同步**：如需频繁同步数据库，可考虑迁移到云数据库（如 PostgreSQL）。
4. **Docker 差异**：Mac 上 Docker 命令为 `docker compose`（无横杠），与 Windows 一致。
