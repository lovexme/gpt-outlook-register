"""代理池自动健康检查模块。

功能：
  1. 定时（每5分钟）自动检测后端记录的所有代理
  2. 结果持久化到 settings 表的 proxy_health key
  3. 连续失败 3 次自动标记为不可用并从轮换中移除
  4. 前端通过 GET /api/proxy/health 查询状态

设计：
  - 独立线程运行，不阻塞 FastAPI event loop
  - 复用 /api/proxy/test 的测试逻辑（create_http_session）
  - 每次检查只测当前池，和被标记移除的代理互不干扰
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("proxy_health")

# 单轮失败立即从生效池禁用；下一轮仍从配置池复查，成功则恢复。
MAX_CONSECUTIVE_FAILS = 1
# 检查间隔秒数
CHECK_INTERVAL = 1800  # 30 分钟

# 全局单例控制
_health_checker: ProxyHealthChecker | None = None
_start_lock = threading.Lock()


class ProxyHealthChecker:
    """代理健康检查器。

    用法：
        checker = ProxyHealthChecker()
        checker.start()          # 启动后台线程（已在 app.py 启动时调用）
        checker.check_now()      # 手动触发一次
        checker.stop()           # 停止
        checker.get_status()     # 当前状态快照
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_check_at: float = 0.0
        self._last_duration: float = 0.0
        self._in_progress = False

    def start(self) -> None:
        """启动后台守护线程。"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="proxy-health-checker"
            )
            self._thread.start()
            logger.info("代理健康检查线程已启动（间隔 %d 秒）", CHECK_INTERVAL)

    def stop(self) -> None:
        """停止后台线程。"""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            logger.info("代理健康检查线程已停止")

    def check_now(self) -> dict:
        """手动触发一次健康检查，返回结果摘要。"""
        return self._run_check()

    def get_status(self) -> dict:
        """返回当前状态快照（不含完整代理详情，详情走 DB）。"""
        with self._lock:
            return {
                "running": self._running,
                "in_progress": self._in_progress,
                "last_check_at": self._last_check_at,
                "last_duration": self._last_duration,
            }

    # ──────────────── 内部 ────────────────

    def _loop(self) -> None:
        """后台循环，每 CHECK_INTERVAL 秒跑一次。"""
        # 启动后先等 10 秒再跑第一次，避免和服务启动同时抢资源
        # 实际 10s 后立即开跑（_stop_event 不设超时等待）
        # 用 wait 替代 sleep 以便 stop 能及时打断
        if self._stop_event.wait(timeout=10):
            return
        while not self._stop_event.is_set():
            try:
                self._run_check()
            except Exception as e:
                logger.exception("健康检查异常: %s", e)
            # 等待下一轮，但可被 stop 打断
            if self._stop_event.wait(timeout=CHECK_INTERVAL):
                break

    def _run_check(self) -> dict:
        """执行一轮健康检查。"""
        # 防止重入（手动触发 + 定时触发同时跑）
        with self._lock:
            if self._in_progress:
                return {"ok": False, "error": "上一轮检查尚未完成"}
            self._in_progress = True

        t0 = time.perf_counter()
        try:
            # 从 DB 读完整配置池：当前被禁用的代理和新添加代理都必须继续检查
            from . import db as _db

            config_pool = _db.get_proxy_config_pool()
            active_pool = _db.get_proxy_pool()

            # 读上次健康状态，拿连续失败计数 + 历史移除的代理
            prev_health = _db.get_proxy_health()
            prev_proxies = prev_health.get("proxies", {})
            removed_proxies = prev_health.get("removed", []) or []

            candidates = list(config_pool)

            if not candidates:
                with self._lock:
                    self._last_check_at = time.time()
                    self._last_duration = time.perf_counter() - t0
                _db.set_proxy_health({
                    "proxies": {},
                    "last_check_at": time.time(),
                    "healthy": 0,
                    "unhealthy": 0,
                    "removed": [],
                })
                return {"ok": True, "total": 0, "healthy": 0, "unhealthy": 0}

            # 并发测试所有候选
            results = _test_all_proxies(candidates)

            # 记录新状态
            new_proxies: dict[str, dict] = {}
            new_removed: list[str] = []
            restored: list[str] = []
            healthy_count = 0
            unhealthy_count = 0

            for proxy, res in results.items():
                is_ok = res.get("ok", False)
                prev_data = prev_proxies.get(proxy, {})
                prev_consecutive = prev_data.get("consecutive_fails", 0)

                entry = {
                    "ok": is_ok,
                    "latency_ms": res.get("latency_ms", 0),
                    "ip": res.get("ip", ""),
                    "error": res.get("error", "") if not is_ok else "",
                    "checked_at": time.time(),
                }

                if is_ok:
                    entry["consecutive_fails"] = 0
                    healthy_count += 1
                    # 之前被移除的代理恢复了 → 记录待恢复
                    if proxy in removed_proxies:
                        restored.append(proxy)
                else:
                    entry["consecutive_fails"] = prev_consecutive + 1
                    if entry["consecutive_fails"] >= MAX_CONSECUTIVE_FAILS:
                        entry["removed"] = True
                        new_removed.append(proxy)
                        logger.warning(
                            "代理 %s 本轮失败，已临时禁用；下轮继续复查",
                            proxy,
                        )
                        # 仍保留失败状态，防止前端同步时误重新启用；配置池不删除。
                    unhealthy_count += 1

                new_proxies[proxy] = entry

            # 生效池只保留本轮健康代理；失败代理仅临时禁用，仍留在 config_pool 中复查。
            new_pool = [p for p in config_pool if p in new_proxies and new_proxies[p].get("ok")]
            for r in restored:
                logger.info("代理 %s 已恢复，重新加入代理池", r)

            # 更新池
            if new_pool != active_pool:
                _db.set_active_proxy_pool(new_pool)
                if restored:
                    logger.info("已恢复 %d 个代理，当前池大小 %d", len(restored), len(new_pool))
                if new_removed:
                    logger.info("已移除 %d 个代理，当前池大小 %d", len(new_removed), len(new_pool))

            # 持久化健康状态
            health_data = {
                "proxies": new_proxies,
                "last_check_at": time.time(),
                "healthy": healthy_count,
                "unhealthy": unhealthy_count,
                "removed": new_removed,
                "removed_count": len(new_removed),
                "restored": restored,
                "total": len(candidates),
            }
            _db.set_proxy_health(health_data)

            with self._lock:
                self._last_check_at = time.time()
                self._last_duration = time.perf_counter() - t0

            logger.info(
                "健康检查完成: %d 正常 / %d 异常 / %d 移除 / %d 恢复（耗时 %.1fs）",
                healthy_count, unhealthy_count, len(new_removed), len(restored),
                self._last_duration,
            )
            return {
                "ok": True,
                "total": len(candidates),
                "healthy": healthy_count,
                "unhealthy": unhealthy_count,
                "removed": new_removed,
                "restored": restored,
                "duration": self._last_duration,
            }
        finally:
            with self._lock:
                self._in_progress = False


def _test_all_proxies(proxies: list[str], timeout: int = 8) -> dict[str, dict]:
    """并发测试所有代理，返回 { proxy: { ok, latency_ms, ip, error } }。

    复用 /api/proxy/test 的测试逻辑。
    """
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from http_client import create_http_session

    test_url = "https://api.ipify.org?format=json"
    from concurrent.futures import ThreadPoolExecutor

    def _test_one(proxy: str) -> dict:
        t0 = time.perf_counter()
        try:
            sess = create_http_session(proxy=proxy)
            resp = sess.get(test_url, timeout=timeout)
            latency = int((time.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                return {"ok": False, "latency_ms": latency, "error": f"HTTP {resp.status_code}"}
            ip = ""
            try:
                ip = resp.json().get("ip", "")
            except Exception:
                ip = (resp.text or "").strip()[:64]
            return {"ok": True, "latency_ms": latency, "ip": ip}
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:140]}

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=20, thread_name_prefix="proxy-health") as ex:
        for proxy, res in zip(proxies, ex.map(_test_one, proxies)):
            results[proxy] = res
    return results


def get_checker() -> ProxyHealthChecker:
    """获取全局 ProxyHealthChecker 单例。"""
    global _health_checker
    with _start_lock:
        if _health_checker is None:
            _health_checker = ProxyHealthChecker()
        return _health_checker