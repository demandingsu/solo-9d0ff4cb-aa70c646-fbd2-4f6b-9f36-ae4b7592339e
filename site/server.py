#!/usr/bin/env python3
"""本地示例公告站：静态文件 + 跳转规则。

仅用标准库实现，可完全离线运行。跳转规则来自 build_site.py 生成的
``redirects.json``（与 html/ 同级），用于演示：

* 站内迁移：旧地址 301 至站内新地址（采集器应跟随并记录最终地址）；
* 跨站跳转：同站地址 302 至外站（采集器不得请求目标，本次查询无法核定）；
* 循环跳转 / 超长跳转链（采集器应拒绝并给出原因）。

用法: python server.py [--bind 127.0.0.1] [--port 8080] [--directory html]
"""
from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent


def load_redirects(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {rule["path"]: rule for rule in data.get("rules", [])}


class RedirectingHandler(SimpleHTTPRequestHandler):
    """先查跳转规则表，命中则返回 3xx，否则按静态文件处理。"""

    redirects: dict[str, dict] = {}

    def send_head(self):  # noqa: D102
        rule = self.redirects.get(urlparse(self.path).path)
        if rule is not None:
            self.send_response(rule["status"])
            self.send_header("Location", rule["location"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return super().send_head()


def main() -> None:
    parser = argparse.ArgumentParser(description="示例公告站（静态文件 + 跳转规则）")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--directory", default=str(BASE_DIR / "html"))
    parser.add_argument("--redirects", default=str(BASE_DIR / "redirects.json"))
    args = parser.parse_args()

    RedirectingHandler.redirects = load_redirects(Path(args.redirects))
    handler = partial(RedirectingHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(
        f"示例公告站: http://{args.bind}:{args.port}/ "
        f"（目录 {args.directory}，跳转规则 {len(RedirectingHandler.redirects)} 条）"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
