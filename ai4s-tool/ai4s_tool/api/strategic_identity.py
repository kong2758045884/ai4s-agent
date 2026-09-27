"""Resolve private assessment ownership using the existing HttpOnly visitor cookie.

Visitor IDs in URLs, JSON or client headers are never authentication credentials.
The Java service owns token validation; private records fail closed if it is down.
"""
from __future__ import annotations

import os
from http.cookies import SimpleCookie

import httpx
from fastapi import HTTPException, Request


def problem(status: int, code: str, message: str, *, retryable: bool = False):
    raise HTTPException(status, {"code": code, "message": message,
                                 "retryable": retryable, "fieldErrors": {}})


def current_visitor(request: Request) -> str:
    name = os.environ.get("AI4S_VISITOR_COOKIE_NAME", "ai_agent_visitor_token")
    token = request.cookies.get(name)
    if not token or len(token) > 4096:
        problem(401, "VISITOR_REQUIRED", "请先打开首页建立访客会话，再读取研判记录")
    cookie = SimpleCookie()
    cookie[name] = token
    # Fixed server-side origin, not any user-supplied redirect or Host header.
    origin = os.environ.get("AI4S_VISITOR_SERVICE_URL", "http://127.0.0.1:8100").rstrip("/")
    try:
        with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
            response = client.get(origin + "/api/agent/visitor/bootstrap",
                                  headers={"Cookie": cookie.output(header="").strip()})
        if response.status_code != 200:
            problem(503, "IDENTITY_UNAVAILABLE", "访客身份服务暂时不可用，已有记录仍保留", retryable=True)
        # Invalid tokens are rotated by Java. Do not accept the newly-created visitor.
        if response.headers.get("set-cookie"):
            problem(401, "VISITOR_EXPIRED", "访客会话已失效，请返回首页重新建立会话")
        body = response.json()
        visitor = body.get("data", {}).get("visitorId")
        if body.get("code") != "0000" or not isinstance(visitor, str) or not visitor:
            problem(401, "VISITOR_INVALID", "无法确认当前访客身份")
        return visitor
    except (httpx.HTTPError, ValueError):
        problem(503, "IDENTITY_UNAVAILABLE", "访客身份服务暂时不可用，已有记录仍保留", retryable=True)
