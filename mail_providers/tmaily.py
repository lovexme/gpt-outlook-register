#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tmaily.com 临时邮箱 Provider —— 当前唯一能完整注册 OpenAI GPT 的公共临时邮箱站。

仅需 GET /generate 创建邮箱 + GET /emails?address= 查信。

⚠️ tmaily 随机分配 8 个域名，但 OpenAI 只接受部分（白名单实测）：
   ✅ amgld.com, smaau.com, aiqseo.com, manglgih.com, 10timer.com
   ❌ watersoftenersystemcost.com（create-account 返回 unsupported_email）
   create_mailbox 会循环 generate+force 直到拿到白名单域名，确保注册成功。

用途：当作不需要绑号的临时邮箱来注册 GPT 账号。注册成功后自动保存到本地邮箱库（tempo_mail_store.db）。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .base import (
    ConfigField,
    MailProvider,
    extract_otp,
    message_is_after,
    register,
)

logger = logging.getLogger("tmaily")

# OpenAI 实测可完整注册（create-account 通过）的 tmaily 域名白名单
WHITELIST_DOMAINS = {
    "amgld.com",
    "smaau.com",
    "aiqseo.com",
    "manglgih.com",
    "10timer.com",
}


@register
class TmailyProvider(MailProvider):
    """tmaily.com provider — 循环 generate 直到命中 OpenAI 白名单域名。"""

    kind = "tmaily"
    display_name = "TMaily"
    pooled = False
    ephemeral = True
    config_fields: list[ConfigField] = []

    def __init__(self, proxy: Optional[str] = None):
        self._proxy = proxy
        self._email = ""
        self._user = ""
        self._s = None

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        """从配置创建实例。settings 的 proxy 可选。"""
        self = cls(settings.get("proxy"))
        self._ensure_session()
        return self

    def _ensure_session(self):
        import curl_cffi.requests as cr

        if self._s is None:
            self._s = cr.Session()
            if self._proxy:
                self._s.proxies = {
                    "http": self._proxy.replace("socks5://", "socks5h://"),
                    "https": self._proxy.replace("socks5://", "socks5h://"),
                }
        return self._s

    def create_mailbox(self, *args, **kwargs) -> str:
        self._ensure_session()
        last_addr = ""
        # 最多尝试 10 次，循环 generate(+force) 直到命中白名单域名
        for attempt in range(10):
            try:
                url = "https://tmaily.com/generate"
                if attempt > 0:
                    url += "?force=true"
                r = self._ensure_session().get(url, timeout=15)
                if r.status_code == 429:
                    logger.info(f"[tmaily] generate 限流(429)，等待 5s 重试")
                    time.sleep(5)
                    continue
                d = r.json()
                addr = d.get("address", "")
                if not addr:
                    logger.warning(f"[tmaily] generate 空地址，重试 (attempt={attempt})")
                    time.sleep(3)
                    continue
                last_addr = addr
                dom = addr.split("@")[-1] if "@" in addr else ""
                if dom in WHITELIST_DOMAINS:
                    self._email = addr
                    self._user = addr.split("@")[0]
                    logger.info(f"[tmaily] 邮箱: {self._email} (白名单域名 {dom})")
                    return self._email
                logger.info(f"[tmaily] {addr} 域名 {dom} 不在 OpenAI 白名单，force 换新")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[tmaily] generate err: {e}, retry (attempt={attempt})")
                time.sleep(3)
        # 兜底：全部失败就返回最后一次拿到的（哪怕非白名单，让流程报错更明确）
        raise RuntimeError(f"tmaily 10 次 generate 均未命中白名单域名，最后地址: {last_addr}")

    def wait_for_otp(self, email_addr: str, timeout: int = 150,
                      issued_after: Optional[float] = None) -> str:
        self._ensure_session()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._ensure_session().get("https://tmaily.com/emails",
                                 params={"address": email_addr}, timeout=15)
                if r.status_code != 200:
                    logger.warning(f"[tmaily] emails HTTP {r.status_code}, retry")
                    time.sleep(3)
                    continue
                data = r.json()
                msgs = (data if isinstance(data, list)
                        else data.get("emails", data.get("messages", [])))
                for m in (msgs or []):
                    if not message_is_after(m, issued_after):
                        continue
                    raw = (m.get("text") or m.get("html") or m.get("body")
                           or m.get("content") or str(m))
                    otp = extract_otp(raw)
                    if otp:
                        logger.info(f"[tmaily] ✅ OTP={otp}")
                        return otp
            except Exception as e:
                logger.warning(f"[tmaily] poll err: {e}")
            time.sleep(3)
        raise TimeoutError(f"tmaily OTP timeout {email_addr}")