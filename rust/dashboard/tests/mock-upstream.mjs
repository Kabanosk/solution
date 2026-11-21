// Local browser-test fixture server. Never used by the product runtime.
import fs from 'node:fs';import path from 'node:path';import http from 'node:http';import {pathToFileURL,fileURLToPath} from 'node:url';
const crate=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');const repo=path.resolve(crate,'../..');const fixtures=path.join(crate,'.browser-fixtures');fs.mkdirSync(fixtures,{recursive:true});
fs.writeFileSync(path.join(fixtures,'preview-data.ts'),fs.readFileSync(path.join(repo,'dashboard/lib/preview-data.ts'),'utf8').replace('import "server-only";','').replace('from "./finance-model"','from "./finance-model.ts"'));
fs.copyFileSync(path.join(repo,'dashboard/lib/finance-model.ts'),path.join(fixtures,'finance-model.ts'));
const {previewLlmUsage}=await import(pathToFileURL(path.join(repo,'dashboard/lib/llm-usage-model.ts')).href);
const {getPreviewData,getPreviewTask,getPreviewRecords}=await import(pathToFileURL(path.join(fixtures,'preview-data.ts')).href);
const data=getPreviewData();data.workspace={slug:'browser-fixture',name:'Pizzeria Amalfi · browser fixture',userId:'fixture-owner',userName:'Vincenzo Amalfi',role:'owner',googleAvailable:false,organizations:[{slug:'browser-fixture',name:'Pizzeria Amalfi · browser fixture'}]};
data.scout.tasks.push({...data.scout.tasks.find(t=>t.id==='SC-155'),id:'SC-FIXTURE-WHATIF',title:'What if: new oven €30,000.00 in March 2027'});
let strategy={provider:null,model:null,companyContext:"",systemPrompt:"Find current world news relevant to our company, explain likely implications and suggest next steps.",enabled:false,nextRunAt:null};
let guardrails={enabled:true};
let setup={version:1,status:"pending",revision:1,network:null,steps:["bank","invoicing","ksef","email","slack","network"].map(id=>({id,state:"not_started",provider:null,message:null,display:{}}))};
const server=http.createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');const chunks=[];for await(const chunk of req)chunks.push(chunk);let body={};try{body=JSON.parse(Buffer.concat(chunks).toString())}catch{}
 const send=(status,value)=>{res.statusCode=status;res.end(JSON.stringify(value))};
 if(req.url==='/api/dashboard')return req.headers['x-scout-organization']==='signed-out'?send(401,{error:'fixture signed out'}):send(200,data);
 if(req.url==='/api/auth/find')return send(200,{slug:body.organization,name:body.organization==='signed-out'?'Signed-out browser fixture':data.workspace.name});
 if(req.url==='/api/auth/login')return send(401,{error:'Email, password or organization access is incorrect.'});
 if(req.url==='/api/auth/register')return send(200,{redirect:'/browser-fixture'});
 if(req.url==='/api/onboarding'){
 if(req.method==='GET')return send(200,setup);
 if(body.revision!==setup.revision)return send(409,{error:'Setup was changed elsewhere.'});
 setup.revision++;if(body.action==='step'){const step=setup.steps.find(s=>s.id===body.step);if(step)step.state=body.state;}if(body.action==='network'){setup.network=body.network;setup.steps[5].state=body.network.required?'needs_configuration':'connected'}if(body.action==='complete')setup.status='completed';if(body.action==='skip')setup.status='dismissed';return send(200,setup);}
 if(req.url==='/api/onboarding/handoff')return send(200,{setupUrl:'http://127.0.0.1:3100/browser-fixture#setup',markdown:'# Fictional browser fixture setup',manifest:{version:1,fixture:true},prompt:'Complete the fictional browser fixture setup.',claudeUrl:null});
 if(req.url?.startsWith('/api/onboarding/connect/')){const provider=req.url.split('/').at(-1);const map={bunq:'bank',builtin:'invoicing',ksef:'ksef',google:'email',slack:'slack'};const step=setup.steps.find(s=>s.id===map[provider]);if(step){step.state=req.method==='DELETE'?'not_started':provider==='bunq'?'sandbox_connected':'connected';step.provider=provider;step.message='Fictional browser fixture connection';if(provider==='slack')step.display={authorized:true,channel_ids:'C0123456789'};}setup.revision++;return send(200,setup);}
 if(req.url==='/api/onboarding/slack/channels'){setup.steps[4].display.channel_ids=(body.channels||[]).join(', ');setup.revision++;return send(200,setup);}

 if(req.url==='/api/chat'&&req.method==='GET')return send(200,{models:[{id:'modelcar-granite-4-0-h-tiny',name:'Granite 4.0 H Tiny'}],external:null});
 if(req.url==='/api/chat'&&req.method==='POST'){res.setHeader('Content-Type','application/x-ndjson');res.write(JSON.stringify({type:'step',text:'Checking the browser fixture'})+'\n');setTimeout(()=>{res.write(JSON.stringify({type:'delta',text:'**Fixture response:** invoices checked. Zażółć 😀'})+'\n');setTimeout(()=>{res.end(JSON.stringify({type:'done',truncated:false,task_id:'SC-158'})+'\n')},50)},50);return}
 if(req.url?.startsWith('/api/llm-usage?'))return send(200,previewLlmUsage(Number(new URL(req.url,'http://fixture').searchParams.get('days'))));
 if(req.url==='/api/strategy/settings'){if(req.method==='PUT'){if(body.external){strategy.provider=body.external.provider;strategy.model=body.external.model;}else strategy={...strategy,...body};}return send(200,strategy)}
 if(req.url==='/api/strategy/review')return send(200,{running:false,taskId:'SC-FIXTURE-REVIEW'});
 if(req.url==='/api/demo')return send(200,{ok:true,fixture:true});
 if(req.url==='/api/chat/ack')return send(200,{ok:true});
 if(req.url?.startsWith('/api/tasks/')){const detail=getPreviewTask(decodeURIComponent(req.url.slice('/api/tasks/'.length)));return send(detail?200:404,detail||{error:'Task not found'})}
 if(req.url?.startsWith('/api/records/')){const kind=req.url.slice('/api/records/'.length);return send(200,getPreviewRecords()[kind]??[])}
 if(req.url==='/api/guardrails'){if(req.method==='PUT')guardrails={enabled:body.enabled};return send(200,guardrails)}
 if(req.url?.startsWith('/api/invoices/'))return send(200,{invoice:{number:'FLV-42026',supplier:'Fattoria La Vino',total:1840,currency:'EUR',issueDate:'2026-11-16',dueDate:'2026-11-18',category:'beverages',source:'db'},lines:[{position:1,description:'Browser fixture wine',quantity:20,unitPrice:92,amount:1840}],document:{taskId:'SC-154',textExcerpt:'Fictional browser-test invoice. Zażółć 😀'}});
 if(req.url==='/api/approve')return send(200,{status:body.action==='approve'?'approved':'rejected'});
 send(404,{error:'Unimplemented fixture endpoint'});
});server.listen(3101,'127.0.0.1',()=>console.log('Browser fixture upstream on 127.0.0.1:3101'));process.on('SIGTERM',()=>server.close());
