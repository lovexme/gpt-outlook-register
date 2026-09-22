"""Tempo Mails 临时邮箱 provider（tempomails.com）。

能力：pooled=False（自己造地址，无限量）
      ephemeral=True（每次新地址 → OpenAI 始终当新号）

接口（基于实测逆向）：
  - 自定义/换邮箱:  POST https://tempomails.com/change   (需 CSRF + Session cookie)
  - 随机创建:      POST https://tempomails.com/api/emails/<app_token>
  - 收件:          GET  https://tempomails.com/api/messages/<email_token>/<邮箱>
服务端强制域名白名单 = free2access.com / usapremium.net
"""
from __future__ import annotations

import logging
import random
import re
import string
import time
from typing import Any, Optional

import requests

from .base import ConfigField, MailProvider, extract_otp, message_is_after, register

logger = logging.getLogger(__name__)

_BASE = "https://tempomails.com"
_API = f"{_BASE}/api"
_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
# 服务端白名单免费域名
_FREE_DOMAINS = ["free2access.com", "usapremium.net"]
_DEFAULT_MODE = "random"  # random=服务端随机, custom=指定 name


def _gen_local_part(rng: Optional[random.Random] = None, length: int = 8) -> str:
    r = rng or random
    return "".join(r.choices(string.ascii_lowercase + string.digits, k=length))


@register
class TempoMailProvider(MailProvider):
    """Tempo Mails 临时邮箱 provider。

    使用方式：
        mail = TempoMailProvider(mode="random", proxy="socks5h://127.0.0.1:1081")
        auth_flow.run_register(mail)
    """

    kind = "tempo"
    display_name = "Tempo 临时邮箱"
    pooled = False
    ephemeral = True

    line_segments = 0
    import_hint = ""
    import_placeholder = ""

    config_fields = [
        ConfigField(
            "tempo_mode", "创建模式",
            placeholder="random",
            help="random=服务端随机地址 / custom=表单里输入自定义前缀",
        ),
        ConfigField(
            "tempo_domain", "域名",
            placeholder="free2access.com",
            help="白名单: free2access.com / usapremium.net",
        ),
        ConfigField(
            "tempo_app_token", "App Token（收件必需）",
            type="password",
            placeholder="从 tempo 页面/抓包获取",
            help="收件接口 GET /api/messages/<app_token>/<邮箱> 用这个 token，不填收不到信",
        ),
    ]

    def __init__(
        self,
        mode: str = "random",
        domain: str = "",
        proxy: Optional[str] = None,
        app_token: str = "",
    ):
        self._mode = (mode or "random").strip().lower()
        self._domain = (domain or "").strip() or _FREE_DOMAINS[0]
        self._app_token = app_token
        self._session = requests.Session()
        if proxy:
            norm = proxy
            if norm.startswith("socks5://"):
                norm = "socks5h://" + norm[len("socks5://"):]
            self._session.proxies = {"https": norm, "http": norm}
        self._session.headers["User-Agent"] = _USER_AGENT
        self._csrf = ""
        self._email_token = ""

    # ── 会话 ─────────────────────────────────────────────
    def _ensure_session(self) -> None:
        """打开首页建立会话 + 拿 CSRF token。"""
        if self._csrf:
            return
        try:
            r = self._session.get(_BASE, timeout=25)
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
            if m:
                self._csrf = m.group(1)
        except Exception as e:
            logger.warning(f"[tempo] 建立会话失败: {e}")
        # 尝试从首页提取 app_token（随机模式用）
        if self._app_token:
            return
        try:
            for key in ("app_token", "APP_TOKEN", "device_token"):
                mm = re.search(
                    key + r'["\']?\s*[=:]\s*["\']([^"\']{20,})["\']', r.text
                )
                if mm:
                    self._app_token = mm.group(1)
                    break
        except Exception:
            pass

    # ── 创建 ─────────────────────────────────────────────
    def create_mailbox(self) -> str:
        """返回本次注册要用的邮箱地址。"""
        self._ensure_session()
        if self._mode == "custom" and self._csrf:
            email = self._custom_create()
        else:
            email = self._random_create()
        logger.info(f"[tempo] 创建邮箱: {email}")
        return email

    def _custom_create(self, name: str = "") -> str:
        """自定义创建: 走 POST /change。"""
        name = (name or _gen_local_part()).strip()
        domain = self._domain or _FREE_DOMAINS[0]
        payload = {"_token": self._csrf, "name": name, "domain": domain}
        r = self._session.post(
            f"{_BASE}/change", json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=25,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"TempoMail custom_create 失败: {r.status_code} {r.text[:200]}"
            )
        data = r.json()
        mailbox = data.get("mailbox", "")
        self._email_token = data.get("email_token", "")
        if not mailbox:
            raise RuntimeError(f"TempoMail 响应缺 mailbox: {data}")
        return mailbox

    def _random_create(self) -> str:
        """随机创建: 优先 /api/emails/<app_token>。"""
        if self._app_token:
            try:
                r = self._session.post(
                    f"{_API}/emails/{self._app_token}",
                    json={},
                    timeout=25,
                )
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    email = data.get("email", "")
                    self._email_token = data.get("email_token", "")
                    if email:
                        return email
            except Exception as e:
                logger.warning(f"[tempo] 随机 API 创建失败，回退 /change: {e}")
        # 回退: /change + 随机前缀
        self._ensure_session()
        if not self._csrf:
            raise RuntimeError("TempoMail 无法获取 CSRF，且无 app_token")
        return self._custom_create(name=_gen_local_part())

    # ── 收件 ─────────────────────────────────────────────
    def wait_for_otp(
        self,
        email_addr: str,
        timeout: int = 120,
        issued_after: Optional[float] = None,
    ) -> str:
        """轮询收件直到拿到 OTP，超时抛 TimeoutError。"""
        timeout = max(int(timeout), 60)
        deadline = time.time() + timeout
        logger.info(f"[tempo] 等待 OTP -> {email_addr} (timeout={timeout}s)")

        seen_ids = set()
        while time.time() < deadline:
            try:
                msgs = self._fetch_messages(email_addr)
                for m in msgs:
                    if not message_is_after(m, issued_after):
                        continue
                    mid = str(m.get("id", ""))
                    if mid and mid in seen_ids:
                        continue
                    if mid:
                        seen_ids.add(mid)
                    raw = self._msg_raw(m)
                    otp = extract_otp(raw)
                    if otp:
                        logger.info(f"[tempo] ✅ OTP={otp} from mail id={mid}")
                        return otp
            except Exception as e:
                logger.warning(f"[tempo] poll 异常: {e}")
            time.sleep(3)
        raise TimeoutError(f"TempoMail OTP timeout {timeout}s for {email_addr}")

    def _fetch_messages(self, email_addr: str) -> list:
        token = self._app_token or "__probe__"
        try:
            r = self._session.get(
                f"{_API}/messages/{token}/{email_addr}",
                timeout=25,
            )
            if r.status_code != 200:
                logger.debug(f"[tempo] /messages 返回 {r.status_code}")
                return []
            data = r.json()
            return data.get("messages", []) or []
        except Exception as e:
            logger.warning(f"[tempo] 拉取邮件失败: {e}")
            return []

    @staticmethod
    def _msg_raw(m: dict) -> str:
        """拼一份可过 extract_otp 的原文。"""
        body = m.get("body") or m.get("text") or m.get("content") or ""
        subject = m.get("subject") or ""
        from_addr = m.get("from_email") or m.get("from") or ""
        return f"From: {from_addr}\nSubject: {subject}\n\n{body}"

    # ── 构造 ─────────────────────────────────────────────
    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None) -> "TempoMailProvider":
        return cls(
            mode=(settings.get("tempo_mode") or "random").strip(),
            domain=((settings.get("tempo_domain") or "").strip()),
            proxy=(settings.get("proxy") or "").strip(),
            app_token=(settings.get("tempo_app_token") or "").strip(),
        )

    def self_test(self) -> dict:
        try:
            self._ensure_session()
            if self._csrf:
                return {"ok": True, "message": "会话建立成功，CSRF 已获取"}
            return {"ok": False, "message": "会话建立失败（拿不到 CSRF）"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:200]}