#!/usr/bin/env bash
# Docker Compose 离线演示：构建并启动 API + 本地示例公告站。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUERY_TIME="${1:-2026-11-11T10:00:00+08:00}"
SITE_BASE="http://notice-site:8080"

# 示例站镜像直接打包静态 HTML 与跳转规则，先确保已生成
python3 "$ROOT/site/build_site.py"

cd "$ROOT"
docker compose up -d --build

for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:8000/health >/dev/null && break
  sleep 1
done

query() {
  echo "== 入口: $1 =="
  curl -s -X POST http://127.0.0.1:8000/query \
    -H 'Content-Type: application/json' \
    -d '{"index_url":"'"$1"'","query_time":"'"$QUERY_TIME"'"}'
  echo
}

# 1) 主入口；2) 栏目迁移入口（301 至 /bulletin/）；3) 跳转异常入口（无法核定）
query "$SITE_BASE/index.html"
query "$SITE_BASE/old/index.html"
query "$SITE_BASE/alerts/index.html"

echo "演示完成，停止服务: docker compose down"
