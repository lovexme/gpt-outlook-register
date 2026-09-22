#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webui.exporter import refresh_codex_token
c=sqlite3.connect('webui/webui.db')
rt=c.execute("select refresh_token from registered where refresh_token is not null and refresh_token!='' limit 1").fetchone()[0]
d=refresh_codex_token(rt,timeout=30)
print('refresh_ok',len(d.get('access_token','')),len(d.get('refresh_token','')),len(d.get('id_token','')))
