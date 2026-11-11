"""生成离线示例公告站（纯静态 HTML + 跳转规则清单 redirects.json）。

场景覆盖（查询时刻 2026-11-11T10:00+08:00）：
  001 12 路原始停运，查询时有效
  002 18 路已过停运结束时间（过期）
  003 25 路原始停运，006 更正、010 再更正（更正引用更正，区间被两次替换）
  004 33 路停运，007 撤销（整条链失效）
  005 12 路另一条独立停运（同线路独立判断），012 更正发布于查询时刻之后（不可见）
  008 5 路停运区间尚未开始
  009 40 路停运，011 更正同时引用了不存在的 N-2026-999（无法匹配的引用）
  013 50 路整条公告发布于查询时刻之后（时间门控）
  014 8 路停运，详情页站内迁移：旧地址 301 至 /archive/ 下新地址
  另有一个跨站详情链接（N-EXT-001），验证同站限制。

跳转示例（规则写入 redirects.json，由 server.py 执行）：
  /old/index.html            301 -> /bulletin/index.html   公告栏目站内迁移，
                              新栏目分页为相对链接，须按最终地址解析
  /notices/N-2026-014.html   301 -> /archive/notices/N-2026-014.html  详情站内迁移
  /notices/N-EXT-002.html    302 -> http://external.example.org/...   跨站跳转
  /loop-a.html <-> /loop-b.html  循环跳转
  /chain-0.html -> ... -> /chain-8.html  超长跳转链（超过采集上限）
  后三者挂在独立入口 /alerts/index.html 上，命中即无法核定。
"""
from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

SITE_DIR = Path(__file__).parent
OUT_DIR = SITE_DIR / "html"
REDIRECTS_FILE = SITE_DIR / "redirects.json"
PAGE_SIZE = 5
TZ = "+08:00"

EXTERNAL_HOST = "http://external.example.org"


@dataclass
class Notice:
    notice_id: str
    kind: str  # suspension | correction | cancellation
    title: str
    route: str | None
    published_at: str
    suspend_start: str | None = None
    suspend_end: str | None = None
    references: list[str] = field(default_factory=list)
    offsite: bool = False  # 列表项指向外站
    moved: bool = False    # 详情页已迁至站内新地址（旧地址 301 跳转）


NOTICES: list[Notice] = [
    Notice("N-2026-001", "suspension", "12 路临时停运公告", "12 路",
           "2026-11-08T09:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-12T22:00:00+08:00"),
    Notice("N-2026-002", "suspension", "18 路临时停运公告", "18 路",
           "2026-11-03T14:00:00+08:00", "2026-11-05T06:00:00+08:00",
           "2026-11-08T20:00:00+08:00"),
    Notice("N-2026-003", "suspension", "25 路临时停运公告", "25 路",
           "2026-11-07T16:30:00+08:00", "2026-11-09T06:00:00+08:00",
           "2026-11-12T20:00:00+08:00"),
    Notice("N-2026-004", "suspension", "33 路临时停运公告", "33 路",
           "2026-11-08T11:00:00+08:00", "2026-11-10T05:30:00+08:00",
           "2026-11-20T22:00:00+08:00"),
    Notice("N-2026-005", "suspension", "12 路临时停运公告（施工绕行）", "12 路",
           "2026-11-09T17:20:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-13T21:00:00+08:00"),
    Notice("N-2026-006", "correction", "25 路停运时间更正公告", "25 路",
           "2026-11-09T15:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-15T22:00:00+08:00", references=["N-2026-003"]),
    Notice("N-2026-007", "cancellation", "33 路停运撤销公告", "33 路",
           "2026-11-09T10:00:00+08:00", references=["N-2026-004"]),
    Notice("N-2026-008", "suspension", "5 路临时停运公告", "5 路",
           "2026-11-09T08:00:00+08:00", "2026-11-12T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00"),
    Notice("N-2026-009", "suspension", "40 路临时停运公告", "40 路",
           "2026-11-10T09:30:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-13T20:00:00+08:00"),
    Notice("N-2026-010", "correction", "25 路停运时间第二次更正公告", "25 路",
           "2026-11-10T08:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-20T22:00:00+08:00", references=["N-2026-006"]),
    Notice("N-2026-011", "correction", "40 路停运更正公告（含一处错误引用）", "40 路",
           "2026-11-10T18:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-16T20:00:00+08:00",
           references=["N-2026-009", "N-2026-999"]),
    Notice("N-2026-012", "correction", "12 路施工绕行安排更正（次日发布）", "12 路",
           "2026-11-12T09:00:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-14T21:00:00+08:00", references=["N-2026-005"]),
    Notice("N-2026-013", "suspension", "50 路临时停运公告（提前发布）", "50 路",
           "2026-11-14T09:00:00+08:00", "2026-11-15T06:00:00+08:00",
           "2026-11-18T20:00:00+08:00"),
    Notice("N-2026-014", "suspension", "8 路临时停运公告（详情页已迁移）", "8 路",
           "2026-11-08T10:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-12T23:00:00+08:00", moved=True),
    Notice("N-EXT-001", "suspension", "外站转载公告（跨站链接示例）", "99 路",
           "2026-11-08T09:00:00+08:00", offsite=True),
]

# 仅挂在 /alerts/ 入口下的正常公告（用于演示“无法核定时有效结论被抑制”）
ALERTS_ONLY_NOTICES: list[Notice] = [
    Notice("N-2026-015", "suspension", "15 路临时停运公告", "15 路",
           "2026-11-10T09:00:00+08:00", "2026-11-11T00:00:00+08:00",
           "2026-11-12T20:00:00+08:00"),
]

# /alerts/ 入口的列表项：(编号, 类型, 标题, 链接)。链接用绝对路径，
# 其中三条分别触发跨站跳转、循环跳转、超长跳转链。
ALERTS_ARTICLES: list[tuple[str, str, str, str]] = [
    ("N-2026-015", "suspension", "15 路临时停运公告", "/notices/N-2026-015.html"),
    ("N-EXT-002", "suspension", "镜像详情（跨站跳转示例）", "/notices/N-EXT-002.html"),
    ("N-LOOP-001", "suspension", "循环跳转示例公告", "/loop-a.html"),
    ("N-CHAIN-001", "suspension", "超长跳转链示例公告", "/chain-0.html"),
]

# 跳转规则：path -> (状态码, Location)。server.py 按 redirects.json 执行。
CHAIN_LEN = 8  # 跳转链长度（超过采集器的跳转上限 5）
REDIRECT_RULES: list[dict] = [
    # 公告栏目整体迁至 /bulletin/，旧入口 301 至新地址
    {"path": "/old/index.html", "status": 301, "location": "/bulletin/index.html"},
    # 详情页站内迁移：旧地址 301 至 /archive/ 下的新地址
    {"path": "/notices/N-2026-014.html", "status": 301,
     "location": "/archive/notices/N-2026-014.html"},
    # 同站地址跳往外站（跨站跳转，采集器不得请求目标）
    {"path": "/notices/N-EXT-002.html", "status": 302,
     "location": f"{EXTERNAL_HOST}/notice/N-EXT-002.html"},
    # 循环跳转
    {"path": "/loop-a.html", "status": 302, "location": "/loop-b.html"},
    {"path": "/loop-b.html", "status": 302, "location": "/loop-a.html"},
    # 超长跳转链（超过采集上限）
    *[
        {"path": f"/chain-{i}.html", "status": 302, "location": f"/chain-{i + 1}.html"}
        for i in range(CHAIN_LEN + 1)
    ],
]

KIND_CN = {"suspension": "停运", "correction": "更正", "cancellation": "撤销"}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def detail_path(n: Notice) -> str:
    """详情页文件的实际存放路径（迁移公告在 /archive/ 下）。"""
    if n.moved:
        return f"archive/notices/{n.notice_id}.html"
    return f"notices/{n.notice_id}.html"


def list_href(n: Notice) -> str:
    """主列表中详情链接的 href（迁移公告仍写旧地址，由服务器 301）。"""
    if n.offsite:
        return f"{EXTERNAL_HOST}/notice/{n.notice_id}.html"
    if n.moved:
        return f"notices/{n.notice_id}.html"  # 旧地址，301 至 /archive/
    return detail_path(n)


def render_detail(n: Notice) -> str:
    rows: list[str] = []

    def row(field_name: str, label: str, value: str | None, is_time: bool = False) -> None:
        if value is None:
            return
        if is_time:
            cell = f'<time datetime="{_esc(value)}">{_esc(value.replace("T", " "))}</time>'
        else:
            cell = _esc(value)
        rows.append(
            f'      <tr data-field="{field_name}"><th>{label}</th><td>{cell}</td></tr>'
        )

    row("notice_id", "公告编号", n.notice_id)
    row("kind", "类型", KIND_CN[n.kind])
    row("route", "线路", n.route)
    row("suspend_start", "停运开始时间", n.suspend_start, is_time=True)
    row("suspend_end", "停运结束时间", n.suspend_end, is_time=True)
    row("published_at", "发布时间", n.published_at, is_time=True)

    refs_html = ""
    if n.references:
        links = "、".join(
            f'<a class="reference" data-ref-id="{_esc(r)}" href="/notices/{_esc(r)}.html">'
            f"{_esc(r)}</a>"
            for r in n.references
        )
        refs_html = f"\n    <section class=refs><h2>引用公告</h2><p>{links}</p></section>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_esc(n.title)}</title></head>
<body>
  <article>
    <h1 class="notice-title">{_esc(n.title)}</h1>
    <table class="notice-fields">
{chr(10).join(rows)}
    </table>{refs_html}
    <p><a href="../index.html">返回公告列表</a></p>
  </article>
</body>
</html>
"""


def render_list(
    page_notices: list[Notice],
    page_idx: int,
    total_pages: int,
    *,
    absolute_detail_links: bool = False,
) -> str:
    """渲染列表页。

    ``absolute_detail_links=True`` 时详情链接写站内绝对路径（栏目迁移后的
    新列表仍指向原详情地址），分页始终保持相对链接。
    """
    items: list[str] = []
    for n in page_notices:
        href = list_href(n)
        if absolute_detail_links and not n.offsite:
            href = "/" + href
        items.append(
            f'    <article class="notice" data-id="{_esc(n.notice_id)}" '
            f'data-kind="{n.kind}">\n'
            f'      <a class="detail-link" href="{_esc(href)}">{_esc(n.title)}</a>\n'
            f'      <span class="published">{_esc(n.published_at)}</span>\n'
            f"    </article>"
        )

    pager_parts: list[str] = []
    if page_idx > 0:
        prev_file = "index.html" if page_idx == 1 else f"page{page_idx}.html"
        pager_parts.append(f'<a rel="prev" href="{prev_file}">上一页</a>')
    if page_idx < total_pages - 1:
        next_file = "page2.html" if page_idx == 0 else f"page{page_idx + 2}.html"
        pager_parts.append(f'<a rel="next" href="{next_file}">下一页</a>')
    pager = f'  <nav class="pager">{" ".join(pager_parts)}</nav>' if pager_parts else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>线路公告列表（第 {page_idx + 1} 页）</title></head>
<body>
  <h1>线路公告列表</h1>
{chr(10).join(items)}
{pager}
</body>
</html>
"""


def render_alerts() -> str:
    items = [
        f'    <article class="notice" data-id="{_esc(nid)}" data-kind="{kind}">\n'
        f'      <a class="detail-link" href="{_esc(href)}">{_esc(title)}</a>\n'
        f"    </article>"
        for nid, kind, title, href in ALERTS_ARTICLES
    ]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>镜像公告列表（跳转异常演示入口）</title></head>
<body>
  <h1>镜像公告列表</h1>
{chr(10).join(items)}
</body>
</html>
"""


def _write_list_section(base: Path, absolute_detail_links: bool) -> int:
    """把 NOTICES 分页写入指定目录，返回页数。"""
    pages: list[list[Notice]] = [
        NOTICES[i : i + PAGE_SIZE] for i in range(0, len(NOTICES), PAGE_SIZE)
    ]
    total = len(pages)
    base.mkdir(parents=True, exist_ok=True)
    for idx, page_notices in enumerate(pages):
        name = "index.html" if idx == 0 else f"page{idx + 1}.html"
        (base / name).write_text(
            render_list(
                page_notices, idx, total, absolute_detail_links=absolute_detail_links
            ),
            encoding="utf-8",
        )
    return total


def build() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "notices").mkdir(parents=True)

    # 详情页（含站内迁移至 /archive/ 的 014 与仅 alerts 入口的 015）
    real_notices = [n for n in NOTICES + ALERTS_ONLY_NOTICES if not n.offsite]
    for n in real_notices:
        path = OUT_DIR / detail_path(n)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_detail(n), encoding="utf-8")

    # 主列表（旧结构，相对详情链接）
    total = _write_list_section(OUT_DIR, absolute_detail_links=False)
    # 迁移后的新栏目 /bulletin/：同样的公告，分页为相对链接，详情写绝对路径
    _write_list_section(OUT_DIR / "bulletin", absolute_detail_links=True)
    # 跳转异常演示入口 /alerts/
    (OUT_DIR / "alerts").mkdir(parents=True)
    (OUT_DIR / "alerts" / "index.html").write_text(render_alerts(), encoding="utf-8")

    REDIRECTS_FILE.write_text(
        json.dumps({"rules": REDIRECT_RULES}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"示例站点已生成: {OUT_DIR}（{len(real_notices)} 条公告，{total} 个列表页，"
        f"另有 /bulletin/ 迁移栏目与 /alerts/ 跳转演示入口，"
        f"跳转规则 {len(REDIRECT_RULES)} 条 -> {REDIRECTS_FILE}）"
    )


if __name__ == "__main__":
    build()
