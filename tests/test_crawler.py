"""爬虫层：跳转策略（同源约束、最终地址解析、失败记录）。

用 httpx.MockTransport 在内存中模拟公告站，不依赖网络与本地示例站。
"""
from __future__ import annotations

import httpx
import pytest

from app.crawler import Crawler
from app.models import REDIRECT_CROSS_SITE, REDIRECT_LOOP, REDIRECT_TOO_MANY

ROOT = "http://site.test/index.html"

pytestmark = pytest.mark.asyncio


def detail_html(notice_id: str, route: str = "1 路") -> str:
    return f"""<!DOCTYPE html><html><body>
    <h1 class="notice-title">{notice_id}</h1>
    <table class="notice-fields">
      <tr data-field="notice_id"><th>公告编号</th><td>{notice_id}</td></tr>
      <tr data-field="kind"><th>类型</th><td>停运</td></tr>
      <tr data-field="route"><th>线路</th><td>{route}</td></tr>
      <tr data-field="suspend_start"><th>停运开始</th>
        <td><time datetime="2026-11-10T06:00:00+08:00">x</time></td></tr>
      <tr data-field="suspend_end"><th>停运结束</th>
        <td><time datetime="2026-11-12T22:00:00+08:00">x</time></td></tr>
      <tr data-field="published_at"><th>发布时间</th>
        <td><time datetime="2026-11-08T09:00:00+08:00">x</time></td></tr>
    </table>
    </body></html>"""


def list_html(*links: str, next_href: str | None = None) -> str:
    items = "".join(
        f'<article class="notice" data-id="{i}" data-kind="suspension">'
        f'<a class="detail-link" href="{href}">n{i}</a></article>'
        for i, href in enumerate(links)
    )
    pager = f'<nav class="pager"><a rel="next" href="{next_href}">下一页</a></nav>' if next_href else ""
    return f"<html><body>{items}{pager}</body></html>"


class FakeSite:
    """路径 -> (状态码, Location 或 body)。记录所有被请求的 URL。"""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, str]] = {}
        self.requested: list[str] = []

    def page(self, path: str, body: str) -> None:
        self.routes[path] = (200, body)

    def redirect(self, path: str, location: str, status: int = 301) -> None:
        self.routes[path] = (status, location)

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requested.append(str(request.url))
            path = request.url.path
            if path not in self.routes:
                return httpx.Response(404, text="not found")
            status, payload = self.routes[path]
            if status in (301, 302, 303, 307, 308):
                return httpx.Response(status, headers={"Location": payload})
            return httpx.Response(status, text=payload)

        return httpx.MockTransport(handler)

    def crawler(self, **kwargs) -> Crawler:
        return Crawler(httpx.AsyncClient(transport=self.transport), **kwargs)


async def test_insite_redirect_resolves_relative_links_by_final_url():
    """列表页 301 至新目录：相对分页与相对详情链接都按最终地址解析。"""
    site = FakeSite()
    site.redirect("/index.html", "/bulletin/index.html")
    # 新目录下的列表：相对分页 page2.html、相对详情 n1.html
    site.page("/bulletin/index.html", list_html("n1.html", next_href="page2.html"))
    site.page("/bulletin/page2.html", list_html("/notices/n2.html"))
    site.page("/bulletin/n1.html", detail_html("N1"))
    # 详情页也发生站内迁移，来源应记录最终地址
    site.redirect("/notices/n2.html", "/archive/n2.html")
    site.page("/archive/n2.html", detail_html("N2", route="2 路"))

    result = await site.crawler().crawl(ROOT)

    assert result.redirect_failures == []
    assert result.errors == []
    assert result.list_pages == [
        "http://site.test/bulletin/index.html",
        "http://site.test/bulletin/page2.html",
    ]
    assert {a.notice_id for a in result.announcements} == {"N1", "N2"}
    urls = {a.notice_id: a.url for a in result.announcements}
    assert urls["N1"] == "http://site.test/bulletin/n1.html"
    assert urls["N2"] == "http://site.test/archive/n2.html"  # 详情真实来源


async def test_cross_site_redirect_is_not_requested():
    """同站地址跳往外站：不请求跨站目标，记录失败。"""
    site = FakeSite()
    site.page("/index.html", list_html("/go.html"))
    site.redirect("/go.html", "http://external.test/x.html", status=302)

    result = await site.crawler().crawl(ROOT)

    assert result.announcements == []
    assert len(result.redirect_failures) == 1
    failure = result.redirect_failures[0]
    assert failure.url == "http://site.test/go.html"
    assert failure.target == "http://external.test/x.html"
    assert failure.reason == REDIRECT_CROSS_SITE
    # 跨站目标不得发出请求
    assert not any(u.startswith("http://external.test/") for u in site.requested)


async def test_list_page_cross_site_redirect_stops_pagination():
    """列表页本身跳往外站：不请求目标，记录失败且无列表页。"""
    site = FakeSite()
    site.redirect("/index.html", "http://external.test/index.html", status=302)

    result = await site.crawler().crawl(ROOT)

    assert result.list_pages == []
    assert [f.reason for f in result.redirect_failures] == [REDIRECT_CROSS_SITE]
    assert site.requested == ["http://site.test/index.html"]


async def test_redirect_loop_detected():
    site = FakeSite()
    site.page("/index.html", list_html("/a.html"))
    site.redirect("/a.html", "/b.html", status=302)
    site.redirect("/b.html", "/a.html", status=302)

    result = await site.crawler().crawl(ROOT)

    assert len(result.redirect_failures) == 1
    failure = result.redirect_failures[0]
    assert failure.reason == REDIRECT_LOOP
    assert failure.url == "http://site.test/b.html"
    assert failure.target == "http://site.test/a.html"


async def test_too_many_redirects_detected():
    site = FakeSite()
    site.page("/index.html", list_html("/chain-0.html"))
    for i in range(6):
        site.redirect(f"/chain-{i}.html", f"/chain-{i + 1}.html", status=302)

    result = await site.crawler(max_redirects=3).crawl(ROOT)

    assert len(result.redirect_failures) == 1
    failure = result.redirect_failures[0]
    assert failure.reason == REDIRECT_TOO_MANY
    assert failure.url == "http://site.test/chain-3.html"
    assert failure.target == "http://site.test/chain-4.html"
    # 只跟随到上限为止
    assert "http://site.test/chain-4.html" not in site.requested


async def test_redirect_target_checked_against_input_root_not_current_url():
    """同源基准始终是输入首页：先站内跳转到同站其他路径，再跳外站仍被拒绝。"""
    site = FakeSite()
    site.page("/index.html", list_html("/step1.html"))
    site.redirect("/step1.html", "/step2.html")
    site.redirect("/step2.html", "//external.test/clone.html", status=302)

    result = await site.crawler().crawl(ROOT)

    assert [f.reason for f in result.redirect_failures] == [REDIRECT_CROSS_SITE]
    assert result.redirect_failures[0].target == "http://external.test/clone.html"
    assert not any("external.test" in u for u in site.requested)


async def test_offsite_detail_link_still_skipped_without_request():
    """页面上的跨站链接（非跳转）照旧跳过，不影响可核定性。"""
    site = FakeSite()
    site.page("/index.html", list_html("http://external.test/x.html", "/n1.html"))
    site.page("/n1.html", detail_html("N1"))

    result = await site.crawler().crawl(ROOT)

    assert result.redirect_failures == []
    assert result.skipped_offsite == ["http://external.test/x.html"]
    assert {a.notice_id for a in result.announcements} == {"N1"}
    assert not any("external.test" in u for u in site.requested)


async def test_pagination_cycle_via_redirect_terminates():
    """分页经跳转指回已抓页面时不死循环。"""
    site = FakeSite()
    site.page("/index.html", list_html("/n1.html", next_href="/page2.html"))
    site.redirect("/page2.html", "/index.html")  # 跳回首页
    site.page("/n1.html", detail_html("N1"))

    result = await site.crawler().crawl(ROOT)

    assert result.errors == []
    assert result.list_pages == ["http://site.test/index.html"]
    assert {a.notice_id for a in result.announcements} == {"N1"}
