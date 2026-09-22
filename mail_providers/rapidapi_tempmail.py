"""RapidAPI Temp-Mail 临时邮箱 provider（即用即查，无需创建邮箱）。

API: https://privatix-temp-mail-v1.p.rapidapi.com
  - GET /request/domains/ → 域名列表
  - GET /request/mail/id/{md5}/ → 按邮箱 md5 查收件
  - GET /request/source/id/{mail_id}/ → 原始邮件源码

能力：pooled=False（自己造地址，无限量）
      ephemeral=True（每次新地址）
"""
from __future__ import annotations

import hashlib
import logging
import random
import string
import time
from typing import Optional

from .base import ConfigField, MailProvider, extract_otp, message_is_after, register

logger = logging.getLogger(__name__)

_API_BASE = "https://privatix-temp-mail-v1.p.rapidapi.com"
_DEFAULT_DOMAIN = "@nuclene.com"
_RAPIDAPI_KEY = "2fcde3641emshb2943d2bce5bd57p156bcbjsnce26cd4b4712"

_HEADERS = {
    "x-rapidapi-host": "privatix-temp-mail-v1.p.rapidapi.com",
    "x-rapidapi-key": _RAPIDAPI_KEY,
}


@register
class RapidApiTempMailProvider(MailProvider):
    """RapidAPI Temp-Mail 临时邮箱 provider（即用即查）。"""

    kind = "rapidapi_tempmail"
    display_name = "RapidAPI 临时邮箱"
    pooled = False
    ephemeral = True

    line_segments = 0
    import_hint = ""
    import_placeholder = ""

    config_fields = [
        ConfigField(
            "rapidapi_domain", "邮箱域名",
            placeholder=_DEFAULT_DOMAIN,
            help="RapidAPI 域名，含 @ 前缀，默认 @nuclene.com",
        ),
    ]

    def __init__(self, domain: str = _DEFAULT_DOMAIN, proxy: Optional[str] = None):
        self._domain = domain.strip()
        self._email = ""
        self._proxy = proxy

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        domain = (settings.get("rapidapi_domain") or _DEFAULT_DOMAIN).strip()
        proxy = settings.get("proxy") or ""
        return cls(domain=domain, proxy=proxy or None)

    def create_mailbox(self) -> str:
        """生成随机邮箱地址（即用即查，无需调 API 创建）。"""
        prefix = "gpt" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        self._email = f"{prefix}{self._domain}"
        logger.info(f"[rapidapi_tempmail] 邮箱: {self._email}")
        return self._email

    def wait_for_otp(
        self, email_addr: str, timeout: int = 120, issued_after: Optional[float] = None,
    ) -> str:
        """轮询 RapidAPI 等待 OTP。"""
        timeout = max(int(timeout), 60)
        deadline = time.time() + timeout
        md5 = hashlib.md5(email_addr.encode()).hexdigest()
        poll_interval = 3  # 每 3 秒轮询一次

        logger.info(f"[rapidapi_tempmail] 等待 OTP -> {email_addr} (timeout={timeout}s)")
        proxy = self._proxy
        if proxy and proxy.startswith("socks5://"):
            proxy = "socks5h://" + proxy[len("socks5://"):]
        proxies = {"http": proxy, "https": proxy} if proxy else None

        while time.time() < deadline:
            try:
                import json
                from curl_cffi import requests

                r = requests.get(
                    f"{_API_BASE}/request/mail/id/{md5}/",
                    headers=_HEADERS,
                    proxies=proxies,
                    timeout=15,
                )
                if r.status_code != 200:
                    logger.warning(f"[rapidapi_tempmail] GET /mail 非 200: {r.status_code}")
                    time.sleep(poll_interval)
                    continue

                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    # "There are no emails yet" = 没邮件，继续等
                    if "no emails" in str(data.get("error", "")).lower():
                        time.sleep(poll_interval)
                        continue
                    logger.warning(f"[rapidapi_tempmail] API 返回错误: {data}")
                    time.sleep(poll_interval)
                    continue

                # 邮件列表
                if isinstance(data, list):
                    for mail in data:
                        mail_id = mail.get("mail_id", "")
                        if not mail_id:
                            continue

                        # 获取原始邮件源码
                        r2 = requests.get(
                            f"{_API_BASE}/request/source/id/{mail_id}/",
                            headers=_HEADERS,
                            proxies=proxies,
                            timeout=15,
                        )
                        if r2.status_code != 200:
                            continue

                        if not message_is_after(mail, issued_after):
                            continue
                        raw = r2.text
                        otp = extract_otp(raw)
                        if otp:
                            logger.info(f"[rapidapi_tempmail] ✅ OTP={otp} from mail_id={mail_id}")
                            return otp
                        logger.debug(f"[rapidapi_tempmail] mail_id={mail_id} 未匹配到 OTP")

                time.sleep(poll_interval)

            except Exception as e:
                logger.warning(f"[rapidapi_tempmail] poll 异常: {e}")
                time.sleep(poll_interval)

        raise TimeoutError(f"RapidAPI Temp-Mail OTP timeout {timeout}s for {email_addr}")