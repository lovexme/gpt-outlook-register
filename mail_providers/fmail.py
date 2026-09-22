#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fmail 集群临时邮箱 Provider —— 第二个能完整注册 OpenAI GPT 的公共临时邮箱站。

API（简单无鉴权）:
- 客户端自选 user@domain，无需创建端点
- 查信: GET /api/inbox/{user}?domain={host}  → 邮件列表(元数据)
- 正文: GET /api/email/{token}              → 完整邮件(含 OTP)

⚠️ 域名可用性（OpenAI 白名单，2026-08-26 实测）:
  ✅ emailxo.pro（完整注册成功）
  ⏳ 其余域名待逐个验证（emailawb.pro/emailfoxi.pro/emailvb.pro/mail-temp.pro 等）
  create_mailbox 只从 WHITELIST_DOMAINS 中挑，确保命中可用域名。
"""
from __future__ import annotations

import logging
import random
import string
import time
from typing import Optional

from .base import (
    ConfigField,
    MailProvider,
    extract_otp,
    message_is_after,
    register,
)

logger = logging.getLogger("fmail")

# OpenAI 实测可完整注册的 fmail 域名（白名单，8 个）：
#   ✅ emailxo.pro, mail-temp.pro, emailab.xyz, mail-temp.shop, canicasbrawl.com,
#      deislerlive.com, exolinker.com, gootsijs.com（全部完整注册成功）
#   ❌ 拒发：emailawb.pro, emailvb.pro, mailmaxy.one
#   ⏳ 未测：ougoods.com, uncmail.org
WHITELIST_DOMAINS = [
    "emailxo.pro",
    "mail-temp.pro",
    "emailab.xyz",
    "mail-temp.shop",
    "canicasbrawl.com",
    "deislerlive.com",
    "exolinker.com",
    "gootsijs.com",
]


@register
class FMailProvider(MailProvider):
    """fmail 集群 provider — 自选 user@domain，查收件箱按 token 拉正文。"""

    kind = "fmail"
    display_name = "FMail"
    pooled = False
    ephemeral = True
    config_fields: list[ConfigField] = []

    def __init__(self, proxy: Optional[str] = None):
        self._proxy = proxy
        self._email = ""
        self._user = ""
        self._host = ""
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
        self._host = random.choice(WHITELIST_DOMAINS)
        self._user = "u" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        self._email = f"{self._user}@{self._host}"
        logger.info(f"[fmail] 邮箱: {self._email}")
        return self._email

    def wait_for_otp(self, email_addr: str, timeout: int = 150,
                      issued_after: Optional[float] = None) -> str:
        self._ensure_session()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._ensure_session().get(
                    f"https://{self._host}/api/inbox/{self._user}?domain={self._host}",
                    timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    for m in (d.get("emails") or []):
                        if not message_is_after(m, issued_after):
                            continue
                        token = m.get("token", "")
                        if not token:
                            continue
                        r2 = self._ensure_session().get(
                            f"https://{self._host}/api/email/{token}", timeout=15)
                        if r2.status_code == 200:
                            m2 = r2.json()
                            raw = (m2.get("body_html") or m2.get("body_text")
                                   or m2.get("subject") or str(m2))
                            otp = extract_otp(raw)
                            if otp:
                                logger.info(f"[fmail] ✅ OTP={otp}")
                                return otp
            except Exception as e:
                logger.warning(f"[fmail] poll err: {e}")
            time.sleep(3)
        raise TimeoutError(f"fmail OTP timeout {email_addr}")