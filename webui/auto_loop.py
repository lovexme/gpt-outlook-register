"""全局 auto-loop 控制器：自动从号池 claim 一个号 → 跑注册 → 完成后继续。

设计：
  - 全局单例（同时最多一个 auto-loop 在跑，避免并发抢号 / OpenAI 风控）
  - 状态机：stopped → running → paused → running / stopped
  - 优雅暂停：当前 run 跑完才检查 paused 标志，不强杀正在跑的注册
  - 空号池：claim 不到号 → 自动停止 + 推送 idle 事件
  - 复用 registrar.start_registration：每个号开一个 run，等其结束再判断要不要 claim 下一个
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Optional

from . import db, registrar

logger = logging.getLogger("auto_loop")


class AutoLoopState:
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class AutoLoopController:
    """单例 auto-loop 控制器。

    - start(options) : 启动循环（options 是注册参数，跟单跑一样）
    - pause()        : 暂停（当前 run 跑完后停在下一轮前）
    - resume()       : 恢复
    - stop()         : 彻底停止
    - status()       : 当前状态 + 进度信息
    - subscribe()    : 返回一个 queue，可用于 SSE 推送状态变化
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._state = AutoLoopState.STOPPED
        self._thread: Optional[threading.Thread] = None
        self._options: dict = {}
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # set = 暂停
        # 进度统计
        self._started_at: float = 0.0
        self._registered_ok = 0
        self._registered_fail = 0
        self._current_email = ""
        self._current_run_id = ""
        self._last_message = ""
        # 熔断状态
        self._consecutive_network_fails = 0
        self._circuit_break_threshold = 3  # 连续 N 次网络错误自动暂停
        self._last_break_reason = ""
        # SSE 订阅
        self._subscribers: list[queue.Queue] = []

    # ──────────────────────── 公共 API ────────────────────────

    def start(self, options: dict) -> dict:
        with self._lock:
            if self._state in (AutoLoopState.RUNNING, AutoLoopState.PAUSED):
                return {"ok": False, "error": f"已经在跑了 (state={self._state})"}
            # 重置
            self._stop_event.clear()
            self._pause_event.clear()
            self._options = dict(options or {})
            self._state = AutoLoopState.RUNNING
            self._started_at = time.time()
            self._registered_ok = 0
            self._registered_fail = 0
            self._current_email = ""
            self._current_run_id = ""
            self._last_message = "auto-loop 启动"
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="auto-loop"
            )
            self._thread.start()
        self._broadcast("state", self._snapshot())
        return {"ok": True, "state": self._state}

    def pause(self) -> dict:
        with self._lock:
            if self._state != AutoLoopState.RUNNING:
                return {"ok": False, "error": f"当前 state={self._state}，不可暂停"}
            self._pause_event.set()
            self._state = AutoLoopState.PAUSED
            self._last_message = "已请求暂停（当前 run 跑完才生效）"
        self._broadcast("state", self._snapshot())
        return {"ok": True, "state": self._state}

    def resume(self) -> dict:
        with self._lock:
            if self._state != AutoLoopState.PAUSED:
                return {"ok": False, "error": f"当前 state={self._state}，不可恢复"}
            self._pause_event.clear()
            self._state = AutoLoopState.RUNNING
            self._last_message = "已恢复"
        self._broadcast("state", self._snapshot())
        return {"ok": True, "state": self._state}

    def stop(self) -> dict:
        with self._lock:
            if self._state == AutoLoopState.STOPPED:
                return {"ok": False, "error": "没在跑"}
            self._stop_event.set()
            self._pause_event.clear()  # 让 wait 立即返回
            self._last_message = "已请求停止（当前 run 跑完才生效）"
        self._broadcast("state", self._snapshot())
        return {"ok": True}

    def status(self) -> dict:
        return self._snapshot()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        # 立刻推一次当前状态
        try:
            q.put_nowait({"kind": "state", "data": self._snapshot()})
        except queue.Full:
            pass
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            try: self._subscribers.remove(q)
            except ValueError: pass

    # ──────────────────────── 内部 ────────────────────────

    def _snapshot(self) -> dict:
        with self._lock:
            stats = db.stats()
            return {
                "state": self._state,
                "started_at": self._started_at,
                "elapsed": (time.time() - self._started_at) if self._started_at else 0,
                "registered_ok": self._registered_ok,
                "registered_fail": self._registered_fail,
                "current_email": self._current_email,
                "current_run_id": self._current_run_id,
                "last_message": self._last_message,
                "pool_stats": stats,
            }

    def _broadcast(self, kind: str, data):
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait({"kind": kind, "data": data})
            except queue.Full:
                pass

    def _set_message(self, msg: str):
        with self._lock:
            self._last_message = msg
        self._broadcast("state", self._snapshot())

    def _loop(self):
        """循环：claim → 跑 → 等结束 → 检查暂停/停止 → 继续。"""
        idle_round = 0  # 连续没号的次数
        while True:
            # 检查停止
            if self._stop_event.is_set():
                self._set_message("已停止")
                with self._lock:
                    self._state = AutoLoopState.STOPPED
                self._broadcast("state", self._snapshot())
                return

            # 检查暂停
            if self._pause_event.is_set():
                self._set_message("已暂停，等待恢复...")
                # 等待恢复 or 停止
                while self._pause_event.is_set() and not self._stop_event.is_set():
                    time.sleep(0.5)
                if self._stop_event.is_set():
                    continue
                self._set_message("已恢复，继续循环")

            # claim 下一个号
            account = db.claim_next()
            if not account:
                idle_round += 1
                self._set_message(f"号池空，等待新号导入... (空号 {idle_round} 轮)")
                # 空 10 轮（约 30s）就自动停止
                if idle_round >= 10:
                    self._set_message("号池空 30s，自动停止 auto-loop")
                    with self._lock:
                        self._state = AutoLoopState.STOPPED
                    self._broadcast("state", self._snapshot())
                    return
                # 等 3s 再试
                for _ in range(30):
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        break
                    time.sleep(0.1)
                continue
            idle_round = 0

            # 启一个 run
            try:
                run_id = registrar.start_registration(account, self._options)
            except Exception as e:
                logger.exception(f"启动注册失败: {e}")
                db.release_unused(account["email"])
                self._set_message(f"启动 run 失败: {e}")
                time.sleep(2)
                continue

            with self._lock:
                self._current_email = account["email"]
                self._current_run_id = run_id
                self._last_message = f"正在注册 {account['email']} (run={run_id})"
            self._broadcast("state", self._snapshot())
            self._broadcast("run_started", {"email": account["email"], "run_id": run_id})

            # 等当前 run 跑完（轮询 DB runs 表 status）
            ok, category = self._wait_run_finish(run_id)
            with self._lock:
                if ok:
                    self._registered_ok += 1
                    self._consecutive_network_fails = 0
                else:
                    self._registered_fail += 1
                    if category == "network":
                        self._consecutive_network_fails += 1
                    else:
                        self._consecutive_network_fails = 0
                self._current_email = ""
                self._current_run_id = ""
                self._last_message = (
                    f"上一个号完成 ({'成功' if ok else f'失败/{category or 'unknown'}'})，"
                    f"累计 ok={self._registered_ok} fail={self._registered_fail}"
                )
                # 熔断判断
                trigger_break = (
                    self._consecutive_network_fails >= self._circuit_break_threshold
                    and self._state == AutoLoopState.RUNNING
                )
            self._broadcast("state", self._snapshot())
            self._broadcast(
                "run_finished",
                {"email": account["email"], "run_id": run_id, "ok": ok, "category": category},
            )

            if trigger_break:
                with self._lock:
                    self._pause_event.set()
                    self._state = AutoLoopState.PAUSED
                    self._last_break_reason = (
                        f"连续 {self._consecutive_network_fails} 次网络/环境错误，"
                        f"自动暂停（号已自动 release，请检查代理后点恢复）"
                    )
                    self._last_message = self._last_break_reason
                    self._consecutive_network_fails = 0  # 重置计数，恢复后重新计
                logger.warning(self._last_break_reason)
                self._broadcast("circuit_break", {"reason": self._last_break_reason})
                self._broadcast("state", self._snapshot())

            # 给 OpenAI 喘口气，免得连续打太狠被风控
            cool_down = float(self._options.get("cool_down_seconds") or 3)
            if cool_down > 0:
                for _ in range(int(cool_down * 10)):
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        break
                    time.sleep(0.1)

    def _wait_run_finish(self, run_id: str, timeout: int = 1800) -> tuple[bool, str]:
        """轮询 runs 表，等 run 跑完。返回 (ok, error_category)。

        ok=True 时 category=""
        ok=False 时 category 为 'network' / 'account' / 'unknown' / ''（超时为""）
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_event.is_set():
                return False, ""
            con = db._conn()
            cur = con.execute(
                "SELECT status, error_category FROM runs WHERE run_id=?", (run_id,)
            )
            row = cur.fetchone()
            if row:
                st = row["status"]
                if st == "done":
                    return True, ""
                if st == "failed":
                    return False, (row["error_category"] or "")
            time.sleep(1)
        logger.warning(f"run {run_id} 等了 {timeout}s 没结束，超时放弃")
        return False, ""


# 全局单例
CONTROLLER = AutoLoopController()
