"""端到端：FastAPI + 爬虫 + 引擎，对本地示例公告站做完整查询。

前置：示例站点已在 127.0.0.1:8080 提供服务（见 README / demo 脚本，
需用 site/server.py 启动以获得跳转规则）。
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SITE = os.environ.get("TEST_SITE_URL", "http://127.0.0.1:8080/index.html")
SITE_BASE = SITE.rsplit("/", 1)[0]  # 用于拼接站内其他入口
Q = "2026-11-11T10:00:00+08:00"

pytestmark = pytest.mark.asyncio


async def _post(index_url: str, query_time: str = Q):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # lifespan 由 LifespanManager 之外的方式不保证执行，手动触发
        async with app.router.lifespan_context(app):
            return await ac.post(
                "/query", json={"index_url": index_url, "query_time": query_time}
            )


async def test_full_query_workflow():
    r = await _post(SITE)
    assert r.status_code == 200, r.text
    data = r.json()

    # 采集完整，无跳转失败，可核定
    assert data["verification_status"] == "verified"
    assert data["redirect_failures"] == []

    # 三个列表页全部爬到，14 条同站详情（跨站链接 1 条被跳过；
    # N-2026-014 经站内 301 迁移，跟随并计入）
    assert len(data["list_pages_crawled"]) == 3
    assert data["detail_pages_crawled"] == 14
    assert data["announcements_parsed"] == 14
    assert len(data["skipped_offsite_links"]) == 1

    active = data["active_suspensions"]
    chains = {tuple(a["source_chain"]) for a in active}
    # 有效链：001 原始；005 原始（012 次日更正不可见）；
    # 003→006→010 两次更正；009→011 更正（带悬空引用）；014 站内迁移公告
    assert ("N-2026-001",) in chains
    assert ("N-2026-005",) in chains
    assert ("N-2026-003", "N-2026-006", "N-2026-010") in chains
    assert ("N-2026-009", "N-2026-011") in chains
    assert ("N-2026-014",) in chains

    by_route = {a["route"]: a for a in active}
    # 两次更正后的最终区间
    assert by_route["25 路"]["suspend_end"].startswith("2026-11-20T22:00:00")
    # 同线路两条独立公告分别判断：12 路有两条有效停运
    routes_12 = [a for a in active if a["route"] == "12 路"]
    assert len(routes_12) == 2

    reasons = {(c["root_id"], c["reason"]) for c in data["inactive_chains"]}
    assert ("N-2026-002", "expired") in reasons
    assert ("N-2026-004", "cancelled") in reasons
    assert ("N-2026-008", "not_yet_effective") in reasons
    assert ("N-2026-013", "not_yet_effective") in reasons

    unmatched = data["unmatched_references"]
    assert len(unmatched) == 1
    assert unmatched[0]["notice_id"] == "N-2026-011"
    assert unmatched[0]["missing_reference"] == "N-2026-999"

    # 来源链接完整；迁移公告记录跳转后的最终真实地址
    chain25 = next(a for a in active if a["route"] == "25 路")
    assert all(u.startswith(f"{SITE_BASE}/notices/") for u in chain25["sources"])
    chain8 = next(a for a in active if a["route"] == "8 路")
    assert chain8["sources"] == [f"{SITE_BASE}/archive/notices/N-2026-014.html"]


async def test_insite_migration_entry_resolves_relative_pager_by_final_url():
    """公告栏目站内迁移：旧入口 301 至 /bulletin/，相对分页按最终地址解析。"""
    r = await _post(f"{SITE_BASE}/old/index.html")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["verification_status"] == "verified"
    assert data["redirect_failures"] == []
    # 列表页记录最终地址；相对分页 page2/page3 解析到 /bulletin/ 下
    assert data["list_pages_crawled"] == [
        f"{SITE_BASE}/bulletin/index.html",
        f"{SITE_BASE}/bulletin/page2.html",
        f"{SITE_BASE}/bulletin/page3.html",
    ]
    # 与主入口采集到同样的公告集合（详情仍在原地址，014 经迁移记录最终地址）
    assert data["announcements_parsed"] == 14
    chains = {tuple(a["source_chain"]) for a in data["active_suspensions"]}
    assert ("N-2026-001",) in chains
    assert ("N-2026-014",) in chains
    chain8 = next(
        a for a in data["active_suspensions"] if a["route"] == "8 路"
    )
    assert chain8["sources"] == [f"{SITE_BASE}/archive/notices/N-2026-014.html"]


async def test_redirect_failures_mark_query_unverifiable():
    """跨站/循环/超限跳转：列出失败地址与原因，本次查询无法核定。"""
    r = await _post(f"{SITE_BASE}/alerts/index.html")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["verification_status"] == "unverifiable"
    # 不得输出确定的有效停运结论（015 本可判定为有效，但被抑制）
    assert data["active_suspensions"] == []
    assert data["announcements_parsed"] == 1  # 只有 015 成功解析

    failures = {(f["url"], f["target"], f["reason"]) for f in data["redirect_failures"]}
    assert (
        f"{SITE_BASE}/notices/N-EXT-002.html",
        "http://external.example.org/notice/N-EXT-002.html",
        "cross_site_redirect",
    ) in failures
    assert (
        f"{SITE_BASE}/loop-b.html",
        f"{SITE_BASE}/loop-a.html",
        "redirect_loop",
    ) in failures
    assert any(reason == "too_many_redirects" for _, _, reason in failures)


async def test_naive_query_time_rejected():
    r = await _post(SITE, "2026-11-11T10:00:00")
    assert r.status_code == 422


async def test_bad_index_url_returns_502():
    r = await _post("http://127.0.0.1:9/never.html")
    assert r.status_code == 502
