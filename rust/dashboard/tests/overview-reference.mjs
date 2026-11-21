// Characterization only. The original source remains unchanged and is never shipped.
import * as model from '../../../dashboard/lib/overview-model.ts';
import fs from 'node:fs';
const root=new URL('../',import.meta.url);const cases=[];
const add=(op,...args)=>cases.push({op,args});
for(const [low,high]of [[0,100],[-3400,78000],[3,3],[-1,0],[0.0002,0.0005],[1e8,4e8]])add('niceTicks',low,high);
for(const x of [-2,0,0.999,1,1.499,1.5,2,9.99,10,12.34,1.25,1.35,2.55,9.95,10.5,12.5,-1.25,-0.01,1e21]){add('bufferStatus',x);add('formatCoverage',x)}
for(const balance of [0,99,100,149,150,1000])for(const lowest of [50,100,150])add('gaugeStatus',balance,lowest,100);
const tasks=[];let id=0;for(const status of ['todo','in_progress','waiting','escalated','done','cancelled'])for(const level of ['AUTO','NOTIFY','APPROVAL'])for(const resolvedVia of [null,'approval','slack_dm','files']){const task={id:`SC-${++id}`,loop:'invoicing',title:'Invoice',status,level,resolvedVia,createdAt:'2026-11-01',updatedAt:'2026-11-02',closedAt:null,outcome:status==='done'?'Done · Invoice filed':null};tasks.push(task);add('scoutOutcome',task)}
add('scoutRing',tasks,'2026-11-21');add('scoutRing',[...tasks,{...tasks[0],loop:'chat'},{...tasks[4],createdAt:'2026-10-01'}],'2026-11-21');add('notableActions',tasks,3);
for(const outcome of ['Resolved · €1,840.00 filed as FLV-42026 (SIMULATED)','Done · Credit line of €30,000.00 (SIMULATED)','Closed · Sent invoice. Another sentence.','Done · Matched (supplier reference) and filed invoice','x'.repeat(73),'Zażółć 😀 invoice. Closed.'])for(const status of ['done','waiting'])add('actionSummary',{outcome,title:'Fallback',simulated:false,status});
for(const title of ['What if: new oven €30,000.00 in March 2027','What if: supply in november 2028','What if: oven in NotMonth 2027','What if: €1.00 in May 2027','What if: Fridge in April 2027   ']){const list=[{id:'SC-1',loop:'liquidity',status:'done',amount:-30000,title,createdAt:'2026-11-01'}];add('latestWhatIf',list)}
add('withWhatIf',[{day:0,date:'2026-11-21',balance:102.32},{day:1,date:'2027-03-01',balance:10000}],{amount:30000,on:'2027-03-01'});add('withWhatIf',[],null);
fs.writeFileSync(new URL('tests/overview-inputs.jsonl',root),cases.map(c=>JSON.stringify(c)).join('\n')+'\n');fs.writeFileSync(new URL('tests/overview-expected.jsonl',root),cases.map(c=>JSON.stringify(model[c.op](...c.args))).join('\n')+'\n');console.log(`${cases.length} original TypeScript overview cases recorded`);
