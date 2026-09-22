#!/usr/bin/env python3
"""Moakt 临时邮箱 CLI 测试脚本 (requests 版)"""
import json
import logging
import re
import time
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

class MoaktMail:
    BASE = "https://www.moakt.com"
    
    def __init__(self, proxy=None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if proxy:
            norm = proxy
            if norm.startswith("socks5://"):
                norm = "socks5h://" + norm[9:]
            self.session.proxies = {"http": norm, "https": norm}
        self._email = ""
        self._lang = "en"
    
    def create_mailbox(self):
        """创建随机邮箱"""
        r = self.session.post(f"{self.BASE}/{self._lang}/inbox", 
                              data={"random": "获得一个随机邮箱地址"},
                              allow_redirects=True, timeout=20)
        email_match = re.search(r'id="email-address">([^<]+)', r.text)
        if email_match:
            self._email = email_match.group(1).strip()
            log.info(f"[moakt] 邮箱: {self._email}")
            return self._email
        raise RuntimeError(f"创建邮箱失败: status={r.status_code}")
    
    def get_email(self):
        return self._email
    
    def get_messages(self, html=None):
        """解析邮件列表，返回 [(href, subject, sender), ...]"""
        if html is None:
            r = self.session.get(f"{self.BASE}/{self._lang}/inbox", timeout=20)
            html = r.text
        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*><a[^>]*href="(/[^"]*inbox/message/[^"]*)"[^>]*>([^<]*)</a></td>\s*<td[^>]*>([^<]*)</td>',
            html
        )
        return rows
    
    def get_message_detail(self, href):
        """获取邮件详情"""
        r = self.session.get(f"{self.BASE}{href}", timeout=20)
        # 提取邮件正文
        body = re.search(r'<div[^>]*class="[^"]*message-body[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        if body:
            return body.group(1)
        return r.text

    def wait_for_otp(self, timeout=120):
        """轮询等待 OTP"""
        deadline = time.time() + timeout
        seen = set()
        while time.time() < deadline:
            rows = self.get_messages()
            for href, subject, sender in rows:
                if href in seen:
                    continue
                seen.add(href)
                log.info(f"[moakt] 新邮件: {subject} from {sender}")
                detail = self.get_message_detail(href)
                # 提取 6 位 OTP
                otp = re.search(r'(?<!\d)(\d{6})(?!\d)', detail)
                if otp:
                    log.info(f"[moakt] ✅ OTP={otp.group(1)}")
                    return otp.group(1)
            time.sleep(3)
        raise TimeoutError(f"moakt OTP timeout {timeout}s")

# 测试
if __name__ == "__main__":
    m = MoaktMail(proxy="socks5h://127.0.0.1:1081")
    email = m.create_mailbox()
    print(f"Email: {email}")
    rows = m.get_messages()
    print(f"邮件数: {len(rows)}")
    # 等 10 秒再看
    print("等待 10 秒...")
    time.sleep(10)
    rows = m.get_messages()
    print(f"10秒后邮件数: {len(rows)}")
    for href, subject, sender in rows:
        print(f"  subject={subject} sender={sender}")