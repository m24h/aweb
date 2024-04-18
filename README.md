# aweb

A very light-weight HTTP Web async server.

Not fully tested.

Examples:

```python
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
# ssl is not supported, it's too expensive for most of DIYers, but it's also easy to be wrapped
asyncio.get_event_loop().run_until_complete( \
    aweb.server(web, port=80, limit=1024, clients=5))
asyncio.get_event_loop().run_until_complete( \
    aweb.server(web2, port=8080, limit=1024, clients=5))

@web('test/*', 'get', "I'm robot", generator)
async def test(flow, title, gen): # async function is also automatically supported
    await asyncio.sleep(1)
    #support outside content generator, also can send text/html/redirect/file/json/form directly
    if gen:
        flow.tail['Content-Type']=...
        flow.send=gen
    else:
        flow.send_json({'return':title})

# initialize other aync services

# loop all async services
asyncio.get_event_loop().run_forever()
```
