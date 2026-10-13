#!/usr/bin/env bash
# Install the Docker-compatible Python 3.11 backend test environment locally.
set -euo pipefail

task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
task_env="$task_root/.test-env"
cd "$task_root"

command -v uv >/dev/null || { echo "需要先安装 uv：https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }
test -f pytdx3/pyproject.toml || { echo "缺少 pytdx3 子模块，请先执行 git submodule update --init pytdx3" >&2; exit 1; }

if ! test -x "$task_env/venv/bin/python"; then
    env UV_PYTHON_INSTALL_DIR="$task_env/python" uv venv --python 3.11 \
        --cache-dir "$task_env/cache" "$task_env/venv"
fi
"$task_env/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), "测试环境需要 Python 3.11"'
uv pip sync --python "$task_env/venv/bin/python" --cache-dir "$task_env/cache" \
    web/backend/requirements-test.txt
# Use the exact submodule source also installed by the backend Dockerfile.
uv pip install --python "$task_env/venv/bin/python" --cache-dir "$task_env/cache" --no-deps ./pytdx3
uv pip check --python "$task_env/venv/bin/python"
echo "测试环境就绪。运行：.test-env/venv/bin/python -m pytest"
