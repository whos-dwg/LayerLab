'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../src/dwg-layer-standardizer/ui/workbench-core.js');
const rows = [
  {name:'0',protected:true},
  {name:'WALL',types:{LINE:12},block_names:{Office:2}},
  {name:'NOTE',types:{TEXT:3}},
  {name:'UNKNOWN',types:{CIRCLE:1}},
];
const suggestions = new Map([['WALL',{target:'A-wall'}],['NOTE',{target:'A-text'}]]);

function reviewFixture() {
  const inventory={job_id:'f83689bd-a231-4a4a-b601-329990d9ee09',filename:'example.dxf',sha256:'source',layers:rows};
  const standard={name:'test standard',version:'1',layers:[{name:'A-wall',aci:7},{name:'A-text',aci:2}]};
  const actions=C.createActions(rows);actions.set('WALL',{source:'WALL',decision:'map',target:'A-wall',reason:'human_review'});
  const state={inventory,standard,actions,drafts:new Map([['NOTE','A-text']]),suggestions,
    suggestionEngine:'rules',focused:'NOTE',selected:new Set(['NOTE'])};
  const identity={jobId:inventory.job_id,inventory:'inventory fingerprint',standard:'standard fingerprint'};
  return {inventory,standard,state,identity,record:C.reviewSnapshot(state,identity)};
}

test('review restore preserves approval, pending drafts and protected layers separately',()=>{
  const {inventory,standard,record,identity}=reviewFixture();
  const restored=C.restoreReview(JSON.parse(JSON.stringify(record)),inventory,standard,identity);
  assert.equal(restored.actions.get('WALL').decision,'map');
  assert.equal(restored.actions.get('NOTE').decision,'pending');
  assert.equal(restored.drafts.get('NOTE'),'A-text');
  assert.equal(restored.actions.get('0').reason,'system_protected');
  assert.equal(C.summary(rows,restored.actions,restored.suggestions).manual,1);
  assert.deepEqual([...restored.selected],['NOTE']);
  const legacy=JSON.parse(JSON.stringify(record));delete legacy.standardLabel;legacy.standardName='Previous company name';
  assert.equal(C.restoreReview(legacy,inventory,standard,identity).standardLabel,'default');
});

test('review restore refuses changed source or standard instead of retaining old approval',()=>{
  const {inventory,standard,record,identity}=reviewFixture();
  assert.throws(()=>C.restoreReview(record,inventory,standard,{...identity,inventory:'changed'}),/來源/);
  assert.throws(()=>C.restoreReview(record,inventory,standard,{...identity,standard:'changed'}),/標準/);
  assert.throws(()=>C.restoreReview(record,{...inventory,job_id:'other'},standard,identity),/來源/);
});

test('corrupt or stale saved mappings cannot partially restore human approvals',()=>{
  const {inventory,standard,record,identity}=reviewFixture();
  const changed=()=>JSON.parse(JSON.stringify(record));
  let bad=changed();bad.actions[1].target='not-in-standard';
  assert.throws(()=>C.restoreReview(bad,inventory,standard,identity),/無效/);
  bad=changed();bad.actions[0]={source:'0',decision:'map',target:'A-wall'};
  assert.throws(()=>C.restoreReview(bad,inventory,standard,identity),/無效/);
  bad=changed();bad.actions.push(bad.actions[1]);
  assert.throws(()=>C.restoreReview(bad,inventory,standard,identity),/不完整/);
  bad=changed();bad.drafts.push(['UNKNOWN','removed-target']);
  assert.throws(()=>C.restoreReview(bad,inventory,standard,identity),/無效/);
  assert.equal(record.actions[1].target,'A-wall');
});

test('SHA-256 draft identity ignores JSON property order but binds geometry and standard metadata',async()=>{
  const a={name:'drawing',layers:[{name:'WALL',preview:[{points:[[0,1],[2,3]],kind:'line'}]}]};
  const b={layers:[{preview:[{kind:'line',points:[[0,1],[2,3]]}],name:'WALL'}],name:'drawing'};
  assert.equal(await C.fingerprint(a),await C.fingerprint(b));
  b.layers[0].preview[0].points[0][0]=99;
  assert.notEqual(await C.fingerprint(a),await C.fingerprint(b));
  assert.notEqual(await C.fingerprint({version:'1',layers:[]}),await C.fingerprint({version:'2',layers:[]}));
});
test('system protection never counts as human approval or pending work', () => {
  const actions=C.createActions(rows);
  actions.set('NOTE',{source:'NOTE',decision:'keep',target:null});
  assert.deepEqual(C.summary(rows,actions,suggestions),{total:4,manual:1,protected:1,suggested:1,unknown:1,pending:2,reviewable:3});
  assert.equal(C.humanApproved(rows[0],actions),false);
  assert.equal(C.approved(rows[0],actions),true);
});
test('approved filter excludes protected rows; pending separates suggestions and unknowns', () => {
  const actions=C.createActions(rows);
  actions.set('NOTE',{source:'NOTE',decision:'map',target:'A-text'});
  const names=filter=>C.filterRows(rows,actions,suggestions,filter,'').map(row=>row.name);
  assert.deepEqual(names('approved'),['NOTE']);
  assert.deepEqual(names('pending'),['WALL','UNKNOWN']);
  assert.deepEqual(names('suggested'),['WALL']);
  assert.deepEqual(names('unknown'),['UNKNOWN']);
  assert.deepEqual(names('protected'),['0']);
});
test('all-selected state ignores protected rows and supports a mixed selection', () => {
  assert.deepEqual(C.selectionState(rows,new Set(['WALL'])),{checked:false,indeterminate:true,disabled:false});
  assert.deepEqual(C.selectionState(rows,new Set(['WALL','NOTE','UNKNOWN'])),{checked:true,indeterminate:false,disabled:false});
  assert.deepEqual(C.selectionState([rows[0]],new Set()),{checked:false,indeterminate:false,disabled:true});
});
test('search matches block evidence and trims case-insensitive queries', () => {
  assert.deepEqual(C.filterRows(rows,C.createActions(rows),suggestions,'all',' OFFICE ').map(row=>row.name),['WALL']);
  assert.deepEqual(C.filterRows(rows,C.createActions(rows),suggestions,'unknown','line'),[]);
});
test('standard migration preserves only explicit keeps and unchanged target definitions', () => {
  const actions=C.createActions(rows);
  actions.set('WALL',{source:'WALL',decision:'map',target:'A-wall'});
  actions.set('NOTE',{source:'NOTE',decision:'keep',target:null});
  actions.set('UNKNOWN',{source:'UNKNOWN',decision:'map',target:'A-text'});
  const original={layers:[{name:'A-wall',aci:7,lineweight:25},{name:'A-text',aci:2}]};
  const replacement={layers:[{lineweight:25,aci:7,name:'A-wall',plot:true,aliases:['new alias']},{name:'A-text',aci:3}]};
  const result=C.compatibleActions(rows,actions,original,replacement);
  assert.equal(result.preserved,2);
  assert.equal(result.actions.get('WALL').decision,'map');
  assert.equal(result.actions.get('NOTE').decision,'keep');
  assert.equal(result.actions.get('UNKNOWN').decision,'pending');
  assert.equal(result.actions.get('0').reason,'system_protected');
  assert.equal(actions.get('UNKNOWN').decision,'map','migration must not mutate the undo snapshot');
});
test('removed targets return to pending instead of silently approving stale mappings', () => {
  const actions=C.createActions(rows);
  actions.set('WALL',{source:'WALL',decision:'map',target:'A-wall'});
  const result=C.compatibleActions(rows,actions,{layers:[{name:'A-wall'}]},{layers:[]});
  assert.equal(result.preserved,0);
  assert.equal(result.actions.get('WALL').decision,'pending');
});
test('invalid or unsupported preview geometry has an explicit empty result', () => {
  assert.equal(C.geometry({kind:'text'}),null);
  assert.equal(C.geometry({kind:'line',points:[[0,0],[NaN,1]]}),null);
  assert.equal(C.geometry({kind:'polyline',points:[[1,2]]}),null);
  assert.equal(C.bounds([]),null);
});
test('CAD bounds mirror Y and use bounded curve tessellation', () => {
  const line=C.geometry({kind:'line',points:[[1000,2000],[1100,2050]]});
  assert.deepEqual(C.bounds([line]),[992,-2058,116,66]);
  const circle=C.geometry({kind:'circle',center:[0,0],radius:10});
  assert.equal(circle.closed,true);
  assert.ok(circle.points.length<=73);
  const arc=C.geometry({kind:'arc',center:[0,0],radius:10,start:350,end:10});
  assert.ok(arc.points.every(p=>Number.isFinite(p[0])&&Number.isFinite(p[1])));
});
test('staging revokes approval, protects system layers, and preserves the undo input',()=>{
  const actions=C.createActions(rows),drafts=new Map(),standard={layers:[{name:'A-wall'},{name:'A-text'}]};
  actions.set('WALL',{source:'WALL',decision:'map',target:'A-wall'});
  const result=C.stageMapping(rows,actions,drafts,['0','WALL','WALL'],'A-text',standard);
  assert.equal(result.count,1);assert.equal(result.actions.get('WALL').decision,'pending');
  assert.equal(result.drafts.get('WALL'),'A-text');assert.equal(result.actions.get('0').reason,'system_protected');
  assert.equal(actions.get('WALL').decision,'map');assert.equal(drafts.size,0);
});
test('batch confirmation uses each source target and rejects an incomplete batch atomically',()=>{
  const actions=C.createActions(rows),drafts=new Map(),standard={layers:[{name:'A-wall'},{name:'A-text'}]};
  const rejected=C.confirmMappings(rows,actions,suggestions,drafts,['WALL','UNKNOWN'],standard);
  assert.equal(rejected.ok,false);assert.equal(actions.get('WALL').decision,'pending');
  const accepted=C.confirmMappings(rows,actions,suggestions,drafts,['0','WALL','NOTE'],standard);
  assert.equal(accepted.ok,true);assert.equal(accepted.count,2);
  assert.equal(accepted.actions.get('WALL').target,'A-wall');assert.equal(accepted.actions.get('NOTE').target,'A-text');
  assert.equal(accepted.actions.get('0').decision,'keep');
});
test('manual staging takes precedence over a new model suggestion and stale targets cannot be approved',()=>{
  const actions=C.createActions(rows),drafts=new Map([['WALL','A-text']]),standard={layers:[{name:'A-text'}]};
  assert.equal(C.draftTarget(rows[1],actions,suggestions,drafts,standard),'A-text');
  assert.equal(C.stageMapping(rows,actions,drafts,['WALL'],'missing',standard).ok,false);
  assert.equal(C.confirmMappings(rows,actions,suggestions,new Map(),['WALL'],standard).ok,false);
  const kept=C.confirmMappings(rows,actions,suggestions,drafts,['WALL'],standard,true);
  assert.equal(kept.actions.get('WALL').decision,'keep');assert.equal(kept.actions.get('WALL').target,null);
});
test('large batches span source pages and map across the complete target catalog',()=>{
  const many=Array.from({length:300},(_,i)=>({name:'SOURCE-'+i,protected:i<2}));
  const standard={layers:Array.from({length:160},(_,i)=>({name:'TARGET-'+i}))};
  const selected=many.map(row=>row.name),actions=C.createActions(many);
  const staged=C.stageMapping(many,actions,new Map(),selected,'TARGET-159',standard);
  assert.equal(staged.count,298);assert.equal(C.summary(many,staged.actions,new Map()).manual,0);
  const confirmed=C.confirmMappings(many,staged.actions,new Map(),staged.drafts,selected,standard);
  assert.equal(confirmed.count,298);assert.equal(C.summary(many,confirmed.actions,new Map()).pending,0);
});
