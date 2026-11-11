#!/usr/bin/env bash
# 离线演示：不依赖 Docker，用本地 venv 启动示例公告站与采集 API。
# 用法: ./demo/run_local_demo.sh [查询时刻 ISO8601]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
QUERY_TIME="${1:-2026-11-11T10:00:00+08:00}"
SITE_BASE="http://127.0.0.1:8080"
SITE_URL="$SITE_BASE/index.html"

if [[ ! -x "$PY" ]]; then
  echo "缺少虚拟环境，请先创建: python3 -m venv --without-pip .venv && 引导 pip 后 pip install -r app/requirements.txt" >&2
  exit 1
fi

# 1) 生成示例静态公告站（含跳转规则 redirects.json）
"$PY" -I "$ROOT/site/build_site.py"

cleanup() {
  [[ -n "${SITE_PID:-}" ]] && kill "$SITE_PID" 2>/dev/null || true
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

# 2) 启动示例公告站（8080，静态文件 + 跳转规则）
"$PY" -I "$ROOT/site/server.py" --bind 127.0.0.1 --port 8080 &
SITE_PID=$!

# 3) 启动采集 API（8000）
( cd "$ROOT/app" && "$PY" -I -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
API_PID=$!

for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:8000/health >/dev/null && curl -sf "$SITE_URL" >/dev/null && break
  sleep 0.5
done

query() {
  curl -s -X POST http://127.0.0.1:8000/query \
    -H 'Content-Type: application/json' \
    -d "{\"index_url\":\"$1\",\"query_time\":\"$QUERY_TIME\"}" | "$PY" -I -m json.tool
}

echo "== 查询时刻: $QUERY_TIME =="
echo "== 1) 主入口（含详情页站内迁移 N-2026-014） =="
query "$SITE_URL"

echo "== 2) 栏目迁移入口 /old/index.html（301 至 /bulletin/，相对分页按最终地址解析） =="
query "$SITE_BASE/old/index.html"

echo "== 3) 跳转异常入口 /alerts/index.html（跨站/循环/超限跳转 -> 无法核定） =="
query "$SITE_BASE/alerts/index.html"
