"""反代 Temp-Mail provider：创建强制走 VM 反代（住宅 IP 防 429），收信 hy2。

与原生 tempmail 源的区别：
- tempmail_proxy 优先走反代（tempmail_create_proxy_base），反代挂/异常自动回退原生池创建，
  不会因为反代单点故障导致整批注册失败。
- 面板邮箱来源选 tempmail_proxy 即可获得"住宅 IP 创建 + 自动降级"。
"""
from __future__ import annotations

import logging

from .base import register
from .tempmail import TempMailProvider

logger = logging.getLogger(__name__)


@register
class TempMailProxyProvider(TempMailProvider):
    """反代 Temp-Mail：创建走 VM 反代，反代异常自动回退原生池。"""

    kind = "tempmail_proxy"
    display_name = "反代 Temp-Mail"
    pooled = False
    ephemeral = True

    line_segments = 0
    import_hint = ""
    import_placeholder = ""

    def _create_once(self) -> str:
        """优先反代创建；反代配置缺失或异常时回退原生池创建。"""
        try:
            from webui import db
            proxy_base = (db.get_setting("tempmail_create_proxy_base", "") or "").strip()
        except Exception:
            proxy_base = ""
        if proxy_base:
            try:
                return self._create_via_proxy(proxy_base)
            except Exception as e:
                logger.warning(f"[tempmail_proxy] 反代创建失败，回退原生池: {e}")
        return self._create_via_pool()