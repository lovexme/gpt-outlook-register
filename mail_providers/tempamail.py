"""Temp Mail (tempamail.com) 临时邮箱 provider。

API: https://api.tempamail.com/android
  - POST /email/random    → 创建随机邮箱
  - POST /email/custom    → 自定义邮箱名（支持 domain_id）
  - POST /messages        → 查收件箱（uuid + email_id）
  - POST /email/delete    → 删除邮箱
  - POST /domains         → 列出域名

认证：固定 client uuid（全局凭证，每个邮箱的 email_id 用于查收件箱）。

能力：pooled=False（自己造地址，无限量）
      ephemeral=True（每次新地址）
"""
from __future__ import annotations

import json
import logging
import random as _random
import re
import string as _string
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .base import ConfigField, MailProvider, extract_otp, message_is_after, register

logger = logging.getLogger(__name__)

_API_BASE = "https://api.tempamail.com/android"

# 域名缓存（类级别，避免每次创建都调 domains API）
_domain_cache: list[dict] = []
_domain_cache_ts: float = 0


def _api(path: str, payload: dict, timeout: int = 15) -> dict:
    """POST JSON 到指定端点，返回解析后的 dict。"""
    url = f"{_API_BASE}/{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"http_{e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _fetch_domains(uuid: str) -> list[dict]:
    """获取所有可用域名，缓存 5 分钟。"""
    global _domain_cache, _domain_cache_ts
    if time.time() - _domain_cache_ts < 300:
        return _domain_cache
    r = _api("domains", {"uuid": uuid})
    doms = r.get("domains") or []
    _domain_cache = doms
    _domain_cache_ts = time.time()
    return doms


def _parse_domain_spec(spec: str) -> list[int]:
    """解析域名配置：空/0=随机，逗号分隔=指定列表，单个数字=固定。"""
    spec = (spec or "").strip()
    if not spec or spec == "0":
        return []  # 随机
    parts = [p.strip() for p in spec.split(",") if p.strip().isdigit()]
    return [int(p) for p in parts] if parts else []


def _random_domain_id(uuid: str) -> int:
    """从所有可用域名中随机选一个 ID。"""
    doms = _fetch_domains(uuid)
    if not doms:
        return 0
    return _random.choice(doms)["id"]


@register
class TempamailProvider(MailProvider):
    """Temp Mail (tempamail.com) 临时邮箱 provider。"""

    kind = "tempamail"
    display_name = "Temp Mail2"
    pooled = False
    ephemeral = True

    line_segments = 0
    import_hint = ""
    import_placeholder = ""

    config_fields = [
        ConfigField(
            "tempamail_uuid", "Client UUID",
            type="password",
            placeholder="c3c40144-a00c-43f5-9bb0-fb2f92bac1c4",
            help="App 的 client uuid（固定凭证），默认已内置一个可用 uuid",
        ),
        ConfigField(
            "tempamail_domains", "域名 ID",
            placeholder="17 / 1,5,17 / 留空=随机",
            help="留空或0=随机域名；1,5,17=轮询指定域名；单个数字=固定域名。ID: 17=ogzmail, 5=ozvmail, 4=koletter, 3=mailfrs, 2=opemails, 1=uiemail",
        ),
    ]

    def __init__(self, uuid: str = "", domain_spec: str = "", proxy: Optional[str] = None):
        self._uuid = (uuid or self._default_uuid()).strip()
        self._domain_ids = _parse_domain_spec(domain_spec)
        self._domain_spec = domain_spec
        self._proxy = proxy
        self._email_id: str = ""
        self._email_token: str = ""   # 与 registrar 保存逻辑对齐：存 email_id
        self._email_addr: str = ""
        self._call_count: int = 0     # 轮询计数器

    @staticmethod
    def _default_uuid() -> str:
        return "c3c40144-a00c-43f5-9bb0-fb2f92bac1c4"

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        uuid = (settings.get("tempamail_uuid") or "").strip() or cls._default_uuid()
        domain_spec = (settings.get("tempamail_domains") or "").strip()
        return cls(uuid=uuid, domain_spec=domain_spec)

    def _pick_domain_id(self) -> int:
        """根据配置选域名 ID。"""
        if not self._domain_ids:
            # 空列表 → 随机
            return _random_domain_id(self._uuid)
        # 轮询
        idx = self._call_count % len(self._domain_ids)
        self._call_count += 1
        return self._domain_ids[idx]

    def create_mailbox(self) -> str:
        """创建邮箱，按域名配置选择域名。"""
        did = self._pick_domain_id()
        import random
        import string as _str
        username = "".join(random.choices(_str.ascii_lowercase, k=8))
        r = _api("email/custom", {
            "uuid": self._uuid,
            "username": username,
            "domain_id": did,
        })
        em = r.get("email")
        if not em:
            err = r.get("error") or str(r)[:200]
            raise RuntimeError(f"TempMail2 创建失败: {err}")
        self._email_addr = em.get("address", "")
        self._email_id = str(em.get("id", ""))
        self._email_token = self._email_id  # registrar 保存用
        logger.info(f"[tempamail] 创建邮箱: {self._email_addr} id={self._email_id}")
        return self._email_addr

    def wait_for_otp(
        self,
        email_addr: str,
        timeout: int = 120,
        issued_after: Optional[float] = None,
    ) -> str:
        """轮询收件箱直到拿到 OTP，超时抛 TimeoutError。"""
        timeout = max(int(timeout), 60)
        deadline = time.time() + timeout
        logger.info(f"[tempamail] 等待 OTP -> {email_addr} (timeout={timeout}s)")

        seen_ids = set()
        while time.time() < deadline:
            try:
                msgs = self._fetch_messages()
                for m in msgs:
                    mid = str(m.get("id", ""))
                    if mid and mid in seen_ids:
                        continue
                    if mid:
                        seen_ids.add(mid)
                    if not message_is_after(m, issued_after):
                        continue
                    raw = self._msg_raw(m)
                    otp = extract_otp(raw)
                    if otp:
                        logger.info(f"[tempamail] ✅ OTP={otp} from mail id={mid}")
                        return otp
            except Exception as e:
                logger.warning(f"[tempamail] poll 异常: {e}")
            time.sleep(3)
        raise TimeoutError(f"TempMail2 OTP timeout {timeout}s for {email_addr}")

    def _fetch_messages(self) -> list:
        if not self._email_id:
            return []
        r = _api("messages", {"uuid": self._uuid, "email_id": int(self._email_id)})
        return r.get("messages", []) or []

    @staticmethod
    def _parse_ts(ts: str) -> float:
        """解析 tempamail 时间戳，格式如 '2026-08-25T12:00:00+00:00'。"""
        from datetime import datetime
        clean = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean).timestamp()
        except Exception:
            return 0

    @staticmethod
    def _msg_raw(m: dict) -> str:
        body = m.get("body") or m.get("text") or m.get("html") or ""
        subject = m.get("subject") or ""
        from_addr = m.get("from_address") or m.get("from") or ""
        import re as _re
        text = _re.sub(r"<[^>]+>", " ", str(body))
        text = " ".join(text.split())
        return f"From: {from_addr}\nSubject: {subject}\n\n{text}"

    def self_test(self) -> dict:
        """测试连通性：尝试创建和删除一个邮箱。"""
        try:
            did = self._pick_domain_id()
            username = "".join(_random.choices(_string.ascii_lowercase, k=8))
            r = _api("email/custom", {"uuid": self._uuid, "username": username, "domain_id": did})
            em = r.get("email")
            if not em:
                return {"ok": False, "message": f"测试失败: {r.get('error') or str(r)[:200]}"}
            addr = em.get("address", "")
            eid = em.get("id")
            if eid:
                _api("email/delete", {"uuid": self._uuid, "email_id": int(eid)})
            return {"ok": True, "message": f"✅ 连通正常，测试邮箱 {addr} 已自动删除"}
        except Exception as e:
            return {"ok": False, "message": f"测试异常: {str(e)[:200]}"}