"""Original guardrail text behavior; no external network or production settings."""
import json
import logging
from pathlib import Path
import sys
logging.disable(logging.CRITICAL)
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from scout import guardrails as g
try:
    data=json.load(sys.stdin)
    if not isinstance(data,dict) or data.get('op') not in ['mask','chunks','messages','rails','reply','digest','outside','ksef','batches','response']:raise ValueError()
except (ValueError,TypeError):
    print('{"error":"invalid_input"}');sys.exit()
op=data['op']
if op=='mask':result=g.mask_sensitive(data['text'])
elif op=='chunks':result=g._chunks(data['text'])
elif op=='messages':result=g._messages(data['config'],data['texts'])
elif op=='rails':result=g._rail_names(data['body'])
elif op=='reply':result=g.blocked_reply(data['attempt'])
elif op=='digest':result=g._digest(data['text'])
elif op=='outside':result=g._outside_items(data['tool'],data.get('result'))
elif op=='ksef':result=g.ksef_text(data['doc'])
elif op=='batches':result=g._batches(data['turns'],data['indexes'])
elif op=='response':
    import httpx
    client=g.HttpGuardrails('http://127.0.0.1',http=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data['body']))))
    value=client.check('test',[])
    result={'status':value.status,'activated_rails':value.activated_rails,'detail':value.detail}
print(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')))
