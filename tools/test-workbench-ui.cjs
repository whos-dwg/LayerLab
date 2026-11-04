/* Run after installing jsdom@26.1.0 into temp/layerlab-dom-tests. No live browser or CAD writes. */
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const {JSDOM,VirtualConsole}=require(process.env.LAYERLAB_JSDOM_MODULE||'../temp/layerlab-dom-tests/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),ui=path.join(root,'src/dwg-layer-standardizer/ui');
// Entirely synthetic fixtures; no imported company standard is read or reproduced.
const actualStandard={schema:'cad-layer-standard/v1',name:'Synthetic UI Fixture',version:'0.0.1',layers:[
  {name:'TEST-DOOR',aci:7,linetype:'Continuous',lineweight:-3,category:'door'},
  {name:'TEST-PLANT',aci:3,linetype:'Continuous',lineweight:9,category:'furniture'},
  {name:'TEST-FURN',aci:4,linetype:'HIDDEN',lineweight:18,category:'furniture'},
  ...Array.from({length:44},(_,i)=>({name:'TEST-TARGET-'+String(i).padStart(3,'0'),aci:7,linetype:'Continuous',lineweight:25,category:'fixture'}))
]};
const JOB='f83689bd-a231-4a4a-b601-329990d9ee09';
const inventory={schema:'cad-layer-inventory/v1',job_id:JOB,filename:'review-fixture.dxf',filetype:'DXF',can_convert:true,sha256:'fixture-original-source',total_entities:70,warnings:[],
  layers:Array.from({length:71},(_,i)=>({name:i===0?'0':i===70?'UNKNOWN':i===1?'A-DOOR':'SOURCE-'+String(i).padStart(3,'0'),protected:i===0,protected_reason:i===0?'system layer':null,
    total:i===0?0:1,types:i===0?{}:{LINE:1},block_names:{},counts:{model:1,paper:0,block:0},color:{aci:7},linetype:'CONTINUOUS',lineweight:-3,
    preview:i===0?[]:[{kind:'line',points:[[i,0],[i+1,10]]}]}))};
const suggestions=inventory.layers.filter(r=>!r.protected).map(r=>({source:r.name,target:r.name==='UNKNOWN'?null:'TEST-DOOR',reason:'Fixture evidence for '+r.name}));
const copy=value=>JSON.parse(JSON.stringify(value));
async function until(check,message='condition',timeout=2500) {
  const deadline=Date.now()+timeout;
  while(!check()){if(Date.now()>deadline)throw Error('Timed out: '+message);await new Promise(r=>setTimeout(r,15));}
}
async function boot(options={}) {
  const errors=[],calls=[],virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(ui,'index.html'),'utf8'),{url:'http://127.0.0.1:8765/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const w=dom.window,standard=copy(options.standard||actualStandard);
  Object.defineProperty(w,'crypto',{value:webcrypto});w.TextEncoder=TextEncoder;
  w.CSS={escape:s=>String(s).replace(/["\\]/g,'\\$&')};
  w.matchMedia=()=>({matches:true,addEventListener(){},removeEventListener(){}});
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  if(options.storage)for(const [key,value]of Object.entries(options.storage))w.localStorage.setItem(key,value);
  if(options.storageFailure)w.Storage.prototype.setItem=function(){throw new w.DOMException('Storage quota exceeded','QuotaExceededError');};
  w.fetch=async(url,init={})=>{
    calls.push([url,init.method||'GET']);let value;
    if(url==='/api/health')value={version:'test',semantic_suggest:{available:false}};
    else if(url==='/api/standard')value=standard;
    else if(url==='/api/suggest')value={standard:JSON.parse(init.body).standard,suggestions};
    else if(url==='/api/demo')value={inventory:options.demoInventory||inventory,standard,suggestions};
    else if(url==='/api/jobs/'+JOB+'/files/inventory.json'){
      if(options.expiredJob)throw Error('Missing job');value=options.serverInventory||inventory;
    } else if(url==='/api/jobs/'+JOB+'/convert'&&options.convertResult)value=options.convertResult;
    else throw Error('Unexpected API request: '+url);
    return {ok:true,json:async()=>copy(value)};
  };
  for(const file of ['workbench-core.js','preview-hover.js','app.js'])w.eval(fs.readFileSync(path.join(ui,file),'utf8'));
  await until(()=>!w.document.querySelector('#demo-btn').disabled,'initialize');
  const $=selector=>w.document.querySelector(selector);
  return {w,dom,$,calls,errors,storage:()=>Object.fromEntries(Array.from({length:w.localStorage.length},(_,i)=>{const k=w.localStorage.key(i);return [k,w.localStorage.getItem(k)];})),
    load:async()=>{$('#demo-btn').click();await until(()=>$('#stat-layers').textContent==='71','demo import');},
    settle:async()=>until(()=>$('#review-save-status').textContent.includes('草稿已保存'),'autosave'),close:()=>dom.window.close()};
}
function clickTarget(p,name){p.$('[data-target="'+name+'"]').click();}
function currentSource(p,name){p.$('[data-source="'+name+'"] .source-button').click();}
async function saveFixtureReview(p) {
  await p.load();
  const last=actualStandard.layers.at(-1).name;
  clickTarget(p,last);p.$('#select-all').click();p.$('#next-page').click();p.$('#select-all').click();
  assert.equal(p.$('#selected-count').textContent,'59');
  assert.match(p.$('#hidden-selected').textContent,/29/);
  assert.equal(p.$('#bulk-row').hidden,false);assert.equal(p.$('#current-actions').hidden,true);
  p.$('#bulk-stage').click();p.$('#bulk-approve').click();
  assert.equal(p.$('#stat-approved').textContent,'59');assert.equal(p.$('#bulk-row').hidden,true);
  p.$('#next-page').click();currentSource(p,'UNKNOWN');clickTarget(p,'TEST-DOOR');p.$('#current-stage').click();
  assert.match(p.$('#current-target').textContent,/暫定映射/);
  await p.settle();
}

test('all 47 synthetic targets are rendered without pagination or implicit first target',async()=>{
  const p=await boot();try {
    assert.equal(p.w.document.querySelectorAll('[data-target]').length,47);
    assert.equal(p.$('#target-next'),null);assert.match(p.$('#target-count').textContent,/全部 47/);
    assert.equal(p.w.document.querySelector('[data-target][aria-pressed="true"]'),null);
    await p.load();assert.equal(p.$('#current-stage').disabled,true);
    assert.match(p.$('#current-target-name').textContent,/尚未選擇/);
    clickTarget(p,'TEST-DOOR');assert.equal(p.$('#current-stage').disabled,false);
    assert.equal(p.$('[data-target="TEST-DOOR"]').getAttribute('aria-pressed'),'true');
    const search=p.$('#target-search');search.value='NO SUCH TARGET';search.dispatchEvent(new p.w.Event('input'));
    assert.equal(p.w.document.querySelectorAll('[data-target]').length,0);assert.equal(p.$('#clear-target-filters').hidden,false);
    p.$('#clear-target-filters').click();assert.equal(p.w.document.querySelectorAll('[data-target]').length,47);
    assert.equal(p.$('#decision-reason').closest('details'),null);assert.match(p.$('#decision-reason').textContent,/Fixture evidence/);
    assert.ok(p.$('[data-source="A-DOOR"] .source-button').getAttribute('aria-describedby'));
    assert.deepEqual(p.errors,[]);
  } finally {p.close();}
});

test('precision mapping evidence follows suggestions, staging, approval, keep, undo and protected sources',async()=>{
  const p=await boot();try {
    assert.equal(p.$('#relation-name').textContent,'—');await p.load();
    assert.equal(p.$('#relation-name').textContent,'TEST-DOOR');
    assert.match(p.$('#relation-label').textContent,/建議.*尚未核准/);
    assert.match(p.$('#relation-properties').textContent,/ACI 7.*Continuous.*Default/);
    assert.equal(p.$('[data-target="TEST-PLANT"] .column-weight').textContent,'0.09 mm');
    assert.ok(p.$('.assignment-space').firstElementChild.classList.contains('inspector'));
    assert.equal(p.$('#bulk-row').closest('.inspector'),p.$('.inspector'));
    clickTarget(p,'TEST-FURN');p.$('#current-stage').click();
    assert.equal(p.$('#relation-name').textContent,'TEST-FURN');assert.match(p.$('#relation-label').textContent,/暫定.*尚未核准/);
    assert.match(p.$('#relation-properties').textContent,/HIDDEN/);
    p.$('#current-approve').click();assert.equal(p.$('#relation-label').textContent,'已人工確認');
    p.$('#current-keep').click();assert.equal(p.$('#relation-label').textContent,'已確認保留原圖層');
    assert.equal(p.$('#relation-name').textContent,'A-DOOR');assert.equal(p.$('#relation-properties').textContent,'');
    p.$('#undo-btn').click();assert.equal(p.$('#relation-name').textContent,'TEST-FURN');
    currentSource(p,'0');assert.match(p.$('#relation-label').textContent,/系統保護/);assert.equal(p.$('#relation-name').textContent,'0');
    p.$('#next-page').click();p.$('#next-page').click();currentSource(p,'UNKNOWN');
    assert.equal(p.$('#relation-name').textContent,'—');assert.equal(p.$('#relation-properties').textContent,'');
    assert.equal(p.$('.preview-enlarge-hint'),null);
    assert.deepEqual(p.errors,[]);
  }finally{p.close();}
});

test('cross-page batch approval and an unapproved draft survive a fresh document only after explicit restore',async()=>{
  const first=await boot();let storage;
  try {await saveFixtureReview(first);storage=first.storage();assert.deepEqual(first.errors,[]);}finally{first.close();}
  const p=await boot({storage});try {
    assert.equal(p.$('#stat-approved').textContent,'0');assert.equal(p.$('#restore-review').hidden,false);
    p.$('#restore-review').click();await until(()=>p.$('#stat-approved').textContent==='59','restore approvals');
    assert.match(p.$('#current-target').textContent,/UNKNOWN.*TEST-DOOR.*尚未核准/);
    assert.equal(p.$('#current-stage').disabled,true,'prepared target must remain unselected after restore');
    assert.equal(p.$('#current-approve').disabled,false,'restored pending draft can still be reviewed');
    assert.equal(p.calls.some(([url])=>url.includes('/convert')||url.includes('/semantic-suggest')),false);
    assert.deepEqual(p.errors,[]);
  }finally{p.close();}
});

test('restore refuses a changed standard, changed inventory and an expired server job without losing the saved record',async()=>{
  const seed=await boot();let storage;try{await saveFixtureReview(seed);storage=seed.storage();}finally{seed.close();}
  for(const options of [{standard:{...actualStandard,version:'changed'}},{serverInventory:{...inventory,sha256:'different source'}},{expiredJob:true}]) {
    const p=await boot({...options,storage});try {
      p.$('#restore-review').click();await until(()=>!p.$('#error-panel').hidden,'restore error');
      assert.equal(p.$('#stat-approved').textContent,'0');assert.equal(p.$('#stat-layers').textContent,'—');
      assert.ok(p.w.localStorage.getItem('layerlab.review.v1.'+JOB));
      assert.equal(p.calls.some(([url])=>url.includes('/convert')),false);
    }finally{p.close();}
  }
});

test('unavailable browser storage is visible and prompts before leaving unsaved review',async()=>{
  const p=await boot({storageFailure:true});try {
    await p.load();clickTarget(p,'TEST-DOOR');p.$('#current-stage').click();
    await until(()=>p.$('#review-save-status').textContent.includes('草稿未保存'),'storage failure');
    assert.equal(p.$('#export-btn').disabled,false);
    const event=new p.w.Event('beforeunload',{cancelable:true});p.w.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
    assert.equal(p.w.localStorage.getItem('layerlab.review.v1.'+JOB),null);
    assert.equal(p.$('#review-save-status').classList.contains('save-error'),true);
  }finally{p.close();}
});

test('another tab updating the same review pauses autosave until an explicit takeover',async()=>{
  const p=await boot();try {
    await p.load();await p.settle();const saved=p.w.localStorage.getItem('layerlab.review.v1.'+JOB);
    p.w.dispatchEvent(new p.w.StorageEvent('storage',{key:'layerlab.review.v1.'+JOB,newValue:saved}));
    assert.equal(p.$('#resume-save').hidden,false);p.$('#current-keep').click();
    await new Promise(r=>setTimeout(r,520));assert.equal(p.w.localStorage.getItem('layerlab.review.v1.'+JOB),saved);
    p.$('#resume-save').click();await until(()=>p.$('#decision-dialog').open,'takeover confirmation');
    p.$('#decision-dialog').returnValue='resume';p.$('#decision-dialog').close();await p.settle();
    assert.equal(JSON.parse(p.w.localStorage.getItem('layerlab.review.v1.'+JOB)).actions.find(a=>a.source==='A-DOOR').decision,'keep');
  }finally{p.close();}
});

test('loading another drawing preserves the first draft and offers both on next launch',async()=>{
  const options={},p=await boot(options);let storage;
  try {
    await p.load();p.$('#current-keep').click();await p.settle();
    const first=p.w.localStorage.getItem('layerlab.review.v1.'+JOB),otherJob=webcrypto.randomUUID();
    options.demoInventory={...copy(inventory),job_id:otherJob,filename:'second.dxf'};
    p.$('#demo-btn').click();await until(()=>!!p.w.localStorage.getItem('layerlab.review.v1.'+otherJob),'second drawing saved');
    assert.equal(p.w.localStorage.getItem('layerlab.review.v1.'+JOB),first);
    storage=p.storage();
  }finally{p.close();}
  const next=await boot({storage});try {
    assert.equal(next.$('#review-drafts').hidden,false);assert.equal(next.$('#review-drafts').options.length,2);
    next.$('#review-drafts').value=JOB;next.$('#review-drafts').dispatchEvent(new next.w.Event('change'));
    assert.match(next.$('#review-resume-note').textContent,/review-fixture/);
    next.$('#restore-review').click();await until(()=>next.$('#stat-approved').textContent==='1','selected older draft restored');
  }finally{next.close();}
});

test('default standard label becomes the imported filename and survives undo and draft restore without changing standard identity',async()=>{
  const p=await boot();let storage;
  const fileName='Architecture-2026.json';
  const verifyLabel=(view,label)=>{
    assert.equal(view.$('#side-profile strong').textContent,label);
    assert.equal(view.$('#target-standard-version').textContent,label+' · '+actualStandard.version);
    assert.equal(view.$('#standard-summary').textContent,'標準：'+label+' · '+actualStandard.version);
  };
  async function importStandard(answer) {
    Object.defineProperty(p.$('#standard-file'),'files',{configurable:true,value:[{name:fileName,text:async()=>JSON.stringify(actualStandard)}]});
    p.$('#standard-file').dispatchEvent(new p.w.Event('change'));
    await until(()=>p.$('#decision-dialog').open,'standard confirmation');
    assert.match(p.$('#dialog-message').textContent,/Architecture-2026\.json/);
    p.$('#decision-dialog').returnValue=answer;p.$('#decision-dialog').close();
    await until(()=>p.$('#workbench').getAttribute('aria-busy')==='false','standard switch finished');
  }
  try {
    verifyLabel(p,'default');await p.load();verifyLabel(p,'default');
    await importStandard('cancel');verifyLabel(p,'default');
    await importStandard('preserve');verifyLabel(p,fileName);
    p.$('#undo-btn').click();verifyLabel(p,'default');
    await importStandard('preserve');await p.settle();storage=p.storage();
    const record=JSON.parse(storage['layerlab.review.v1.'+JOB]);assert.equal(record.standardLabel,fileName);
    const core=require('../src/dwg-layer-standardizer/ui/workbench-core.js');
    assert.equal(record.identity.standard,await core.fingerprint(actualStandard));
    assert.deepEqual(p.errors,[]);
  }finally{p.close();}
  const next=await boot({storage});try {
    verifyLabel(next,'default');next.$('#restore-review').click();
    await until(()=>next.$('#target-standard-version').textContent.startsWith(fileName),'restored standard label');
    verifyLabel(next,fileName);
  }finally{next.close();}
});


test('verified DXF and DWG success messages match their download and save actions',async()=>{
  for(const format of ['DXF','DWG']) {
    const result={output:'verified.'+format.toLowerCase(),download_prefix:'/downloads/',mapping:'mapping.json',validation:'validation.json',report:{status:'PASS',entity_count_before:70,entity_count_after:70}};
    const p=await boot({demoInventory:{...copy(inventory),filetype:format},convertResult:result});
    try {
      await p.load();p.$('#select-filtered').click();p.$('#bulk-keep').click();
      assert.equal(p.$('#convert-btn').disabled,false);p.$('#convert-btn').click();
      await until(()=>!p.$('#results').hidden,'verified result');
      const message=p.$('#toast').textContent;
      if(format==='DXF') {assert.match(message,/DXF.*下載/);assert.doesNotMatch(message,/DWG/);assert.match(p.$('#results a').textContent,/verified.dxf/);}
      else {assert.match(message,/DWG 儲存位置/);assert.match(p.$('#results button').textContent,/儲存 DWG/);}
      assert.deepEqual(p.errors,[]);
    } finally {p.close();}
  }
});
