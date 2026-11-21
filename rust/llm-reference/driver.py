"""Real HTTP differential harness. Only its local scripted provider is contacted."""
import dataclasses
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/"backend"))
logging.disable(logging.CRITICAL)


def main():
    try:
        data=json.load(sys.stdin)
        if not isinstance(data,dict) or data.get("op") not in ["encode","text","wire","prompt","tls","estimate","chat"]:raise ValueError()
    except (ValueError,TypeError):
        print('{"error":"invalid_input"}');return
    from cfo.llm import OpenAICompatLLM,ToolSpec,Message,_calls_from_text,tls_verify_setting
    from cfo.usage import recorder
    if data["op"]!="chat":
        if "--native" in sys.argv:
            binary=sys.argv[sys.argv.index("--native")+1]
            result=subprocess.run([binary],input=json.dumps(data),text=True,capture_output=True,check=True)
            print(result.stdout.strip());return
        op=data["op"]
        tools=[ToolSpec(**tool) for tool in data.get("tools",[])]
        if op=="encode":result=json.dumps(data["value"])
        elif op=="text":result=[call.model_dump(mode="json") for call in _calls_from_text(data.get("content"),tools)]
        elif op=="wire":result=OpenAICompatLLM._to_wire(Message(**data["message"]))
        elif op=="prompt":result=OpenAICompatLLM._json_in_text_messages(data.get("wire",[]),tools)
        elif op=="tls":result=tls_verify_setting(data["value"])
        elif op=="estimate":
            messages=[Message(**message) for message in data.get("messages",[])]
            result={"text":recorder._message_text(messages,tools),"tokens":recorder.estimate_tokens(recorder._message_text(messages,tools))}
        print(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(",",":")));return
    responses=list(data.get("responses",[]));requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path":self.path,"authorization":self.headers.get("Authorization"),"body":body})
            response=responses.pop(0) if responses else {"status":500,"headers":{"x-should-retry":"false"},"json":{"error":{"message":"script exhausted"}}}
            payload=response.get("text")
            if payload is None:payload=json.dumps(response.get("json",{}))
            payload=payload.encode();self.send_response(response.get("status",200))
            self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(payload)));self.send_header("Connection","close")
            for key,value in response.get("headers",{}).items():self.send_header(key,str(value))
            self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    data["base_url"]="http://127.0.0.1:"+str(server.server_port)+"/v1"
    try:
        if "--native" in sys.argv:
            binary=sys.argv[sys.argv.index("--native")+1]
            process=subprocess.run([binary],input=json.dumps(data),text=True,capture_output=True,check=True)
            result=json.loads(process.stdout)
        else:
            records=[]
            class Sink:
                def write(self,record):
                    record=dataclasses.asdict(record);record.pop("ts");record.pop("latency_ms");records.append(record)
            recorder.set_sink(Sink())
            client=OpenAICompatLLM(data["base_url"],"test-model","test-key",timeout=3)
            results=[]
            for operation in data.get("calls",[]):
                try:
                    response=client.chat([Message(**message) for message in operation.get("messages",[])],[ToolSpec(**tool) for tool in operation.get("tools",[])],temperature=operation.get("temperature",0.0),max_tokens=operation.get("max_tokens"))
                    results.append(response.model_dump(mode="json"))
                except Exception as error:
                    results.append({"error":type(error).__name__,"status":getattr(error,"status_code",None),"message":str(error) if hasattr(error,"status_code") else None})
            result={"results":results,"records":records}
        result["requests"]=requests
        print(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(",",":")))
    finally:server.shutdown();server.server_close();thread.join()


if __name__=="__main__":main()
