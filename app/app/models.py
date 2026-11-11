"""领域数据模型。

所有时间在系统内部统一为时区感知的 ``datetime``（UTC 或带原始偏移量均可，
比较时 Python 会自动换算）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class NoticeKind(str, Enum):
    """公告类型。"""

    SUSPENSION = "suspension"          # 原始停运公告
    CORRECTION = "correction"          # 更正公告
    CANCELLATION = "cancellation"      # 撤销公告


class Announcement(BaseModel):
    """从详情页解析出的单条公告。"""

    notice_id: str = Field(..., description="公告编号，如 2026-001")
    route: str | None = Field(None, description="线路，如 12 路；更正/撤销页可能缺失")
    kind: NoticeKind
    suspend_start: datetime | None = None
    suspend_end: datetime | None = None
    published_at: datetime
    references: list[str] = Field(default_factory=list, description="更正/撤销引用的公告编号")
    url: str = Field(..., description="详情页真实来源地址（站内跳转后的最终地址）")
    title: str | None = None

    @field_validator("published_at")
    @classmethod
    def _published_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("published_at 必须带时区信息")
        return v


class QueryRequest(BaseModel):
    """查询入参。"""

    index_url: str = Field(..., description="公告列表首页 URL（同站分页的起点）")
    query_time: datetime = Field(..., description="带时区的查询时刻，ISO 8601")

    @field_validator("index_url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("index_url 必须是 http/https URL")
        return v

    @field_validator("query_time")
    @classmethod
    def _query_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("query_time 必须带时区，例如 2026-11-11T10:00:00+08:00")
        return v


class ActiveSuspension(BaseModel):
    """查询时刻仍然有效的停运区间。"""

    route: str
    suspend_start: datetime
    suspend_end: datetime
    source_chain: list[str] = Field(..., description="形成该结论的公告链编号，根公告在前")
    sources: list[str] = Field(..., description="链上各公告详情页链接")
    published_at: datetime = Field(..., description="链上最新生效公告的发布时间")


class InactiveChain(BaseModel):
    """未产生有效停运的公告链，用于解释判定过程。"""

    root_id: str
    route: str | None
    reason: str = Field(..., description="cancelled / not_yet_effective / expired / no_interval")
    chain: list[str]
    sources: list[str]
    latest_published_at: datetime | None = None


class UnmatchedReference(BaseModel):
    """指向不存在公告的引用。"""

    notice_id: str = Field(..., description="发出引用的公告编号")
    missing_reference: str
    url: str
    kind: NoticeKind


# 跳转失败原因（稳定取值，供调用方判断）
REDIRECT_CROSS_SITE = "cross_site_redirect"   # 跳转目标与输入首页不同源
REDIRECT_LOOP = "redirect_loop"               # 跳转形成循环
REDIRECT_TOO_MANY = "too_many_redirects"      # 跳转次数超过上限


class RedirectFailure(BaseModel):
    """一次被拒绝/失败的跳转。出现任意一条即说明采集不完整，本次查询无法核定。"""

    url: str = Field(..., description="发生跳转的地址（抓取到它时收到了跳转响应）")
    target: str = Field(..., description="跳转目标地址（跨站目标未发出请求）")
    reason: str = Field(
        ...,
        description=(
            f"{REDIRECT_CROSS_SITE}=跨站跳转 / {REDIRECT_LOOP}=循环跳转 / "
            f"{REDIRECT_TOO_MANY}=超过跳转上限"
        ),
    )


class QueryResponse(BaseModel):
    """查询结果。"""

    index_url: str
    query_time: datetime
    verification_status: str = Field(
        ...,
        description=(
            "verified=采集完整可核定；unverifiable=出现跨站/循环/超限跳转，"
            "采集不完整，本次查询无法核定，active_suspensions 置空"
        ),
    )
    list_pages_crawled: list[str] = Field(
        ..., description="实际抓取的列表页（站内跳转后记录最终地址）"
    )
    detail_pages_crawled: int
    announcements_parsed: int
    active_suspensions: list[ActiveSuspension]
    inactive_chains: list[InactiveChain]
    unmatched_references: list[UnmatchedReference]
    skipped_offsite_links: list[str] = Field(default_factory=list)
    redirect_failures: list[RedirectFailure] = Field(default_factory=list)
