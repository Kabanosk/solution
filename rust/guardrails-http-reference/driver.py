import json
import logging
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
logging.disable(logging.CRITICAL)
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from scout.guardrails import HttpGuardrails
try:
    data=json.load(sys.stdin)
    if not isinstance(data,dict) or 'calls' not in data:raise ValueError()
except (ValueError,TypeError):print('{"error":"invalid_input"}');sys.exit()
responses=list(data.get('responses',[]));requests=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append({'path':self.path,'authorization':self.headers.get('Authorization'),'body':payload})
        response=responses.pop(0) if responses else {'json':{'status':'success'}}
        payload=response.get('text',json.dumps(response.get('json',{}))).encode()
        self.send_response(response.get('status',200));self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.send_header('Connection','close')
        for key,value in response.get('headers',{}).items():self.send_header(key,value)
        self.end_headers();self.wfile.write(payload)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{server.server_port}'
data['url']='' if data.get('disabled') else base
try:
    if '--native' in sys.argv:
        process=subprocess.run([sys.argv[sys.argv.index('--native')+1]],input=json.dumps(data),text=True,capture_output=True,check=True)
        values=json.loads(process.stdout)
    else:
        client=HttpGuardrails(data['url'],data.get('token','test-token'),model='scout-test')
        values=[]
        for call in data['calls']:
            result=client.warm_up() if call['op']=='warm' else client.check(call.get('config','user-input'),call.get('messages',[]))
            values.append({'status':result.status,'activated_rails':list(result.activated_rails),'detail':result.detail})
    for result in values:result['detail']=result['detail'].replace(base,'<provider>')
    print(json.dumps({'values':values,'requests':requests},sort_keys=True,ensure_ascii=False,separators=(',',':')))
finally:server.shutdown();server.server_close();thread.join()
