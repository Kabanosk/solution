"""Compare real HTTP behavior, duplicate headers and first SSE delivery against the source."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx

root=Path(__file__).resolve().parents[2]
python=root/"backend/.venv/bin/python"
binary=root/"rust/app/target/debug/reference-api"
headers={"Authorization":"Bearer scout_rust_migration_local_test_session_0123456789","X-Scout-Organization":"rust-migration-fixture"}
processes=[]

def start(command):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    env=dict(os.environ,PORT=str(port))
    env["PATH"]=str(python.parent)+os.pathsep+env.get("PATH","")
    proc=subprocess.Popen(command,cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    processes.append(proc)
    end=time.monotonic()+60
    while time.monotonic()<end:
        if proc.poll() is not None: raise RuntimeError(proc.stderr.read().decode())
        try:
            with socket.create_connection(("127.0.0.1",port),timeout=.1):return "http://127.0.0.1:"+str(port)
        except OSError:time.sleep(.05)
    raise TimeoutError("server did not start")

try:
    source=start([str(python),str(root/"rust/scripts/reference-api.py")])
    native=start([str(binary)])
    cases=[]
    for text in ["1.0","+10"," 10 ","1_0",".0","1.","1__0","1.0_0","1.2","١٢","0","-0","201","99999999999999999999999","1"*4301]:
        cases.append(("GET","/v1/tasks",headers,{"limit":text}))
    for path in ["/healthz","/%68ealthz","/healthz/","/v1/tasks","/v1/tasks/SC-100","/v1/tasks/%53C-100","/v1/tasks%2FSC-100","/v1/audit","/v1/transactions/booking-1/invoice","/v1/connections"]:
        cases.append(("GET",path,headers,{}))
    for method in ["GET","HEAD","POST","OPTIONS"]:
        cases.append((method,"/healthz",{"Origin":"https://dashboard.example.invalid","Access-Control-Request-Method":"GET"},{}))
    cases.append(("GET","/v1/tasks",list(headers.items())+[("Authorization",headers["Authorization"])],{}))
    cases.append(("GET","/v1/tasks",list(headers.items())+[("X-Scout-Organization","rust-migration-fixture")],{}))
    selected=["content-type","vary","allow","www-authenticate","access-control-allow-origin","access-control-expose-headers"]
    mismatches=[]
    with httpx.Client(timeout=10) as client:
        for method,path,sent,params in cases:
            old=client.request(method,source+path,headers=sent,params=params)
            new=client.request(method,native+path,headers=sent,params=params)
            old_view=(old.status_code,{key:old.headers.get(key) for key in selected},old.content)
            new_view=(new.status_code,{key:new.headers.get(key) for key in selected},new.content)
            if old_view!=new_view:
                mismatches.append((method,path,params,old_view,new_view))
        for address in [source,native]:
            with client.stream("GET",address+"/v1/tasks/stream",headers=headers,timeout=3) as stream:
                if stream.status_code!=200 or next(stream.iter_lines())!="retry: 3000":
                    raise AssertionError("SSE response did not deliver first event")
    if mismatches:
        for row in mismatches: print(repr(row))
        raise SystemExit(1)
    print(f"{len(cases)}/{len(cases)} HTTP comparisons passed; source/native SSE delivered immediately")
finally:
    for proc in reversed(processes):
        proc.terminate()
    for proc in reversed(processes):
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
