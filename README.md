# 公交临时停运公告采集 API

调度员出车前，需要从运营方**分页发布**的线路公告中，查出“查询时刻仍有效”的临时停运。
本服务用 FastAPI + BeautifulSoup 抓取同站分页与详情页，按公告编号引用关系与发布时间
还原**更正/撤销公告链**，返回有效停运区间、来源链接及无法匹配的引用。

## 业务规则

| 规则 | 实现 |
| --- | --- |
| 公告按编号唯一标识 | `Announcement.notice_id` |
| 更正可引用另一条更正 | 引用图上从根（无有效出边引用的公告）沿后继遍历，支持任意深度 |
| 链顺序 | 引用关系定链，同一节点多个后继按发布时间排序 |
| 更正替换原停运信息 | 按发布时间依次应用，链上最后一条可见公告的起止/线路为当前生效信息 |
| 撤销使整条链失效 | 链上出现查询时刻已发布的撤销公告 → `inactive_chains[].reason=cancelled` |
| 未来公告不可见 | 发布时间晚于查询时刻的公告不参与链还原与判定 |
| 同线路独立公告分别判断 | 每条根公告独立成链；示例中 12 路有两条同时有效的独立停运 |
| 无法匹配的引用 | 被引用编号在整站不存在时收集到 `unmatched_references`，不影响其余判定 |
| 同站限制 | 只沿与输入首页同源（协议+主机+端口一致）的链接爬行，跨站链接跳过并在 `skipped_offsite_links` 返回 |
| 跳转同源约束 | 列表/详情的每次跳转逐一检查，目标必须与输入首页同源；跨站目标**不发出请求** |
| 站内迁移 | 站内跳转后按**最终地址**解析相对分页/详情链接，详情来源记录最终地址 |
| 跳转失败 | 跨站/循环/超限跳转记录到 `redirect_failures`（地址+目标+原因），本次查询 `verification_status=unverifiable`，不输出有效停运结论 |

停运区间按半开 `[suspend_start, suspend_end)` 判定：查询时刻等于结束时刻即视为已失效。
所有时间均要求带时区（ISO 8601 偏移），比较基于同一时间线瞬间。

### 跳转与可核定性

运营方把公告页迁到站内新地址（或页面跳往外站）时，采集行为如下：

* **站内跳转（同源）**：跟随，最多 5 跳；后续相对链接以跳转后的最终地址为基准解析，
  `list_pages_crawled` 与公告 `sources` 均记录最终地址。
* **跨站跳转**：目标与输入首页不同源时不发出请求，记录 `redirect_failures`。
* **循环跳转 / 超过 5 跳**：同样记录 `redirect_failures`。
* 只要存在任一跳转失败，即说明采集可能不完整（缺失的页面也许是撤销公告），
  响应为 `verification_status="unverifiable"` 且 `active_suspensions` 置空——
  不输出确定的有效停运结论；`inactive_chains` / `unmatched_references` 仍返回供排查。
* 页面上直接书写的外站链接（非跳转）照旧跳过，计入 `skipped_offsite_links`，不影响可核定性。

## 目录结构

```
app/                 FastAPI 采集服务
  app/models.py      Pydantic 模型（入参/出参/公告实体/跳转失败）
  app/parser.py      BeautifulSoup 解析：分页 rel=next、详情链接、详情字段；同源判定
  app/crawler.py     同站分页 + 详情抓取（httpx 异步，手动跟随跳转：同源检查/限量/去重/错误收集）
  app/engine.py      公告链还原与有效性判定（纯函数，无 IO）
  app/main.py        POST /query、GET /health
  Dockerfile
site/                本地示例公告站（数据驱动生成纯静态 HTML + 跳转规则）
  build_site.py      生成 html/ 与 redirects.json（主列表 + 迁移栏目 + 跳转演示入口）
  server.py          静态文件 + 跳转规则的本地服务器（仅标准库）
  Dockerfile         打包 html/、redirects.json、server.py
tests/               33 个单元/集成测试（pytest）
demo/                本地与 Compose 演示脚本
docker-compose.yml   api(8000) + notice-site(8080)
```

## 运行方式

### 方式一：Docker Compose（推荐，完整离线演示）

```bash
python3 site/build_site.py          # 生成示例站静态文件
docker compose up -d --build

curl -s -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"index_url":"http://notice-site:8080/index.html",
       "query_time":"2026-11-11T10:00:00+08:00"}'
```

容器内 API 通过 Compose 网络访问 `notice-site`；从宿主机调用时 `index_url`
也可换成 `http://127.0.0.1:8080/index.html`（两个地址同构，同站判定均成立）。
交互式文档：http://127.0.0.1:8000/docs

### 方式二：本地 venv（无 Docker 环境）

```bash
python3 -m venv --without-pip .venv
.venv/bin/python get-pip.py        # 若 ensurepip 不可用，先 bootstrap pip
.venv/bin/pip install -r app/requirements.txt
./demo/run_local_demo.sh           # 自动建站、起两个服务并发起查询
```

### 测试

```bash
.venv/bin/pip install pytest pytest-asyncio
python3 site/build_site.py                       # 生成 html/ 与 redirects.json
python3 site/server.py &                         # 集成测试需要示例站（含跳转规则）
.venv/bin/python -m pytest -q
```

## 示例站点场景（查询时刻 2026-11-11 10:00 +08:00）

| 公告 | 场景 | 判定结果 |
| --- | --- | --- |
| N-2026-001 | 12 路原始停运 | 有效 |
| N-2026-002 | 18 路停运已结束 | `expired` |
| 003 → 006 → 010 | 25 路停运被两次更正（更正引用更正） | 有效，区间为 010 替换后的 11-10 ~ 11-20 |
| 004 → 007 | 33 路停运被撤销 | `cancelled`，整条链失效 |
| N-2026-005 | 12 路另一条独立停运；012 更正次日才发布 | 有效，链上只有 005 |
| N-2026-008 | 5 路停运尚未开始 | `not_yet_effective` |
| 009 → 011 | 40 路更正，且 011 误引用不存在的 N-2026-999 | 有效，同时报告悬空引用 |
| N-2026-013 | 50 路整条公告查询时刻之后才发布 | `not_yet_effective` |
| N-2026-014 | 8 路停运，详情页站内迁移（旧地址 301 至 `/archive/`） | 有效，来源记录最终地址 |
| N-EXT-001 | 列表中的跨站链接 | 跳过，出现在 `skipped_offsite_links` |

### 跳转示例入口

| 入口 | 说明 | 预期 |
| --- | --- | --- |
| `/old/index.html` | 公告栏目站内迁移，301 至 `/bulletin/index.html`；新栏目分页为相对链接 | 跟随跳转，相对分页按最终地址解析，结果与主入口一致 |
| `/notices/N-2026-014.html` | 详情页站内迁移，301 至 `/archive/notices/N-2026-014.html` | 跟随，`sources` 记录最终地址 |
| `/alerts/index.html` | 跳转异常演示入口：同站地址 302 至外站、循环跳转、超长跳转链 | `verification_status=unverifiable`，`active_suspensions` 为空，三类失败均列出地址与原因 |

## 接入真实站点

真实页面结构不同时，只需替换 `app/parser.py` 中的 CSS 选择器与字段键映射
（`FIELD_KEYS` / `KIND_MAP`），引擎、爬虫与 API 契约无需改动。解析器约定详情页
使用 `<time datetime>` 携带带时区的 ISO 8601 时间、引用使用
`<a class="reference" data-ref-id="编号">`，避免依赖文案措辞。
