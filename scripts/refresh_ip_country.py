#!/usr/bin/env python3
"""遍历代理池，查询出口IP国家代码，存入 DB settings 的 ip_country_map。

每 90 分钟跑一次即可，代理 IP 是固定的，不用每次列表页实时查。
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webui import db

# 本地代理池
PORTS = [1080, 1081, 1082, 1083, 1084, 1085, 1086, 1087, 1088, 1089, 1090]


def query_country(port: int) -> tuple[str, str]:
    """通过代理查出口 IP + 国家代码，返回 (ip, country_code)。"""
    import requests
    proxy = f"socks5://127.0.0.1:{port}"
    # 优先用 ip-api（返回 countryCode）
    try:
        r = requests.get(
            "http://ip-api.com/json/?fields=query,countryCode",
            proxies={"http": proxy, "https": proxy},
            timeout=8,
        )
        data = r.json()
        ip = data.get("query", "")
        cc = data.get("countryCode", "")
        if ip and cc:
            return ip, cc
    except Exception:
        pass
    # 回退：cf trace 拿 IP，查库
    try:
        r = requests.get(
            "https://www.cloudflare.com/cdn-cgi/trace",
            proxies={"http": proxy, "https": proxy},
            timeout=8,
        )
        ip = ""
        for line in r.text.splitlines():
            if line.startswith("ip="):
                ip = line.split("=", 1)[1].strip()
        if ip:
            return ip, ""
    except Exception:
        pass
    return "", ""


def main():
    mapping = {}
    total = len(PORTS)
    for i, port in enumerate(PORTS, 1):
        try:
            ip, cc = query_country(port)
            if ip:
                mapping[ip] = cc
                status = cc or "?"
            else:
                status = "不通"
            print(f"[{i}/{total}] 108{port-1080} → {ip or '-'} ({status})")
        except Exception as e:
            print(f"[{i}/{total}] 108{port-1080} → 异常 {e}")
        time.sleep(0.5)

    db.set_setting("ip_country_map", json.dumps(mapping, ensure_ascii=False))
    print(f"\n✅ 已保存 {len(mapping)} 个 IP 国家映射到 DB:")
    for ip, cc in mapping.items():
        print(f"  {cc or '?'} -> {ip}")


if __name__ == "__main__":
    main()