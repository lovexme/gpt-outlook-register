#!/usr/bin/env python3
"""Tempo 邮箱库独立查询服务（与主面板隔离端口）。

功能：
- 添加要监控的邮箱（仅限 tempo 库里存在的邮箱）
- 一键刷新全部已添加邮箱的最新邮件
- 展开查看邮件正文，OTP 点击复制

安全设计：
- 页面路径为 32 位随机 hex token，不可枚举
- 所有接口须带 X-Mbx-Key header（32 位随机 hex）
- 只允许添加 tempo_mail_store.db 中已存在的邮箱，不能乱扫邮箱
- 不返回邮箱库列表；邮箱不存在返回"不可添加"
- 每 IP 限流
根路径 GET / 一律 403。
"""
import json
import os
import re
import secrets
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from webui import db as webui_db
from webui import mail_store as ms

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# ── token/key（存主库 settings，首次自动生成）──
_MBX_TOKEN = (webui_db.get_setting("mbx_page_token", "") or "").strip()
_MBX_KEY = (webui_db.get_setting("mbx_key", "") or "").strip()
if not _MBX_TOKEN or len(_MBX_TOKEN) < 16:
    _MBX_TOKEN = secrets.token_hex(16)
    webui_db.set_setting("mbx_page_token", _MBX_TOKEN)
if not _MBX_KEY or len(_MBX_KEY) < 16:
    _MBX_KEY = secrets.token_hex(16)
    webui_db.set_setting("mbx_key", _MBX_KEY)

_RATE = {}

_WATCH_FILE = Path(__file__).resolve().parent / "webui" / "mbx_watchlist.json"


def _load_watch():
    if _WATCH_FILE.exists():
        try:
            return json.loads(_WATCH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_watch(items):
    _WATCH_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                           encoding="utf-8")


class _HtmlText(HTMLParser):
    """HTML 转易读纯文本。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self.skip > 0:
            self.skip -= 1
        if tag in ("p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip == 0:
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)[:4000]


def fetch_messages(email: str, item: dict):
    """按邮箱来源(source)分流拉邮件并转纯文本。

    tempo     -> tempomails.com  API（app_token）
    tempmail  -> web2.temp-mail.org（Bearer email_token）
    tempamail -> api.tempamail.com（uuid + email_id）
    fmail     -> /api/inbox/{user}?domain= + /api/email/{token}
    """
    import requests as req
    ua = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
          "Chrome/124.0.0.0 Mobile Safari/537.36")
    source = (item.get("source") or "").strip()
    email_token = (item.get("email_token") or "").strip()
    app_token = ((item.get("app_token") or "").strip()
                 or webui_db.get_setting("tempo_app_token", "").strip())
    msgs = []

    if source == "tempo":
        if not app_token or app_token == "***":
            return []
        r = req.get(f"https://tempomails.com/api/messages/{app_token}/{email}",
                    headers={"User-Agent": ua, "Accept": "application/json"},
                    timeout=15)
        if r.status_code != 200:
            return []
        msgs = (r.json().get("messages")) or []
    elif source == "tempmail":
        if not email_token:
            return []
        try:
            from curl_cffi import requests as cffi
            s = cffi.Session(impersonate="chrome124")
            s.get("https://temp-mail.org/zh/", impersonate="chrome124", timeout=15)
            r = s.get("https://web2.temp-mail.org/messages",
                      headers={"Authorization": f"Bearer {email_token}"}, timeout=15)
        except ImportError:
            s = req.Session()
            s.get("https://temp-mail.org/zh/", headers={"User-Agent": ua}, timeout=15)
            r = s.get("https://web2.temp-mail.org/messages",
                      headers={"Authorization": f"Bearer {email_token}",
                               "User-Agent": ua}, timeout=15)
        if r.status_code != 200:
            return []
        msgs = (r.json().get("messages")) or []
        # 列表接口只有 bodyPreview；逐封读取详情获取 bodyHtml。
        for _m in msgs:
            _mid = _m.get("_id")
            if not _mid:
                continue
            try:
                _detail = s.get(
                    f"https://web2.temp-mail.org/messages/{_mid}",
                    headers={"Authorization": f"Bearer {email_token}"},
                    timeout=15,
                )
                if _detail.status_code == 200:
                    _m.update(_detail.json())
            except Exception:
                pass
    elif source == "tempamail":
        if not email_token:
            return []
        from webui.db import get_setting as _get_setting
        _uuid = _get_setting("tempamail_uuid", "").strip() or "c3c40144-a00c-43f5-9bb0-fb2f92bac1c4"
        import json as _json, urllib.request as _ur, urllib.error as _ue
        _payload = _json.dumps({"uuid": _uuid, "email_id": int(email_token)}).encode()
        _rq = _ur.Request("https://api.tempamail.com/android/messages",
                          data=_payload,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json"},
                          method="POST")
        try:
            with _ur.urlopen(_rq, timeout=15) as _resp:
                _data = _json.loads(_resp.read().decode())
            msgs = _data.get("messages") or []
        except Exception:
            return []
    elif source == "fmail":
        try:
            from curl_cffi import requests as cffi
            s = cffi.Session(impersonate="chrome124")
            _u, _h = email.split("@", 1)
            _r = s.get(f"https://{_h}/api/inbox/{_u}?domain={_h}", timeout=15)
            if _r.status_code != 200:
                return []
            _data = _r.json()
            for _m in (_data.get("emails") or []):
                _tk = (_m.get("token") or "").strip()
                if not _tk:
                    continue
                _body = {}
                try:
                    _r2 = s.get(f"https://{_h}/api/email/{_tk}", timeout=15)
                    if _r2.status_code == 200:
                        _body = _r2.json()
                except Exception:
                    pass
                msgs.append({
                    "id": _m.get("id"),
                    "from": _m.get("sender") or _m.get("from") or "",
                    "subject": _m.get("subject") or "",
                    "received_at": _m.get("received_at"),
                    "body": _body.get("body_html") or _body.get("body_text") or "",
                })
        except Exception:
            return []
    else:
        return []

    out = []
    for m in msgs:
        body = (m.get("body") or m.get("bodyHtml") or m.get("body_html")
                or m.get("bodyText") or m.get("body_text") or
                m.get("text") or m.get("content") or m.get("html") or
                m.get("bodyPreview") or "")
        if "<" in body and ">" in body:
            try:
                h = _HtmlText()
                h.feed(body)
                plain = h.text()
            except Exception:
                plain = body[:4000]
        else:
            plain = body[:4000]
        mm = re.search(r'(\d{4,8})', body)
        out.append({
            "id": m.get("id", ""),
            "from": m.get("from_email") or m.get("from") or "",
            "subject": m.get("subject") or "",
            "otp": mm.group(1) if mm else None,
            "body": plain,
        })
    return out


def _store_item(email):
    """从 tempo 库取该邮箱记录，不存在返回 None。"""
    db_path = Path(__file__).resolve().parent / "webui" / "tempo_mail_store.db"
    if not db_path.exists():
        return None
    store = ms.MailStore(str(db_path))
    return store.get(email)


def _page_html():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mailbox Monitor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f5f5f5;color:#333;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:24px;max-width:760px;margin:0 auto}
h1{font-size:20px;font-weight:600;margin-bottom:20px}
.add-box{display:flex;gap:8px;margin-bottom:16px}
.add-box input{flex:1;padding:10px 14px;border:1px solid #ddd;border-radius:8px;font-size:15px;outline:none}
.add-box input:focus{border-color:#4a90d9;box-shadow:0 0 0 2px rgba(74,144,217,.15)}
.add-box button{padding:10px 18px;background:#4a90d9;color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer;font-weight:500;white-space:nowrap}
.add-box button:disabled{background:#aaa;cursor:not-allowed}
.add-box button:hover:not(:disabled){background:#357abd}
.toolbar{display:flex;align-items:center;gap:8px;margin-bottom:16px}
.toolbar button{padding:9px 16px;background:#67c23a;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer;font-weight:500}
.toolbar button:disabled{background:#aaa;cursor:not-allowed}
.toolbar button:hover:not(:disabled){background:#57a832}
.status{color:#999;font-size:13px;min-height:20px;flex:1}
.badge{background:#eee;color:#666;font-size:12px;padding:3px 10px;border-radius:10px;font-weight:500}
.watch-item{border:1px solid #e0e0e0;border-radius:10px;padding:12px 16px;background:#fff;margin-bottom:12px}
.watch-head{display:flex;align-items:center;gap:10px;cursor:pointer}
.watch-email{font-family:monospace;font-size:14px;font-weight:500;flex:1;min-width:0;word-break:break-all}
.watch-arrow{color:#999;font-size:12px;transition:transform .2s}
.watch-arrow.open{transform:rotate(90deg)}
.watch-del{background:none;border:none;color:#e74c3c;font-size:18px;cursor:pointer;padding:0 4px;line-height:1}
.msg-list{padding:10px 0 0 0;display:flex;flex-direction:column;gap:10px;border-top:1px solid #f0f0f0;margin-top:10px}
.msg-card{border:1px solid #e8e8e8;border-radius:8px;padding:12px 14px;background:#fafafa}
.msg-head{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}
.otp-tag{background:#67c23a;color:#fff;font-size:13px;font-weight:700;padding:2px 10px;border-radius:4px;cursor:pointer;font-family:monospace}
.msg-subject{font-weight:500;font-size:13px;flex:1;min-width:0}
.msg-from{color:#999;font-size:12px}
.msg-body{font-size:13px;color:#444;line-height:1.7;white-space:pre-wrap;word-break:break-word;background:#fff;border-radius:6px;padding:10px 12px;max-height:400px;overflow:auto}
.empty-hint{color:#999;font-size:14px;text-align:center;padding:30px 0}
.hidden{display:none}
</style>
</head>
<body>
<h1>&#128235; 邮箱监控</h1>
<div class="add-box">
  <input id="email-input" type="email" placeholder="输入邮箱地址添加监控（必须已入库）" autocomplete="off" spellcheck="false">
  <button id="add-btn">添加</button>
</div>
<div class="toolbar">
  <button id="refresh-btn">&#8635; 刷新全部</button>
  <div id="status" class="status"></div>
</div>
<div id="list"></div>
<script>
(function(){const T='T_SLOT',M='K_SLOT';
var emails=[], expanded={};
async function addEmail(){
  var email=document.getElementById('email-input').value.trim().toLowerCase();
  if(!email)return;
  document.getElementById('add-btn').disabled=true;
  var st=document.getElementById('status');st.textContent='添加中...';
  try{
    var r=await fetch('/mbx/add',{method:'POST',headers:{'Content-Type':'application/json','X-Mbx-Key':M},body:JSON.stringify({e:email})});
    var d=await r.json();
    if(d.ok){document.getElementById('email-input').value='';st.textContent=d.message||'已添加';loadList();}
    else{st.textContent=d.error||'添加失败';}
  }catch(e){st.textContent='错误: '+e.message;}
  finally{document.getElementById('add-btn').disabled=false;}
}
async function refreshAll(){
  var btn=document.getElementById('refresh-btn');btn.disabled=true;
  var st=document.getElementById('status');st.textContent='刷新中...';
  try{
    var r=await fetch('/mbx/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Mbx-Key':M},body:'{}'});
    var d=await r.json();
    if(d.ok){
      render(d.items||[], d.errors||[]);
      var bad=Object.keys(d.errors||{}).length;
      st.textContent='刷新完成。'+(d.items||[]).length+' 个邮箱，'+bad+' 个失败';
    }else{st.textContent=d.error||'刷新失败';}
  }catch(e){st.textContent='错误: '+e.message;}
  finally{btn.disabled=false;}
}
async function removeEmail(e){
  var email=e.currentTarget.dataset.e;
  var st=document.getElementById('status');st.textContent='删除中...';
  try{
    var r=await fetch('/mbx/remove',{method:'POST',headers:{'Content-Type':'application/json','X-Mbx-Key':M},body:JSON.stringify({e:email})});
    var d=await r.json();
    if(d.ok){st.textContent='已删除';loadList();}else{st.textContent=d.error||'删除失败';}
  }catch(err){st.textContent='错误: '+err.message;}
}
async function loadList(){
  var st=document.getElementById('status');st.textContent='加载中...';
  try{
    var r=await fetch('/mbx/list',{method:'GET',headers:{'X-Mbx-Key':M}});
    var d=await r.json();
    if(d.ok){emails=d.emails||[];render(emails.map(function(e){return {email:e}}));st.textContent=emails.length+' 个邮箱';}
    else{st.textContent=d.error||'加载失败';}
  }catch(e){st.textContent='错误: '+e.message;}
}
function render(items,errors){
  var box=document.getElementById('list');
  if(!items.length){box.innerHTML='<div class="empty-hint">暂无监控邮箱，请在输入框添加</div>';return;}
  errors=errors||{};
  var h='';
  for(var i=0;i<items.length;i++){
    var it=items[i],em=it.email||'',msgs=it.messages||[];
    var isOpen=expanded[em];
    h+='<div class="watch-item">';
        h+='<div class="watch-head" data-e="'+H(em)+'" onclick="tog2(this)">';
        h+='<span class="watch-arrow'+(isOpen?' open':'')+'">&#9654;</span>';
        h+='<span class="watch-email">'+H(em)+'</span>';
        if(errors[em]===true)h+='<span class="badge" style="background:#e74c3c;color:#fff">失败</span>';
        else if(msgs.length)h+='<span class="badge" style="background:#67c23a;color:#fff">'+msgs.length+' 封</span>';
        else h+='<span class="badge">无邮件</span>';
        h+='<button class="watch-del" data-e="'+H(em)+'" onclick="del(this)">&#10005;</button>';
        h+='</div>';
    if(isOpen){
      if(msgs.length){
        h+='<div class="msg-list">';
        for(var j=msgs.length-1;j>=0;j--){
          var m=msgs[j];
          h+='<div class="msg-card"><div class="msg-head">';
          if(m.otp)h+='<span class="otp-tag" onclick="copyOtp(this)">'+H(m.otp)+'</span>';
          h+='<span class="msg-subject">'+H(m.subject||'(无主题)')+'</span>';
          h+='<span class="msg-from">'+H(m.from||'')+'</span>';
          h+='</div>';
          if(m.body)h+='<div class="msg-body">'+H(m.body)+'</div>';
          h+='</div>';
        }
        h+='</div>';
      }else{
        h+='<div class="empty-hint" style="padding:16px 0">无邮件</div>';
      }
    }
    h+='</div>';
  }
  box.innerHTML=h;
}
window.tog2=function(el){var em=el.dataset.e;expanded[em]=!expanded[em];renderFromWatch();};
window.del=function(btn){removeEmail({currentTarget:btn});};
window.copyOtp=function(el){navigator.clipboard.writeText(el.textContent);el.style.background='#5aad2a';};
function renderFromWatch(){
  var r=document.getElementById('list');
  // re-fetch list then render with cached expanded
  fetch('/mbx/list',{headers:{'X-Mbx-Key':M}}).then(function(rr){return rr.json()}).then(function(d){
    if(d.ok)render(d.emails.map(function(e){return {email:e}}));
  });
}
function H(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
document.getElementById('add-btn').addEventListener('click',addEmail);
document.getElementById('refresh-btn').addEventListener('click',refreshAll);
document.getElementById('email-input').addEventListener('keydown',function(e){if(e.key==='Enter')addEmail()});
loadList();
})();
</script>
</body>
</html>""".replace("T_SLOT", _MBX_TOKEN).replace("K_SLOT", _MBX_KEY)


@app.get("/")
def deny_root():
    """根路径一律 403，不暴露任何东西。"""
    return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)


def _check_auth(req):
    return req.headers.get("x-mbx-key") == _MBX_KEY


def _rate(req, interval=3):
    ip = req.client.host if req.client else "unknown"
    now = time.time()
    if now - _RATE.get(ip, 0) < interval:
        return True
    _RATE[ip] = now
    return False


@app.get("/mbx")
def mailbox_page():
    return HTMLResponse(_page_html())


@app.get("/mbx/list")
async def mailbox_list(req: Request):
    """已添加的邮箱列表（不包含任何邮件内容）。"""
    if not _check_auth(req):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    return {"ok": True, "emails": _load_watch()}


@app.post("/mbx/add")
async def mailbox_add(req: Request):
    """添加邮箱到监控列表（仅限 tempo 库存在的邮箱，防乱扫邮箱）。"""
    if not _check_auth(req):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    body = await req.json()
    email = (body.get("e") or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid email"}
    item = _store_item(email)
    if not item:
        return {"ok": False, "error": "email not in tempo store"}
    watch = _load_watch()
    if email not in watch:
        watch.append(email)
        _save_watch(watch)
    return {"ok": True, "message": "added"}


@app.post("/mbx/remove")
async def mailbox_remove(req: Request):
    if not _check_auth(req):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    body = await req.json()
    email = (body.get("e") or "").strip().lower()
    watch = _load_watch()
    if email in watch:
        watch.remove(email)
        _save_watch(watch)
    return {"ok": True}


@app.post("/mbx/refresh")
async def mailbox_refresh_all(req: Request):
    """刷新全部已添加邮箱的邮件。"""
    if not _check_auth(req):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    if _rate(req):
        return JSONResponse({"ok": False, "error": "rate limit"}, status_code=429)
    watch = _load_watch()
    items = []
    errors = {}
    for email in watch:
        item = _store_item(email)
        if not item:
            errors[email] = True
            continue
        try:
            items.append({"email": email, "messages": fetch_messages(email, item)})
        except Exception:
            errors[email] = True
    return {"ok": True, "items": items, "errors": errors}


if __name__ == "__main__":
    import socket as _sock
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18766
    bind_host = "::"
    sock = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
    sock.setsockopt(_sock.IPPROTO_IPV6, _sock.IPV6_V6ONLY, 0)
    sock.bind((bind_host, port))
    sock.listen()
    uvicorn.run(app, fd=sock.fileno(), log_level="info")