"""同站分页 + 详情抓取，跳转全程受同源策略约束。

约束：
* 只沿同站链接爬行（与输入首页同源：协议/主机/端口一致），跨站分页或详情链接跳过并记录；
* 列表页与详情页的每次跳转都逐一检查：目标必须与输入首页同源，跨站目标**不发出请求**；
  循环跳转与超过跳转上限同样视为失败。三类失败都记录地址与原因，本次查询应标为无法核定；
* 站内跳转后，相对链接（分页、详情）按**最终地址**解析，详情来源记录最终地址；
* 分页跟随 ``rel=next``，带最大页数与死循环保护；
* 详情 URL 去重，抓取失败不中断整体查询，作为错误收集返回。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from .models import (
    REDIRECT_CROSS_SITE,
    REDIRECT_LOOP,
    REDIRECT_TOO_MANY,
    Announcement,
    RedirectFailure,
)
from .parser import is_same_site, parse_detail, parse_detail_links, parse_next_page


@dataclass
class CrawlResult:
    announcements: list[Announcement] = field(default_factory=list)
    list_pages: list[str] = field(default_factory=list)  # 记录最终地址
    detail_pages: int = 0
    skipped_offsite: list[str] = field(default_factory=list)
    redirect_failures: list[RedirectFailure] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class _Fetch:
    """单次抓取（含跳转链）的结果。"""

    final_url: str | None = None      # 跳转链终点地址（无跳转即请求地址）
    html: str | None = None
    error: str | None = None          # 网络/HTTP 错误描述
    redirect_failure: RedirectFailure | None = None


class Crawler:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_list_pages: int = 50,
        max_detail_pages: int = 500,
        max_redirects: int = 5,
    ) -> None:
        self._client = client
        self._max_list_pages = max_list_pages
        self._max_detail_pages = max_detail_pages
        self._max_redirects = max_redirects

    async def _get(self, url: str, *, root: str) -> _Fetch:
        """抓取 ``url``，手动跟随跳转并逐一执行同源检查。

        ``root`` 为输入首页地址，是同源判定的基准。跨站目标不会被请求。
        """
        current = url
        visited = {url}
        hops = 0
        while True:
            try:
                resp = await self._client.get(current, follow_redirects=False)
            except httpx.HTTPError as exc:
                return _Fetch(error=f"{type(exc).__name__}: {exc}")

            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location", "").strip()
                if not location:
                    return _Fetch(error=f"HTTP {resp.status_code} 跳转响应缺少 Location 头")
                target = urljoin(current, location)
                if not is_same_site(target, root):
                    return _Fetch(
                        redirect_failure=RedirectFailure(
                            url=current, target=target, reason=REDIRECT_CROSS_SITE
                        )
                    )
                if target in visited:
                    return _Fetch(
                        redirect_failure=RedirectFailure(
                            url=current, target=target, reason=REDIRECT_LOOP
                        )
                    )
                if hops >= self._max_redirects:
                    return _Fetch(
                        redirect_failure=RedirectFailure(
                            url=current, target=target, reason=REDIRECT_TOO_MANY
                        )
                    )
                visited.add(target)
                current = target
                hops += 1
                continue

            if resp.status_code >= 400:
                return _Fetch(error=f"HTTP {resp.status_code}")
            return _Fetch(final_url=current, html=resp.text)

    async def crawl(self, index_url: str) -> CrawlResult:
        result = CrawlResult()
        seen_pages: set[str] = set()
        detail_urls: list[str] = []
        seen_details: set[str] = set()

        # 1) 抓列表分页（只走同站 rel=next；跳转按最终地址解析相对链接）
        page_url: str | None = index_url
        while page_url and len(seen_pages) < self._max_list_pages:
            if page_url in seen_pages:
                break
            seen_pages.add(page_url)
            fetch = await self._get(page_url, root=index_url)
            if fetch.redirect_failure:
                result.redirect_failures.append(fetch.redirect_failure)
                break
            if fetch.error or fetch.html is None or fetch.final_url is None:
                result.errors.append(f"列表页抓取失败 {page_url}: {fetch.error}")
                break

            final_url = fetch.final_url
            if final_url != page_url:
                if final_url in seen_pages:
                    break  # 跳转回已抓过的页面，分页成环，停止
                seen_pages.add(final_url)
            result.list_pages.append(final_url)
            html = fetch.html

            for detail_url, _id, _kind in parse_detail_links(html, final_url):
                if not is_same_site(detail_url, index_url):
                    result.skipped_offsite.append(detail_url)
                    continue
                if detail_url not in seen_details:
                    seen_details.add(detail_url)
                    detail_urls.append(detail_url)

            nxt = parse_next_page(html, final_url)
            if nxt and not is_same_site(nxt, index_url):
                result.skipped_offsite.append(nxt)
                nxt = None
            page_url = nxt

        # 2) 抓详情（同站已保证；站内跳转后以来源最终地址为准），限量保护
        for detail_url in detail_urls[: self._max_detail_pages]:
            fetch = await self._get(detail_url, root=index_url)
            if fetch.redirect_failure:
                result.redirect_failures.append(fetch.redirect_failure)
                continue
            if fetch.error or fetch.html is None or fetch.final_url is None:
                result.errors.append(f"详情页抓取失败 {detail_url}: {fetch.error}")
                continue
            result.detail_pages += 1
            try:
                result.announcements.append(parse_detail(fetch.html, fetch.final_url))
            except ValueError as exc:
                result.errors.append(f"详情页解析失败 {detail_url}: {exc}")

        return result
