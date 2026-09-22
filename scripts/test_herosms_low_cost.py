#!/usr/bin/env python3
"""heroSMS 低价地区实测工具。

通过 GPT Outlook Register 的真实注册链路测试短信投递，而不是只测租号接口。
默认筛选 dr 服务价格 < $0.10 的地区，每地区实际租号两次，串行执行并保存断点。

用法：
  python3 scripts/test_herosms_low_cost.py
  python3 scripts/test_herosms_low_cost.py --max-price 0.10 --attempts 2
  python3 scripts/test_herosms_low_cost.py --countries 4,16,73 --attempts 2
  python3 scripts/test_herosms_low_cost.py --apply-winners
  python3 scripts/test_herosms_low_cost.py --status
  python3 scripts/test_herosms_low_cost.py --reset
"""
import argparse
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DB = PROJECT / "webui" / "webui.db"
API = "http://[::1]:18765"
HERO = "https://hero-sms.com/stubs/handler_api.php"
STATE = PROJECT / "webui" / "herosms_low_cost_results.json"
LOG = PROJECT / "webui" / "herosms_low_cost_test.log"
DEFAULT_PROXIES = [
    "socks5://127.0.0.1:1081",
    "socks5://127.0.0.1:1082",
    "socks5://127.0.0.1:1083",
    "http://127.0.0.1:7890",
]


def log(msg):
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def hero(key, action, **kwargs):
    url = HERO + "?" + urllib.parse.urlencode({"api_key": key, "action": action, **kwargs})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode(errors="replace")


def api(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"results": {}, "completed": False}


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(STATE)


def get_candidates(key, max_price, selected):
    prices = json.loads(hero(key, "getPrices"))
    names = {}
    try:
        raw = json.loads(hero(key, "getCountries"))
        if isinstance(raw, dict):
            for code, value in raw.items():
                if isinstance(value, dict):
                    names[str(code)] = value.get("eng") or value.get("rus") or value.get("name") or str(code)
    except Exception:
        pass
    out = []
    for code, services in prices.items():
        dr = services.get("dr") if isinstance(services, dict) else None
        if not dr:
            continue
        cost = float(dr.get("cost", 999))
        stock = int(dr.get("count", 0))
        if cost < max_price and stock > 0 and (not selected or str(code) in selected):
            out.append({"code": str(code), "name": names.get(str(code), str(code)), "cost": cost, "stock": stock})
    return sorted(out, key=lambda x: (x["cost"], -x["stock"], int(x["code"])))


def configure_country(code, max_price):
    values = {
        "sms_enabled": "1",
        "sms_country": str(code),
        "sms_allowed_countries": str(code),
        "sms_auto_country": "0",
        "sms_strict_whitelist": "1",
        "sms_max_price": str(max_price),
        "sms_auto_max_price": str(max_price),
        "sms_max_phone_attempts": "1",
        "sms_per_phone_timeout": "100",
        "sms_reuse_phone": "0",
    }
    c = connect()
    for key, value in values.items():
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    c.commit()
    c.close()


def start_run(proxy):
    return api("/api/register", {
        "email": None,
        "want_access_token": True,
        "want_session_token": True,
        "want_refresh_token": True,
        "proxy": proxy,
        "otp_timeout": 10,
        "allow_existing_login": True,
    })


def wait_run(run_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = connect()
        row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        c.close()
        if row and row["status"] in ("done", "failed"):
            return dict(row)
        time.sleep(10)
    return {"run_id": run_id, "status": "timeout", "error": "wait timeout", "log_path": ""}


def inspect_run(row):
    text = ""
    path = row.get("log_path") or ""
    if path and Path(path).exists():
        text = Path(path).read_text(errors="replace")
    received = (
        "✅ 收到 SMS 验证码:" in text
        or ("STATUS_OK:" in text and "未收到 SMS 验证码" not in text)
    )
    return {
        "rented": "已租到号码" in text,
        "send_ok": "POST add-phone/send 成功" in text,
        "received": received,
        "blocked": "Just a moment" in text or "Cloudflare" in text or "too many phone verification" in text,
        "status": row.get("status"),
        "error": (row.get("error") or "")[:240],
    }


def print_summary(state):
    print("地区\t价格\t实际租号\t收到验证码\t成功率")
    rows = sorted(state.get("results", {}).values(), key=lambda x: (x.get("cost", 999), x.get("name", "")))
    for row in rows:
        attempts = row.get("rented_attempts", 0)
        received = row.get("received", 0)
        rate = f"{received / attempts * 100:.0f}%" if attempts else "-"
        print(f"{row.get('name')}({row.get('code')})\t${row.get('cost', 0):.4f}\t{attempts}\t{received}\t{rate}")


def apply_winners(state, min_success=1):
    winners = [
        row for row in state.get("results", {}).values()
        if row.get("received", 0) >= min_success
    ]
    winners.sort(key=lambda x: (-x.get("received", 0), x.get("cost", 999)))
    if not winners:
        raise RuntimeError("没有实测收到验证码的地区")
    codes = ",".join(row["code"] for row in winners)
    c = connect()
    values = {
        "sms_country": winners[0]["code"],
        "sms_allowed_countries": codes,
        "sms_auto_country": "1",
        "sms_strict_whitelist": "1",
    }
    for key, value in values.items():
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    c.commit()
    c.close()
    print("已应用地区：" + ", ".join(f"{row['name']}({row['code']})" for row in winners))


def main():
    parser = argparse.ArgumentParser(description="heroSMS 低价地区真实收码测试")
    parser.add_argument("--max-price", type=float, default=0.10, help="筛选价格上限，不含该值（默认 0.10）")
    parser.add_argument("--attempts", type=int, default=2, help="每地区实际租号次数（默认 2）")
    parser.add_argument("--countries", help="只测试指定地区代码，逗号分隔")
    parser.add_argument("--timeout", type=int, default=1200, help="单注册任务最长等待秒数")
    parser.add_argument("--pause", type=int, default=30, help="任务间隔秒数")
    parser.add_argument("--status", action="store_true", help="仅显示已有结果")
    parser.add_argument("--reset", action="store_true", help="清空历史结果后退出")
    parser.add_argument("--apply-winners", action="store_true", help="把已收码地区写入注册机白名单后退出")
    args = parser.parse_args()

    if args.reset:
        if STATE.exists():
            STATE.unlink()
        print("结果已清空")
        return
    state = load_state()
    if args.status:
        print_summary(state)
        return
    if args.apply_winners:
        apply_winners(state)
        return

    selected = {x.strip() for x in (args.countries or "").split(",") if x.strip()}
    c = connect()
    key = c.execute("SELECT value FROM settings WHERE key='sms_api_key'").fetchone()[0]
    available = c.execute("SELECT count(*) FROM outlook_accounts WHERE status='available'").fetchone()[0]
    c.close()
    candidates = get_candidates(key, args.max_price, selected)
    state["candidates"] = candidates
    state["completed"] = False
    save_state(state)
    log(f"候选 {len(candidates)} 个，可用邮箱 {available}，每地区实际租号 {args.attempts} 次")

    proxy_index = int(state.get("proxy_index", 0))
    for item in candidates:
        code = item["code"]
        rec = state["results"].setdefault(code, {**item, "rented_attempts": 0, "received": 0, "runs": []})
        no_rent = 0
        while rec["rented_attempts"] < args.attempts:
            configure_country(code, args.max_price)
            proxy = DEFAULT_PROXIES[proxy_index % len(DEFAULT_PROXIES)]
            proxy_index += 1
            state["proxy_index"] = proxy_index
            save_state(state)
            log(f"{item['name']}({code}) ${item['cost']:.4f} 第 {rec['rented_attempts'] + 1}/{args.attempts} 次，代理 {proxy}")
            try:
                run = start_run(proxy)
                row = wait_run(run["run_id"], args.timeout)
                info = inspect_run(row)
                info.update({"run_id": run["run_id"], "proxy": proxy, "time": time.time()})
                rec["runs"].append(info)
                if info["rented"]:
                    rec["rented_attempts"] += 1
                    rec["received"] += int(info["received"])
                    no_rent = 0
                    log(f"完成：send_ok={int(info['send_ok'])} received={int(info['received'])} status={info['status']}")
                else:
                    no_rent += 1
                    log(f"未进入租号步骤，不计次数：{info['status']} {info['error']}")
                    if no_rent >= 4:
                        log("连续 4 个邮箱未进入租号，暂跳过该地区")
                        rec["deferred"] = True
                        break
                save_state(state)
            except Exception as exc:
                log(f"异常：{type(exc).__name__}: {exc}")
            time.sleep(args.pause)

    state["completed"] = True
    save_state(state)
    print_summary(state)


if __name__ == "__main__":
    main()
