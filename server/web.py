"""LAN-only UHV Sentinel HTTP API, with cookie authentication and CSRF protection.

Use behind the bundled HTTPS Caddy proxy. No remote hardware control APIs exist.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from server.core import DEVICE_TYPES,Store,Poller

COOKIE='uhv_session'
TTL=8*3600

class Auth:
    def __init__(self,password:bytes,key:bytes):
        if len(password.strip())<16 or len(key)<32:
            raise RuntimeError('Configure a strong server password and a 32-byte session key before launch')
        self.pwd=password.strip();self.key=key
        self.failures={};self.lock=threading.Lock()
    def verify(self,password:str,client:str)->bool:
        with self.lock:
            now=time.time();list_=self.failures.get(client,[])
            list_=[t for t in list_ if now-t<90]
            if len(list_)>=8:return False
            self.failures[client]=list_
        good=hmac.compare_digest(password.encode('utf-8'),self.pwd)
        if not good:
            with self.lock:self.failures.setdefault(client,[]).append(time.time())
        else:
            with self.lock:self.failures.pop(client,None)
        return good
    def make(self):
        payload=f'{int(time.time())}.{secrets.token_hex(16)}'.encode()
        value=base64.urlsafe_b64encode(payload).decode().rstrip('=')
        sig=hmac.new(self.key,value.encode(),hashlib.sha256).hexdigest()
        return value+'.'+sig
    def check(self,token:str):
        try:
            val,sig=token.rsplit('.',1)
            if not hmac.compare_digest(sig,hmac.new(self.key,val.encode(),hashlib.sha256).hexdigest()):return False
            raw=base64.urlsafe_b64decode(val+'='*((-len(val))%4)).decode()
            issued=int(raw.split('.')[0])
            return 0<=time.time()-issued<TTL
        except (ValueError,UnicodeError,TypeError):return False
    def csrf(self,token:str):
        return hmac.new(self.key,b'csrf:'+token.encode(),hashlib.sha256).hexdigest()

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,bind,store:Store,poller:Poller,auth:Auth):
        super().__init__(bind,Handler)
        self.store=store;self.poller=poller;self.auth=auth

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    server_version='UHV-Sentinel-Server'
    sys_version=''
    def log_message(self,format,*args):
        # Do not log submitted credentials or raw URL query strings.
        print('http %s %s'%(self.client_address[0],format%args),flush=True)
    def _headers(self,code,content_type='application/json; charset=utf-8',length=0,headers=None):
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for a,b in headers or ():self.send_header(a,b)
        self.end_headers()
    def _send(self,code,object_,content_type='application/json; charset=utf-8',headers=None):
        content=object_ if isinstance(object_,bytes) else json.dumps(object_,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self._headers(code,content_type,len(content),headers)
        self.wfile.write(content)
    def _fail(self,code,message):
        # Rejecting before consuming the request body must close keep-alive.
        self.close_connection=True
        self._send(code,{'error':str(message)[:400]},headers=[('Connection','close')])
    def _body(self):
        length=int(self.headers.get('Content-Length','0'))
        if not 1<=length<=128*1024:raise ValueError('JSON request must be 1..131072 bytes')
        if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':
            raise ValueError('Expected Content-Type: application/json')
        obj=json.loads(self.rfile.read(length).decode('utf-8'))
        if not isinstance(obj,dict):raise ValueError('Expected JSON object')
        return obj
    def _cookie(self):
        from http.cookies import SimpleCookie
        try:
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            return cookie.get(COOKIE).value if cookie.get(COOKIE) else ''
        except Exception:return ''
    def _authorized(self):return self.server.auth.check(self._cookie())
    def _secure_cookie(self,token):
        # HTTP only inside the Docker network; clients access Caddy over HTTPS.
        return f'{COOKIE}={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={TTL}'
    def _route(self):
        parts=[x for x in urlsplit(self.path).path.split('/') if x]
        if any(len(x)>80 for x in parts):raise ValueError('Invalid path')
        return parts
    def _require_csrf(self):
        if not hmac.compare_digest(self.headers.get('X-UHV-CSRF',''),self.server.auth.csrf(self._cookie())):
            raise PermissionError('CSRF token missing or invalid')
    def _serve_static(self,path):
        root=Path(__file__).resolve().parent
        choices={'/':'ui.html','/ui.js':'ui.js','/ui.css':'ui.css'}
        if path.startswith('/s/') and len(path.split('/'))==3:path='/'
        name=choices.get(path)
        if name is None:return False
        kind={'ui.html':'text/html; charset=utf-8','ui.js':'text/javascript; charset=utf-8','ui.css':'text/css; charset=utf-8'}[name]
        self._send(200,(root/name).read_bytes(),kind)
        return True
    def do_GET(self):
        try:
            path=urlsplit(self.path).path
            if path=='/health':return self._send(200,{'ok':True})
            if path=='/api/session':
                token=self._cookie();good=self.server.auth.check(token)
                return self._send(200,{'authenticated':good,'csrf':self.server.auth.csrf(token) if good else None})
            if not self._authorized():
                # Static files may load without a session; endpoints cannot.
                if self._serve_static(path):return
                return self._fail(401,'Login required')
            bits=self._route()
            if bits==['api','types']:return self._send(200,DEVICE_TYPES)
            if bits==['api','systems']:
                result=[]
                for s in self.server.store.systems():
                    result.append({**s,'devices':len(self.server.store.devices(s['slug']))})
                return self._send(200,result)
            if len(bits)>=3 and bits[:2]==['api','systems']:
                slug=bits[2];s=self.server.store.system(slug)
                if not s:return self._fail(404,'Unknown laboratory system')
                if len(bits)==3:
                    devices=self.server.store.devices(slug)
                    return self._send(200,{**s,'devices':devices,'live':self.server.poller.snapshot(slug)})
                if bits[3:]==['history']:
                    q=parse_qs(urlsplit(self.path).query)
                    hours=float(q.get('hours',['24'])[0]);limit=int(q.get('limit',['1500'])[0])
                    return self._send(200,self.server.store.history(slug,hours,limit))
                if bits[3:]==['profile']:
                    return self._send(200,self.server.store.export_profile(slug),
                                      headers=[('Content-Disposition',f'attachment; filename="uhv-{slug}-profile.json"')])
            if self._serve_static(path):return
            return self._fail(404,'Not found')
        except (ValueError,TypeError) as exc:self._fail(400,str(exc))
        except Exception as exc:self._fail(500,'Internal server error')
    def do_POST(self):
        try:
            path=self._route()
            if path==['api','login']:
                body=self._body()
                if not self.server.auth.verify(str(body.get('password','')),self.client_address[0]):
                    return self._fail(401,'Incorrect password or temporarily rate limited')
                token=self.server.auth.make()
                return self._send(200,{'ok':True,'csrf':self.server.auth.csrf(token)},
                                  headers=[('Set-Cookie',self._secure_cookie(token))])
            if not self._authorized():return self._fail(401,'Login required')
            self._require_csrf()
            if path==['api','logout']:
                self._body()  # consume the JSON body before the next keep-alive request
                return self._send(200,{'ok':True},headers=[('Set-Cookie',
                                  f'{COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0')])
            if path==['api','systems']:
                data=self._body();slug=self.server.store.create_system(data.get('name',''),data.get('slug',''))
                return self._send(201,{'slug':slug,'url':'/s/'+slug})
            if len(path)>=3 and path[:2]==['api','systems']:
                slug=path[2]
                if not self.server.store.system(slug):return self._fail(404,'Unknown system')
                if path[3:]==['devices']:
                    device_id=self.server.store.add_device(slug,self._body())
                    self.server.poller.reconfigure();return self._send(201,{'id':device_id})
                if path[3:]==['layout']:
                    self.server.store.update_layout(slug,self._body());return self._send(200,{'ok':True})
                if path[3:]==['profile']:
                    self.server.store.import_profile(slug,self._body())
                    self.server.poller.reconfigure();return self._send(200,{'ok':True})
                if len(path)==6 and path[3]=='devices' and path[5]=='delete':
                    # Worker stops; records are deliberately retained.
                    self.server.store.delete_device(slug,path[4]);self.server.poller.reconfigure()
                    return self._send(200,{'ok':True})
            return self._fail(404,'Unknown API route')
        except PermissionError as exc:self._fail(403,str(exc))
        except (ValueError,OverflowError,TypeError,sqlite3.IntegrityError) as exc:self._fail(400,str(exc))
        except KeyError as exc:self._fail(404,str(exc))
        except Exception:self._fail(500,'Internal server error')

# sqlite3 is used in exception catch, not exposed to the client.
import sqlite3


def run(host='0.0.0.0',port=8765):
    root=Path(os.environ.get('UHV_DATA_DIR','./server-data')).resolve()
    password_file=Path(os.environ['UHV_ADMIN_PASSWORD_FILE'])
    key_file=Path(os.environ['UHV_SESSION_SECRET_FILE'])
    auth=Auth(password_file.read_bytes(),key_file.read_bytes())
    store=Store(root/'uhv-server.sqlite3')
    poller=Poller(store,int(os.environ.get('UHV_RETENTION_DAYS','365')))
    poller.start()
    try:
        http=Server((host,port),store,poller,auth)
        print('UHV Sentinel headless API started on container interface',flush=True)
        http.serve_forever(poll_interval=.5)
    finally:
        poller.stop()

if __name__=='__main__':run()
