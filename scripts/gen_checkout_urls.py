#!/usr/bin/env python3
"""批量提取 plus_eligible / plus_discount 账号的 checkout 支付链接。

接口: POST https://chatgpt.com/backend-api/payments/checkout
  Body: {"plan_name": "chatgptplusplan"}
  返回 checkout_session_id + processor_entity → 拼成付款 URL

结果写入 registered.extra_json.plus_check.checkout_url
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

from curl_cffi import requests

DB_PATH = '/root/.openclaw/workspace/gpt-outlook-register/webui/webui.db'
CST = timezone(timedelta(hours=8))

# 代理池（socks5h 才能连 chatgpt.com；部分节点 403/97 会失败，失败换下一个）
PROXY_POOL = [
    'socks5h://127.0.0.1:1087',
    'socks5h://127.0.0.1:1081',
    'socks5h://127.0.0.1:1082',
    'socks5h://127.0.0.1:1088',
    'socks5h://127.0.0.1:1085',
    'socks5h://127.0.0.1:1086',
    'socks5h://127.0.0.1:1089',
    'socks5h://127.0.0.1:1084',
]
_PROXY_IDX = 0

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36')
CHECKOUT_URL = 'https://chatgpt.com/backend-api/payments/checkout'


def now_str() -> str:
    return datetime.now(CST).strftime('%H:%M:%S')


def pick_proxy() -> str:
    global _PROXY_IDX
    p = PROXY_POOL[_PROXY_IDX % len(PROXY_POOL)]
    _PROXY_IDX += 1
    return p


def gen_checkout(access: str, cookie: str, proxy: str):
    """生成 checkout session，返回完整 stripe_hosted_url（带 #fid）。失败抛异常。"""
    headers = {
        'Authorization': f'Bearer {access}',
        'Accept': 'application/json',
        'User-Agent': UA,
        'Content-Type': 'application/json',
    }
    if cookie:
        headers['Cookie'] = cookie
    proxies = {'https': proxy, 'http': proxy}
    r = requests.post(CHECKOUT_URL, impersonate='chrome110', proxies=proxies,
                      headers=headers, json={'plan_name': 'chatgptplusplan'}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f'HTTP {r.status_code}: {r.text[:200]}')
    d = r.json()
    sid = d.get('checkout_session_id') or ''
    psk = d.get('publishable_key') or ''
    if not sid:
        raise RuntimeError(f'无 checkout_session_id: {r.text[:200]}')
    # 查 payment_pages 获取 stripe_hosted_url（带 #fid）
    pp_headers = {'Authorization': f'Bearer {psk}', 'Accept': 'application/json'}
    r2 = requests.get(f'https://api.stripe.com/v1/payment_pages/{sid}',
                      impersonate='chrome110', proxies=proxies,
                      headers=pp_headers, timeout=15)
    if r2.status_code != 200:
        # fallback: 用不带 #fid 的链接
        return f'https://checkout.stripe.com/c/pay/{sid}'
    pp = r2.json()
    hosted = pp.get('stripe_hosted_url') or ''
    if hosted:
        return hosted
    return f'https://checkout.stripe.com/c/pay/{sid}'


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT email, access_token, cookie_header, extra_json FROM registered "
        "WHERE access_token IS NOT NULL AND access_token != '' "
        "AND (extra_json LIKE '%plus_eligible%' OR extra_json LIKE '%plus_discount%') "
        "ORDER BY created_at DESC"
    ).fetchall()
    print(f'{now_str()} 找到 {len(rows)} 个 plus 优惠账号')

    ok, fail = 0, 0
    for i, row in enumerate(rows):
        email = row['email']
        access = row['access_token'] or ''
        cookie = row['cookie_header'] or ''
        extra = {}
        try:
            extra = json.loads(row['extra_json'] or '{}')
        except Exception:
            extra = {}
        pc = extra.get('plus_check') or {}

        # 已有链接就不重复生成
        if pc.get('checkout_url'):
            print(f'  [{i+1}/{len(rows)}] {email} 已有链接，跳过')
            ok += 1
            continue

        # 每个号试 N 个代理
        url = None
        last_err = ''
        tried = set()
        for _ in range(4):
            proxy = pick_proxy()
            if proxy in tried:
                continue
            tried.add(proxy)
            try:
                url = gen_checkout(access, cookie, proxy)
                break
            except Exception as e:
                last_err = str(e)[:120]
                time.sleep(0.5)
        if url:
            pc['checkout_url'] = url
            pc['checkout_url_generated_at'] = datetime.now(CST).isoformat()
            extra['plus_check'] = pc
            db.execute("UPDATE registered SET extra_json=? WHERE email=?",
                       (json.dumps(extra, ensure_ascii=False), email))
            db.commit()
            ok += 1
            print(f'  [{i+1}/{len(rows)}] ✅ {email}\n      {url}')
        else:
            fail += 1
            print(f'  [{i+1}/{len(rows)}] ❌ {email} ({last_err})')
        time.sleep(0.3)

    print(f'\n完成: 成功 {ok} / 失败 {fail}')


if __name__ == '__main__':
    main()