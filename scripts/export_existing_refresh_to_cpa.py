#!/usr/bin/env python3
import json, sqlite3, urllib.request, time
DB='/root/.openclaw/workspace/gpt-outlook-register/webui/webui.db'
API='http://[::1]:18765/api/registered/export_to_panel'
start=1786464000
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
rows=list(c.execute("select email,created_at from registered where refresh_token is not null and refresh_token!='' and created_at>=? order by created_at",(start,)))
# 容器文件名清单由调用方通过结果复核；逐个手动导出，避免批量并发。
ok=fail=0
for i,r in enumerate(rows,1):
 body=json.dumps({'email':r['email'],'targets':['cpa']}).encode()
 req=urllib.request.Request(API,data=body,headers={'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=120) as resp: d=json.loads(resp.read().decode())
  result=d.get('cpa') or {}; good=bool(result.get('ok'))
  print(i,r['email'],'OK' if good else 'FAIL',result.get('message') or result.get('error',''))
  ok+=good; fail+=not good
 except Exception as e:
  print(i,r['email'],'FAIL',str(e)[:180]); fail+=1
 time.sleep(2)
print('SUMMARY',len(rows),'ok',ok,'fail',fail)
