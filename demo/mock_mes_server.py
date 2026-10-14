"""
Mock customer MES endpoint - stands in for the end user's site during the demo.

  POST /api/upload        receives files from LineBridge (raw body or multipart), checks the token
  GET  /                  live page showing everything received
  POST /api/outage        toggle a simulated outage (returns 503) to show LineBridge's retry queue

    python demo/mock_mes_server.py --port 8080 --token demo-token
"""
import argparse
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Customer MES Inbox</title><style>
:root{--bg:#f5f3ef;--p:#fff;--ink:#1d1b18;--mu:#6c665d;--line:#e3ded5;--acc:#3d5a98;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#141310;--p:#1d1b17;--ink:#ece8e1;--mu:#a39c90;--line:#33302a;--acc:#8fa8e0;--bad:#ff8a80}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{padding:16px 24px;background:var(--p);border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font-size:17px;margin:0}.mu{color:var(--mu)}main{max-width:1100px;margin:0 auto;padding:18px 24px}
.st{padding:4px 11px;border-radius:99px;font-weight:600;font-size:12.5px;background:#dcefe1;color:#1d7a3a}.st.down{background:#fbe4e2;color:var(--bad)}
button{font:inherit;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--p);color:var(--ink);cursor:pointer}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:14px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px}
.it{padding:9px 10px;border-radius:8px;cursor:pointer;border-bottom:1px solid var(--line);font-size:13px}.it:hover,.it.sel{background:var(--bg)}
.it b{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;word-break:break-all}
pre{margin:0;font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre;overflow:auto;max-height:600px}
.big{font-size:28px;font-weight:700}</style></head><body>
<header><h1>Customer MES · Inbox</h1><span class="mu">mock endpoint for the LineBridge demo</span><span style="flex:1"></span>
<span id="st" class="st">Online</span><button id="tg">Simulate outage</button></header>
<main><div class="card" style="margin-bottom:14px;display:flex;gap:30px;flex-wrap:wrap"><div><div class="big" id="n">0</div><span class="mu">files received</span></div>
<div><div class="big" id="rej">0</div><span class="mu">rejected during outage</span></div><div><div class="big" id="last">—</div><span class="mu">last received</span></div></div>
<div class="grid"><div class="card"><div id="list" class="mu">Waiting for LineBridge…</div></div><div class="card"><pre id="body">Select a file</pre></div></div></main>
<script>
let sel=null;
async function poll(){const s=await (await fetch('/api/received')).json();
 document.getElementById('n').textContent=s.items.length;document.getElementById('rej').textContent=s.rejected;
 const st=document.getElementById('st');st.textContent=s.online?'Online':'OUTAGE (503)';st.className='st'+(s.online?'':' down');
 document.getElementById('tg').textContent=s.online?'Simulate outage':'Restore service';
 document.getElementById('last').textContent=s.items.length?s.items[s.items.length-1].time:'—';
 const L=document.getElementById('list');L.innerHTML=s.items.slice().reverse().map(i=>`<div class="it ${sel===i.id?'sel':''}" data-id="${i.id}"><b>${i.name}</b><br><span class="mu">${i.time} · ${i.type} · ${i.size} bytes · auth ${i.auth}</span></div>`).join('')||'Waiting for LineBridge…';
 L.querySelectorAll('.it').forEach(e=>e.onclick=()=>{sel=+e.dataset.id;show();poll();});
 if(sel===null&&s.items.length){sel=s.items[s.items.length-1].id;show();}}
async function show(){document.getElementById('body').textContent=await (await fetch('/api/file/'+sel)).text();}
document.getElementById('tg').onclick=async()=>{await fetch('/api/outage',{method:'POST'});poll();};
poll();setInterval(poll,1500);</script></body></html>"""


class State:
    def __init__(self, store, token):
        self.items, self.online, self.rejected = [], True, 0
        self.store, self.token, self.lock = Path(store), token, threading.Lock()
        self.store.mkdir(parents=True, exist_ok=True)


def _extract_multipart(body, ctype):
    boundary = ctype.split("boundary=")[-1].encode()
    for part in body.split(b"--" + boundary):
        if b'name="file"' in part:
            head, _, data = part.partition(b"\r\n\r\n")
            name = head.split(b'filename="')[1].split(b'"')[0].decode()
            return name, data.rstrip(b"\r\n")
    return None, body


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, data, ctype="application/json"):
            data = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if self.path == "/api/received":
                with state.lock:
                    return self._send(200, {"online": state.online, "rejected": state.rejected,
                                            "items": [{k: v for k, v in i.items() if k != "path"} for i in state.items]})
            if self.path.startswith("/api/file/"):
                i = int(self.path.rsplit("/", 1)[1])
                return self._send(200, Path(state.items[i]["path"]).read_bytes(), "text/plain; charset=utf-8")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/api/outage":
                state.online = not state.online
                print(f"[mes] {'ONLINE' if state.online else 'OUTAGE simulated'}", flush=True)
                return self._send(200, {"online": state.online})
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not state.online:
                state.rejected += 1
                return self._send(503, {"error": "MES temporarily unavailable"})
            auth = self.headers.get("Authorization", "")
            if state.token and auth != f"Bearer {state.token}":
                return self._send(401, {"error": "bad token"})
            ctype = self.headers.get("Content-Type", "")
            name = self.headers.get("X-Filename", "upload.bin")
            if ctype.startswith("multipart/form-data"):
                name, body = _extract_multipart(body, ctype)
            with state.lock:
                i = len(state.items)
                p = state.store / f"{i:04d}_{Path(name).name}"
                p.write_bytes(body)
                state.items.append({"id": i, "name": name, "type": ctype.split(";")[0], "size": len(body),
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "auth": "ok" if state.token else "none", "path": str(p)})
            print(f"[mes] received {name} ({len(body)} bytes)", flush=True)
            self._send(201, {"status": "accepted", "id": i})
    return H


def serve(port=8080, token="demo-token", store="demo/workspace/mes_received", block=True):
    state = State(store, token)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    if not block:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, state
    print(f"[mes] Mock MES on http://localhost:{port}  (upload: /api/upload, token: {token or 'none'})")
    srv.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", default="demo-token")
    ap.add_argument("--store", default="demo/workspace/mes_received")
    a = ap.parse_args()
    serve(a.port, a.token, a.store)
