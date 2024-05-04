'''
A light-weight async http web server for light-weight usage. 
Examples:

import aweb

web=aweb.Web()

@web('index.html')
def root(flow):
    flow.send_file('web/index.html')
        
@web('post/*', ':before')
def auth(flow):
    flow.var['user']=flow.cookie('user')
    if flow.head['host']=='vhoost':
        flow.path='vhost/'+flow.path

@web('', ':after')
def after_all(flow):
    flow.tail['Access-Control-Allow-Origin'] = '*' # header to send
    flow.set_cookie('user', flow.var['user'], max_age=3600)
    if not hasattr(flow, 'send'):
        flow.send_file('web/404.html', status=404, reason='LOST')

# in fact, more than one server with more path-router can be run at the same time
asyncio.get_event_loop().run_until_complete( \
    aweb.server(web, port=80, limit=1024, clients=5))
asyncio.get_event_loop().run_until_complete( \
    aweb.server(web2, port=8080, limit=1024, clients=5))

@web('test/*', 'get', "I'm robot", 'tag1')
async def test(flow, title, tag): # async function is also automatically supported
    await asyncio.sleep(1)
    flow.tail['My-TAG']=tag
    flow.send_json({'return':title})

# initialize other aync services

# loop all async services
asyncio.get_event_loop().run_forever()

'''

import asyncio
import json
import sys

_minetypes={
    'css': 'text/css',
    'gif': 'image/gif',
    'html': 'text/html',
    'htm': 'text/html',
    'jpg': 'image/jpeg',
    'js': 'application/javascript',
    'json': 'application/json',
    'png': 'image/png',
    'txt': 'text/plain',
    }

def minetype(ext):
    return _minetypes.get(ext.lower(), 'application/octet-stream')

# support only utf-8 in micropython
def url_decode(b):
    ret=bytearray()
    l=len(b)
    i=0
    while i<l:
        t=b[i]
        if t==0x28: # +
            t=0x20  # [space]
        elif t==0x25 and i+2<l: # %
            t=int(b[i+1:i+3], 16)
            i=i+2
        ret.append(t)
        i=i+1
    return ret.decode('utf-8')

_hex=b'0123456789ABCDEF'

def url_encode(s, safe=False):
    ret=bytearray()
    for b in s.encode('utf-8'):
        if (b>=65 and b<=90) or (b>=97 and b<=122) or b==45 or b==46 or (b==47 and not safe) or b==95 or b==126:
            ret.append(b)
        else:
            ret.append(37) # '%'
            ret.append(_hex[b>>4])
            ret.append(_hex[b&0x0F])
    return ret
    
# 'param' is a list in ((key1, value1), (key2, value2) ....) format
# this function will decode 'application/x-www-form-urlencoded' bytes from 'ba' and append key-value pairs into 'param'
def param_decode(param, ba):
    for kv in ba.split(b'&'):
        kv=kv.split(b'=',1)
        if not kv[0]:
            continue
        param.append((url_decode(kv[0].strip()), url_decode(kv[1].strip()) if len(kv)>1 else ''))

# 'param' should be in ((key1, value1), (key2, value2) ....) format, both tuple or list are acceptable
# 'ba' is a bytearray to append 'application/x-www-form-urlencoded' encoded parameters
def param_encode(ba, param):
    for k,v in param:
        if not k:
            continue
        if len(ba)>0:
            ba.extend(b'&')
        ba.extend(url_encode(k))
        ba.extend(b'=')
        ba.extend(url_encode(v or ''))
        
def param_get(param, name):
    for k,v in param:
        if k==name:
            return v
    return None

def param_array(param, name):
    ret=[]
    for k,v in param:
        if k==name:
            ret.append(v)
    return ret

class Web(list):
    #decorator to specify a function as web routing
    #path is likely 'test/index.html' 'test/path', without case sensitive
    #path is from root without leading '/'
    #using '*' at the tail of path as wildcard
    #method can be 'get' 'post', or use ',' to combine them
    #args and kwargs will be used to call the mapped function
    #longest path matches first
    #method ':before' ':after' is specially for function running before or after
    def __call__(self, path, method='get,post', *args, **kwargs):
        def decorator(func):
            p=path.lower()
            if p.endswith('*'):
                p=p[:-1]
                wc=True
            else:
                wc=False
            order=(len(p)<<2)+(0 if wc else 1)
            i=0
            n=len(self)
            while i<n:
                if self[i][0]<=order:
                    break
            for m in method.split(','):
                self.insert(i, (order, p, m.strip().lower(), wc, func, args, kwargs))
            return func
        return decorator
    
    # find the path, return the longest one, then matching method, then matching wildcard
    # return a tuple as (order, path, method, wildcard, func, args, kwargs)
    def find(self, path, method):
        p=path.lower() if path else ''
        m=method.lower() if method else 'get'
        for t in self:
            if (t[1]==p or (t[3] and p.startswith(t[1]))) and t[2]==m:
                return t
        return None

class Flow:
    def __init__(self, r, w, limit):
        self.r=r
        self.w=w
        self.limit=limit
        self.buf=b''
        self.var={}   # for unspecified usage during whole flow
        
    async def readallb(self):
        if t:=self.head.get('content-length'):
            t=int(t)
            if t>self.limit:
                raise MemoryError('Out of limit size')
            buf=self.buf
            self.buf=b''
            if (t:=t-len(buf))>0:
                buf+=await self.r.readexactly(t)
            return buf
        else:
            raise NotImplementedError('Unknown content length')
   
    async def readlineb(self):
        buf=self.buf
        while True:
            if (t:=buf.find(b'\n'))>=0:
                ret=buf[:t].rstrip(b'\r\n')
                self.buf=buf[t+1:]
                return ret
            if (t:=self.limit-len(buf))<=0:
                raise MemoryError('Out of limit size')
            t=await self.r.read(t)
            if not t:
                self.buf=b''
                return buf
            buf+=t
        
    async def _start(self):
        t=await self.readlineb()
        t=t.split()
        if len(t)<3:
            raise ValueError('Bad protocol')
        self.method=t[0].decode('utf-8').lower()
        v=t[2].split(b'/', 1)
        self.ver=v[1].decode('utf-8') if len(v)>1 else '1.1'
        t=t[1].split(b'?', 1)
        v=t[0].replace(b'\\', b'/').split(b'/', 1)
        self.path=url_decode(v[1]).lower() if len(v)>1 else ''
        self._query_b=t[1].lstrip(b'?') if len(t)>1 else b''
        self.head={}
        self.cookie={}
        while t:=await self.readlineb():
            t=t.split(b':',1)
            v=t[0].strip().decode('utf-8').lower()
            if v=='cookie':
                if len(t)>1:
                    for v in t[1].split(b';'):
                        t=v.split(b'=', 1)
                        self.cookie[url_decode(t[0].strip())]=url_decode(t[1].strip()) if len(t)>1 else ''
            else:
                self.head[v]=t[1].strip().decode('utf-8') if len(t)>1 else ''
        self.tail={'Connection':'Close'}
        self._setcookie={}
        
    async def _finish(self):
        w=self.w
        if not hasattr(self, 'send'):
            w.write(b'HTTP/1.0 404 NOTFOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n!!! NOT FOUND !!!')
            await w.drain()
            return
        w.write(('HTTP/1.0 '+str(getattr(self, 'status', 200))+' '+str(getattr(self, 'reason', 'NA'))+'\r\n').encode('utf-8'))
        await w.drain()
        for k,v in self.tail.items():
            if not k:
                continue
            if isinstance(v, tuple) or isinstance(v, list):
                for t in v:
                    w.write(k.encode('utf-8')+b': '+(t or '').encode('utf-8')+b'\r\n')
                    await w.drain()
            else:
                w.write(k.encode('utf-8')+b': '+(v or '').encode('utf-8')+b'\r\n')
                await w.drain()
        for k,v in self._setcookie.items():
            if k:
                w.write(b'Set-Cookie: '+url_encode(k)+b'='+(v or b'')+b'\r\n')
                await w.drain()
        w.write(b'\r\n')
        await w.drain()
        send=self.send
        if isinstance(send, str):
            w.write(send.encode('utf-8'))
            await w.drain()
        elif isinstance(send, bytes) or isinstance(send, bytearray) or isinstance(send, memoryview):
            w.write(send)
            await w.drain()
        elif hasattr(send, 'send') and callable(send.send): # coro
            await send
        elif callable(send): # function
            send()
    
    def query(self):
        if hasattr(self, '_query_b'):
            self._query=[]
            param_decode(self._query, self._query_b)
            delattr(self, '_query_b')
        return self._query
                
    async def recv_json(self):
        if not hasattr(self, 'recv'):
            t=await self.readallb()
            t=t.decode('utf-8')
            t=json.loads(t)
            self.recv=t
        return self.recv
                
    async def recv_form(self):
        if not hasattr(self, 'recv'):
            t=await self.readallb()
            self.recv=[]
            param_decode(self.recv, t)
        return self.recv
        
    def set_cookie(self, name, value, *, path=None, domain=None, expires=None, \
                   max_age=None, secure=False, http_only=False, partitioned=False):
        t=bytearray(url_encode(value) if value else b'')
        if path:
            t.extend(b'; Path='+path.encode('utf-8'))
        if domain:
            t.extend(b'; Domain='+domain.encode('utf-8'))
        if expires:
            t.extend(b'; Expires='+expires.encode('utf-8'))
        if isinstance(max_age, int):
            t.extend(b'; Max-Age='+str(max_age).encode('utf-8'))
        if secure:
            t.extend(b'; Secure')
        if http_only:
            t.extend(b'; HttpOnly')
        if partitioned:
            t.extend(b'; Partitioned')
        self._setcookie[name]=t
    
    def del_cookie(self, name):
        self._setcookie[name]=b'; Expires=Thu, 01 Jan 1970 00:00:01 GMT; Max-Age=0'
    
    def send_text(self, s, *, max_age=None, status=200, reason='OK'):
        self.tail['Content-Type']='text/plain; charset=utf-8'
        if max_age is not None:
            self.tail['Cache-Control']='public, max-age='+str(max_age)
        self.send=s
        self.status=status
        self.reason=reason
        
    def send_html(self, s, *, max_age=None, status=200, reason='OK'):
        self.tail['Content-Type']='text/html; charset=utf-8'
        if max_age is not None:
            self.tail['Cache-Control']='public, max-age='+str(max_age)        
        self.send=s
        self.status=status
        self.reason=reason
        
    def send_redirect(self, url):
        self.tail['Location']=url
        self.status=302
        self.reason='REDIR'
        self.send=None
        
    def send_json(self, obj, *, status=200, reason='OK'):
        self.tail['Content-Type']='application/json; charset=utf-8'
        self.tail['Cache-Control']='no-store'
        self.send=json.dumps(obj, separators=(',', ':'))
        self.status=status
        self.reason=reason

    # obj should be in ((key1, value1), (key2, value2) ...) format, both tuple or list are acceptable
    def send_form(self, obj, *, status=200, reason='OK'):
        self.tail['Content-Type']='application/x-www-form-urlencoded; charset=utf-8'
        self.tail['Cache-Control']='no-store'
        self.send=bytearray()
        param_encode(self.send, obj)
        self.status=status
        self.reason=reason
        
    async def _send_file(self, file):
        w=self.w
        buf=bytearray(self.limit)
        mv=memoryview(buf)
        with open(file, 'rb') as f:
            while t:=f.readinto(buf):
                w.write(mv[:t])
                await w.drain()
        
    def send_file(self, file, *, max_age=86400, status=200, reason='OK'):
        t=file.rsplit('.',1)
        t=t[1] if len(t)>1 else ''
        self.tail['Content-Type']=minetype(t)+'; charset=utf-8'
        if max_age is not None:
            self.tail['Cache-Control']='public, max-age='+str(max_age)
        self.send=self._send_file(file)          
        self.status=status
        self.reason=reason             
                
#start a server listening, return an asyncio.Server object
async def server(web, host='0.0.0.0', port=80, limit=1024, clients=10, ssl=None):
    clnt=0
    async def dispatcher(r, w):
        nonlocal clnt
        if clnt<=clients:
            flow=Flow(r, w, limit=limit)
            clnt=clnt+1
            try:
                try:
                    await flow._start()
                    if exe:=web.find(flow.path, ':before'):
                        coro=exe[4](flow, *(exe[5]), **(exe[6]))
                        if hasattr(coro, 'send') and callable(coro.send):
                            await coro
                    if not hasattr(flow, 'send') and (exe:=web.find(flow.path, flow.method)):
                        coro=exe[4](flow, *(exe[5]), **(exe[6]))
                        if hasattr(coro, 'send') and callable(coro.send):
                            await coro
                    if exe:=web.find(flow.path, ':after'):
                        coro=exe[4](flow, *(exe[5]), **(exe[6]))
                        if hasattr(coro, 'send') and callable(coro.send):
                            await coro
                except:
                    w.write(b'HTTP/1.0 500 INTERR\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n!!! Internal Error !!!')
                    await w.drain()
                    raise
                else:
                    await flow._finish()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                sys.print_exception(e)
            finally:
                clnt=clnt-1
        try:
            w.close()
            await w.wait_closed()
        except:
            pass
    return await asyncio.start_server(dispatcher, host, port, ssl=ssl)
