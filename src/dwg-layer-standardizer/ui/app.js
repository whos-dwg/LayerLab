/* LayerLab workbench. CAD writes remain gated by the existing verified API. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const C = window.LayerLabCore;
  const PAGE_SIZE = 30, MEMBER_PAGE_SIZE = 6, BACKGROUND_BUDGET = 1200;
  const REVIEW_PREFIX='layerlab.review.v1.';
  const fingerprintCache=new WeakMap();
  let reviewTimer=null,reviewDirty=false,reviewPaused=false,lastReview='',reviewIdentity=null,pendingReview=null;
  const SVGNS = 'http://www.w3.org/2000/svg';
  const colorACI = {1:'#e75657',2:'#f0ce5a',3:'#57c88b',4:'#6abace',5:'#668ee5',6:'#a97ade',7:'#c8d6e2',8:'#718391',9:'#a5b1ba'};
  const state = {
    inventory:null, standard:null, standardLabel:'default', actions:new Map(), suggestions:new Map(), selected:new Set(),
    focused:null, filter:'all', query:'', page:0, suggestionEngine:'rules',
    semantic:{available:false,model:null}, initialized:false, busy:false, stage:'',
    revision:0, operation:0, undo:null, retry:null, searchIndex:new Map(),
    rowByName:new Map(), geometries:new Map(), background:[], visible:[], rowNodes:new Map(),
    view:null, baseView:null, drag:null, drafts:new Map(), activeTarget:'', expandedTarget:'', targetQuery:'', category:'', assignedOnly:false, memberPage:0,
    quickAfterImport:false
  };
  let boardDrag=null,suppressBoardClickUntil=0,drawingKey='', toastTimeout, toastDeadline=0, toastRemaining=0, searchTimer;
  const rows = () => state.inventory?.layers || [];
  const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const chevron = open => '<svg class="ui-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="'+(open?'M4 6l4 4 4-4':'M6 4l4 4-4 4')+'"/></svg>';
  const pagingIcon = forward => '<svg class="ui-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="'+(forward?'M3 8h10M9 4l4 4-4 4':'M13 8H3m4-4L3 8l4 4')+'"/></svg>';
  const setText = (selector,value) => { const node=$(selector); const text=String(value); if(node.textContent!==text)node.textContent=text; };
  const displayLineweight = value => value===-3?'Default':value===-1?'ByLayer':value===-2?'ByBlock':Number.isFinite(value)?value/100+' mm':'—';
  const targetProperties = target => 'ACI '+(target.aci??7)+' · '+(target.linetype||'CONTINUOUS')+' · '+displayLineweight(target.lineweight);
  const standardDisplayName=()=>state.standard?(state.standardLabel||'default'):'未載入標準';
  function reviewStatus(text,error=false) {
    setText('#review-save-status',text);$('#review-save-status').classList.toggle('save-error',error);
  }
  function cachedFingerprint(value) {
    if(!fingerprintCache.has(value))fingerprintCache.set(value,C.fingerprint(value));
    return fingerprintCache.get(value);
  }
  async function bindingFor(inventory,standard) {
    const [inventoryHash,standardHash]=await Promise.all([cachedFingerprint(inventory),cachedFingerprint(standard)]);
    return {jobId:inventory.job_id,inventory:inventoryHash,standard:standardHash};
  }
  function flushReview() {
    if(!state.inventory||reviewPaused||reviewIdentity?.inventory!==state.inventory||reviewIdentity?.standard!==state.standard)return false;
    try {
      const snapshot=C.reviewSnapshot(state,reviewIdentity.value),serialized=JSON.stringify(snapshot);
      if(serialized===lastReview){reviewDirty=false;return true;}
      const record={...snapshot,savedAt:new Date().toISOString()};
      localStorage.setItem(REVIEW_PREFIX+record.identity.jobId,JSON.stringify(record));
      lastReview=serialized;reviewDirty=false;
      reviewStatus('草稿已保存於此瀏覽器 · '+new Date(record.savedAt).toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',second:'2-digit'}));
      return true;
    } catch {
      reviewDirty=true;reviewStatus('草稿未保存：瀏覽器儲存空間不可用。請按「匯出映射」保存。',true);return false;
    }
  }
  async function saveReview() {
    if(!state.inventory||reviewPaused)return;
    const inventory=state.inventory,standard=state.standard;
    try {
      const value=await bindingFor(inventory,standard);
      if(inventory!==state.inventory||standard!==state.standard)return;
      reviewIdentity={inventory,standard,value};flushReview();
    } catch(error) {reviewDirty=true;reviewStatus(error.message,true);}
  }
  function queueReviewSave() {
    if(!state.inventory||reviewPaused)return;
    reviewDirty=true;clearTimeout(reviewTimer);reviewTimer=setTimeout(saveReview,450);
  }
  function offerSavedReviews() {
    if(state.inventory)return;
    try {
      const records=[];
      for(let i=0;i<localStorage.length;i++) {
        const key=localStorage.key(i);if(!key?.startsWith(REVIEW_PREFIX))continue;
        try {
          const value=JSON.parse(localStorage.getItem(key));
          if(value?.schema==='layerlab-review/v1'&&/^[0-9a-f-]{36}$/i.test(value.identity?.jobId)&&
            key===REVIEW_PREFIX+value.identity.jobId&&Number.isFinite(Date.parse(value.savedAt)))records.push(value);
        } catch{}
      }
      records.sort((a,b)=>Date.parse(b.savedAt)-Date.parse(a.savedAt));
      const select=$('#review-drafts'),chosen=select.value;
      select.replaceChildren(...records.map(r=>new Option(r.filename+' · '+new Date(r.savedAt).toLocaleString('zh-TW'),r.identity.jobId)));
      if(records.some(r=>r.identity.jobId===chosen))select.value=chosen;
      pendingReview=records.find(r=>r.identity.jobId===select.value)||null;
      select.hidden=records.length<2;
      for(const id of ['restore-review','discard-review','review-resume-note'])$('#'+id).hidden=!pendingReview;
      if(pendingReview) {
        reviewStatus('找到已保存的審核草稿');
        setText('#review-resume-note',pendingReview.filename+' · 標準 '+(pendingReview.standardLabel||'default')+' · '+pendingReview.standardVersion);
      } else reviewStatus('尚未開始審核 · 載入圖面後自動保存進度');
    } catch {reviewStatus('瀏覽器無法保存草稿；審核後請使用「匯出映射」。',true);}
  }
  async function restoreSavedReview() {
    if(state.busy||!pendingReview)return;
    const record=pendingReview;
    await runOperation('正在核對來源與標準並恢復草稿',async valid=>{
      const inventory=await request('/api/jobs/'+record.identity.jobId+'/files/inventory.json').catch(()=>{
        throw new Error('找不到草稿的原始盤點工作。請在原本的工作台服務開啟，或重新匯入來源圖面；保存草稿仍保留。');
      });
      const identity=await bindingFor(inventory,state.standard);
      const restored=C.restoreReview(record,inventory,state.standard,identity);
      if(!valid())return;
      reset({inventory,standard:state.standard,suggestions:[...restored.suggestions.values()]});
      Object.assign(state,restored);state.activeTarget='';reviewIdentity={inventory,standard:state.standard,value:identity};
      invalidateResult();updateProfile();render();saveReview();toast('已恢復審核進度；暫定目標仍待確認，未執行轉換。');
    },restoreSavedReview);
  }
  const proposals = () => new Map([...state.suggestions,...[...state.drafts].map(([name,target])=>[name,{target}])]);
  const counts = () => C.summary(rows(),state.actions,proposals());
  const targetFor = row => C.draftTarget(row,state.actions,state.suggestions,state.drafts,state.standard);
  const targetCategory = target => String(target.category||target.name.split(/[-_]/)[0]||'其他');
  const rowStatus = row => row.protected?['protected','系統保護']:C.humanApproved(row,state.actions)?['approved','已審核']:targetFor(row)?['pending','待確認']:['unknown','待判讀'];
  const canConvert = () => !!state.inventory?.can_convert && counts().pending===0;
  const applicable = () => rows().filter(row=>!C.approved(row,state.actions)&&!state.drafts.has(row.name)&&!!state.suggestions.get(row.name)?.target);
  const currentRow = () => state.rowByName.get(state.focused);
  function fileLink(url,name) {
    const a=document.createElement('a');a.href=url;a.textContent=name;a.download=name;return a;
  }
  function saveButton(url,name) {
    const button=document.createElement('button');button.className='button primary result-save';button.textContent='選擇位置並儲存 DWG';
    button.addEventListener('click',async()=>{
      try {
        if(!window.showSaveFilePicker) {
          fileLink(url,name).click();
          toast('瀏覽器已開始下載；可在瀏覽器下載設定中啟用每次詢問儲存位置。');
          return;
        }
        const handle=await window.showSaveFilePicker({suggestedName:name,types:[{description:'AutoCAD DWG',accept:{'application/octet-stream':['.dwg']}}]});
        button.disabled=true;button.textContent='正在儲存…';
        const response=await fetch(url);if(!response.ok)throw new Error('無法讀取已驗證的 DWG（HTTP '+response.status+'）。');
        const writable=await handle.createWritable();
        try { await writable.write(await response.blob());await writable.close(); }
        catch(error) { await writable.abort().catch(()=>{});throw error; }
        button.textContent='已儲存至選擇的位置 ✓';toast('DWG 已儲存至你選擇的位置。');
      } catch(error) {
        if(error?.name==='AbortError')return;
        button.disabled=false;button.textContent='重新選擇儲存位置';showError('儲存 DWG 失敗',error);
      }
    });
    return button;
  }
  async function request(endpoint,options={}) {
    const response=await fetch(endpoint,options);
    const data=await response.json().catch(()=>({}));
    if(!response.ok) {
      const detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data);
      throw new Error(detail || 'HTTP '+response.status);
    }
    return data;
  }
  const jsonPost = (endpoint,body) => request(endpoint,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  function toast(message,isError=false) {
    const node=$('#toast');node.textContent=message;node.className='toast visible'+(isError?' error':'');
    clearTimeout(toastTimeout);toastRemaining=4300;resumeToast();
  }
  function resumeToast() {
    if(document.hidden || !toastRemaining)return;
    toastDeadline=Date.now()+toastRemaining;
    toastTimeout=setTimeout(()=>{$('#toast').classList.remove('visible');toastRemaining=0;},toastRemaining);
  }
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden){clearTimeout(toastTimeout);toastRemaining=Math.max(0,toastDeadline-Date.now());}
    else resumeToast();
  });
  function clearError() {
    $('#error-panel').hidden=true;state.retry=null;
    $('#operation').classList.remove('failed');
  }
  function showError(title,error,retry) {
    setText('#error-title',title);setText('#error-message',error.message||error);
    $('#error-panel').hidden=false;state.retry=retry;
    $('#retry-btn').hidden=!retry;$('#operation').classList.add('failed');
    toast(title+'：'+(error.message||error),true);
  }
  function syncControls() {
    const c=counts(),busy=state.busy,loaded=!!state.inventory,ready=state.initialized;
    $('#workbench').setAttribute('aria-busy',String(busy));
    for(const id of ['demo-btn','import-btn','input-file']) $('#'+id).disabled=busy||!ready;
    $('#quick-flow-btn').disabled=busy||!ready;
    for(const id of ['restore-review','discard-review','review-drafts','resume-save'])$('#'+id).disabled=busy||!ready;
    for(const id of ['standard-btn','standard-file','model-suggest-btn','export-btn']) $('#'+id).disabled=busy||!loaded;
    $('#model-suggest-btn').disabled=busy||!loaded||!state.semantic.available;
    $('#convert-btn').disabled=busy||!canConvert();
    $('#undo-btn').disabled=busy||!state.undo;
    $('#bulk-stage').disabled=busy||!state.selected.size||!state.activeTarget;
    $('#clear-selection').disabled=busy||!state.selected.size;
    $('#select-filtered').disabled=busy||!state.visible.some(row=>!row.protected);
    $('#bulk-keep').disabled=busy||!state.selected.size;
    $('#bulk-approve').disabled=busy||!state.selected.size||[...state.selected].some(name=>!targetFor(state.rowByName.get(name)));
    $('#accept-suggestions').disabled=busy||!applicable().length;
    $('#retry-btn').disabled=busy;
    const selection=C.selectionState(state.visible.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE),state.selected);
    $('#select-all').checked=selection.checked;$('#select-all').indeterminate=selection.indeterminate;
    $('#select-all').disabled=busy||selection.disabled;
    for(const [name,node] of state.rowNodes) {
      const row=state.rowByName.get(name);
      node.querySelector('.row-check').disabled=busy||row.protected;
      node.querySelector('.source-button').disabled=busy;
    }
    const row=currentRow();
    $('#current-stage').disabled=busy||!row||row.protected||!state.activeTarget;
    $('#current-approve').disabled=busy||!row||row.protected||!targetFor(row);
    $('#current-keep').disabled=busy||!row||row.protected;
    for(const node of document.querySelectorAll('#target-list button,#target-list input'))node.disabled=busy||node.dataset.unavailable==='true';
    for(const id of ['search','status-filter','select-filtered'])$('#'+id).disabled=busy||!loaded;
    for(const id of ['target-search','target-category','assigned-only','clear-target-filters'])$('#'+id).disabled=busy||!ready;
    const pages=Math.max(1,Math.ceil(state.visible.length/PAGE_SIZE));
    $('#prev-page').disabled=state.page===0;$('#next-page').disabled=state.page>=pages-1;
    setText('#operation-note',busy?state.stage:!$('#error-panel').hidden?'操作未完成，請查看錯誤原因。':!ready?'工作區尚未就緒。':loaded?'審核工作區已就緒 · 原始圖面保持不變':'工作區已就緒 · 匯入圖面開始審核');
    $('#operation').classList.toggle('busy',busy);
    $('#operation').classList.toggle('idle',!busy&&loaded&&$('#error-panel').hidden);
    if(!busy && $('#error-panel').hidden)$('#operation').classList.remove('failed');
    setText('#gate-title',busy?'正在處理':'轉換門檻');
    const format=state.inventory?.filetype||'圖面';
    const quickLabel=!loaded?'選擇圖面開始':canConvert()?'核准完成，另存並驗證':state.semantic.available&&state.suggestionEngine!=='semantic-model'?'開始模型辨識':'繼續建議轉換';
    setText('#quick-flow-btn',busy?'流程處理中…':quickLabel);
    $('#quick-flow-btn').classList.toggle('is-ready',!busy&&canConvert());
    $('.quick-flow').classList.toggle('is-working',busy);
    setText('#convert-btn',busy&&state.stage.includes('另存')?'另存與驗證中…':'另存 '+format+' 並驗證');
    setText('#gate-note',busy?state.stage:!loaded?'請先載入 DWG、DXF 或盤點清單。':!state.inventory.can_convert?'此來源為盤點清單，只允許審核與匯出映射。':c.pending?'尚有 '+c.pending+' 個圖層待審核，完成後才能另存。':'人工審核完成；'+c.protected+' 個保護圖層保留，可另存並驗證。');
  }
  async function runOperation(stage,work,retry) {
    if(state.busy || $('#decision-dialog').open)return;
    const token=++state.operation,revision=state.revision,job=state.inventory?.job_id;
    const valid=()=>state.operation===token&&state.revision===revision&&state.inventory?.job_id===job;
    state.busy=true;state.stage=stage;clearError();syncControls();
    try { await work(valid); }
    catch(error) { if(state.operation===token)showError(stage+'失敗',error,retry); }
    finally { if(state.operation===token){state.busy=false;state.stage='';syncControls();} }
  }
  function snapshot(label) {
    return {label,standard:state.standard,standardLabel:state.standardLabel,suggestions:new Map(state.suggestions),actions:new Map([...state.actions].map(([k,v])=>[k,{...v}])),
      suggestionEngine:state.suggestionEngine,selected:new Set(state.selected),focused:state.focused,drafts:new Map(state.drafts),activeTarget:state.activeTarget};
  }
  function remember(label) { state.undo=snapshot(label); }
  function invalidateResult() { state.revision++;$('#results').hidden=true;queueReviewSave(); }
  function undo() {
    if(state.busy||!state.undo)return;
    const previous=state.undo;
    Object.assign(state,{standard:previous.standard,standardLabel:previous.standardLabel||'default',suggestions:previous.suggestions,actions:previous.actions,
      suggestionEngine:previous.suggestionEngine,selected:previous.selected,focused:previous.focused,drafts:previous.drafts,activeTarget:previous.activeTarget,undo:null});
    invalidateResult();updateProfile();render();toast('已復原：'+previous.label);
  }
  async function choose(title,message,choices) {
    if($('#decision-dialog').open)return 'cancel';
    const dialog=$('#decision-dialog');dialog.returnValue='cancel';
    setText('#dialog-title',title);setText('#dialog-message',message);
    $('#dialog-actions').replaceChildren();
    for(const [value,label] of [['cancel','取消'],...choices]) {
      const button=document.createElement('button');button.className='button '+(value==='cancel'?'secondary':'primary');
      button.value=value;button.textContent=label;$('#dialog-actions').append(button);
    }
    const result=new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue||'cancel'),{once:true}));
    dialog.showModal();$('#dialog-actions button').focus();
    return result;
  }
  function updateProfile() {
    $('#side-profile').innerHTML='<strong>'+esc(standardDisplayName())+'</strong><p>版本 '+esc(state.standard?.version||'—')+' · '+(state.standard?.layers?.length||0)+' 個目標</p>';
    setText('#standard-summary','標準：'+standardDisplayName()+' · '+(state.standard?.version||'—'));
    setText('#stat-targets',state.standard?.layers?.length??'—');setText('#standard-version','版本 '+(state.standard?.version||'—'));
    const categories=[...new Set((state.standard?.layers||[]).map(targetCategory))];
    $('#target-category').replaceChildren(new Option('所有分類',''),...categories.map(name=>new Option(name,name)));
    if(!categories.includes(state.category))state.category='';$('#target-category').value=state.category;
    if(!(state.standard?.layers||[]).some(t=>t.name===state.activeTarget))state.activeTarget='';
    state.expandedTarget='';state.memberPage=0;
    setText('#target-standard-version',standardDisplayName()+' · '+(state.standard?.version||'—'));
    state.rowNodes.clear();$('#layer-rows').replaceChildren();
  }
  function buildInventoryIndex() {
    drawingKey='';
    state.rowByName=new Map(rows().map(row=>[row.name,row]));
    state.searchIndex=new Map(rows().map(row=>[row.name,[row.name,...Object.keys(row.types||{}),...Object.keys(row.block_names||{})].join(' ').toLowerCase()]));
    state.geometries.clear();state.background=[];
    // Bound context drawing globally, not independently per source layer.
    for(const row of rows()) {
      if(state.background.length>=BACKGROUND_BUDGET)break;
      for(const g of geometryFor(row).slice(0,80)) {
        state.background.push({source:row.name,geometry:g});
        if(state.background.length>=BACKGROUND_BUDGET)break;
      }
    }
  }
  function reset(result) {
    flushReview();
    clearTimeout(searchTimer);
    clearTimeout(reviewTimer);lastReview='';reviewIdentity=null;reviewPaused=false;$('#resume-save').hidden=true;
    for(const id of ['restore-review','discard-review','review-resume-note','review-drafts'])$('#'+id).hidden=true;
    state.inventory=result.inventory;state.standard=result.standard;state.standardLabel='default';
    state.suggestions=new Map(result.suggestions.map(s=>[s.source,s]));state.suggestionEngine='rules';
    state.actions=C.createActions(rows());state.drafts.clear();state.selected.clear();state.undo=null;
    state.activeTarget=state.expandedTarget=state.targetQuery=state.category='';state.assignedOnly=false;state.memberPage=0;
    $('#target-search').value='';$('#assigned-only').checked=false;
    state.focused=rows().find(row=>!row.protected)?.name||rows()[0]?.name||null;
    state.page=0;state.filter='all';state.query='';$('#search').value='';$('#status-filter').value='all';
    invalidateResult();buildInventoryIndex();updateProfile();
    $('#side-source').innerHTML='<strong>'+esc(state.inventory.filename)+'</strong><p>'+esc(state.inventory.filetype)+' · '+rows().length+' 層 · '+Number(state.inventory.total_entities||0).toLocaleString()+' 個物件</p>';
    setText('#file-breadcrumb',state.inventory.filename+' · '+state.inventory.filetype+' · '+rows().length+' 層');
    render();toast(state.inventory.filename+' 盤點完成，等待審核。');
  }
  function updateCounts() {
    const c=counts();
    setText('#stat-layers',state.inventory?c.total:'—');
    setText('#stat-entities',state.inventory?Number(state.inventory.total_entities||0).toLocaleString():'—');
    setText('#stat-approved',c.manual);setText('#stat-denom',' / '+c.reviewable);
    setText('#stat-pending',state.inventory?c.pending:'—');setText('#stat-protected',state.inventory?c.protected:'—');
    setText('#pending-detail',state.inventory?c.suggested+' 有建議 · '+c.unknown+' 待判讀':'等待載入');
    const engine=state.suggestionEngine==='semantic-model'?'模型語意建議':'規則建議';
    setText('#quick-flow-note',!state.inventory?'選擇圖面後，依序完成辨識、建議核准與驗證另存。':
      !state.inventory.can_convert?'此來源只能審核與匯出映射，不能轉換。':
      c.pending===0?'全部 '+c.reviewable+' 個非保護圖層已有人工決策，可另存副本並驗證。':
      engine+'：'+c.suggested+' 個可核准 · '+c.unknown+' 個需人工判讀；建議不會自動視為核准。');
    setText('#nav-count',c.pending);setText('#selected-count',state.selected.size);
    const visibleNames=new Set(state.visible.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE).map(row=>row.name));
    const hidden=[...state.selected].filter(name=>!visibleNames.has(name)).length;
    setText('#hidden-selected',hidden?'包含目前來源頁面以外 '+hidden+' 層':'勾選跨頁、跨篩選保留');
    setText('#select-filtered','選取篩選結果 '+state.visible.filter(row=>!row.protected).length+' 層');
    const shortName=name=>name.length>28?name.slice(0,25)+'…':name;
    setText('#bulk-stage',state.activeTarget?'暫定至 '+shortName(state.activeTarget):'先選擇標準目標');
    const missing=[...state.selected].filter(name=>!targetFor(state.rowByName.get(name))).length;
    setText('#bulk-target-label',missing?missing+' 層尚無有效目標，暫定後才能整批確認':'確認時保留每個來源各自的目標');
    setText('#bulk-approve','確認這 '+state.selected.size+' 層的映射');
    setText('#bulk-keep','保留這 '+state.selected.size+' 層');
    setText('#current-target-name',state.activeTarget?'準備指定的目標：'+state.activeTarget:'準備指定的目標：尚未選擇');
    setText('#current-stage',state.activeTarget?'暫定至 '+shortName(state.activeTarget):'先選擇標準目標');
    $('#current-stage').title=state.activeTarget?'暫定至 '+state.activeTarget:'請先選擇標準目標';
    const current=currentRow(),currentMapping=current&&targetFor(current);
    setText('#current-approve',currentMapping?'確認 '+shortName(current.name)+' → '+shortName(currentMapping):'確認目前圖層');
    $('#current-approve').title=currentMapping?'確認 '+current.name+' → '+currentMapping:'目前來源尚無有效目標';
    $('#current-approve').setAttribute('aria-label',$('#current-approve').title);
    $('#bulk-row').hidden=!state.selected.size;$('#current-actions').hidden=!!state.selected.size;
    setText('#inspector-scope',state.selected.size?'目前檢視來源 · 僅供核對證據':'只操作目前圖層');
    $('#bulk-stage').title=state.activeTarget?'指定至 '+state.activeTarget:'請先點選標準目標';
    setText('#accept-suggestions','採用所有圖層的 '+applicable().length+' 個建議');
    setText('#model-suggest-btn',state.semantic.available?'模型判定':'模型未設定');
    setText('#model-note',state.semantic.available?'模型 '+state.semantic.model+' · 啟動時傳送抽取證據與標準；不傳原始圖面。':'模型未設定；目前僅有規則建議，仍須人工確認。');
    const labels={all:'全部圖層',suggested:'有建議待確認',unknown:'待人工判讀',pending:'全部待審核',approved:'人工已審核',protected:'系統保護'};
    setText('#filter-label',labels[state.filter]);
    for(const [id,active] of [['nav-mapping',state.filter==='all'],['nav-review',state.filter==='pending']]) {
      $('#'+id).classList.toggle('active',active);$('#'+id).setAttribute('aria-pressed',String(active));
    }
    setText('#warnings',(state.inventory?.warnings||[]).join(' · '));
    syncControls();queueReviewSave();
  }
  function focusRow(name) {
    state.focused=name;
    for(const [key,node] of state.rowNodes) {
      node.classList.toggle('is-focused',key===name);
      node.querySelector('.source-button').setAttribute('aria-pressed',String(key===name));
    }
    renderTargets();renderInspector();updateCounts();
  }
  function createRow(row) {
    const node=document.createElement('div');node.className='layer-row'+(row.protected?' is-protected':'');node.dataset.source=row.name;
    node.innerHTML='<input class="row-check" type="checkbox" aria-label="勾選 '+esc(row.name)+'"><button class="source-button" aria-label="檢視 '+esc(row.name)+' 的證據" aria-controls="inspect-name" draggable="false"><i class="swatch" aria-hidden="true"></i><span class="source-copy"><strong class="name-text">'+esc(row.name)+'</strong><small class="sub-row">'+Number(row.total||0).toLocaleString()+' 個物件 · '+esc(Object.keys(row.types||{}).slice(0,3).join(' / ')||'空白')+'</small></span><span class="badge"></span></button>';
    node.querySelector('.swatch').style.backgroundColor=colorACI[row.color?.aci]||'#7a8995';
    const infoId='source-info-'+rows().indexOf(row),statusId='source-status-'+rows().indexOf(row);
    node.querySelector('.sub-row').id=infoId;node.querySelector('.badge').id=statusId;
    node.querySelector('.source-button').setAttribute('aria-describedby',infoId+' '+statusId);
    node.querySelector('.source-button').addEventListener('click',()=>{if(!state.busy)focusRow(row.name);});
    node.querySelector('.row-check').addEventListener('change',event=>{
      if(state.busy)return;
      if(event.target.checked)state.selected.add(row.name);else state.selected.delete(row.name);
      updateRow(row,node);renderTargets();updateCounts();
    });
    return node;
  }
  function updateRow(row,node) {
    const [kind,label]=rowStatus(row);
    node.classList.toggle('is-focused',row.name===state.focused);node.classList.toggle('is-checked',state.selected.has(row.name));
    node.querySelector('.source-button').setAttribute('aria-pressed',String(row.name===state.focused));
    const check=node.querySelector('.row-check');check.checked=state.selected.has(row.name);check.disabled=state.busy||row.protected;
    const badge=node.querySelector('.badge');badge.className='badge '+kind;badge.textContent=label;
  }
  function targetMembers() {
    const groups=new Map((state.standard?.layers||[]).map(t=>[t.name,[]]));
    for(const row of rows()){const target=targetFor(row);if(groups.has(target))groups.get(target).push(row);}
    return groups;
  }
  function renderTargets() {
    const host=$('#target-list'),scroll=host.scrollTop,focused=document.activeElement;
    const attr=['data-target','data-member-select','data-member-source','data-board-action'].find(key=>focused?.hasAttribute(key));
    const focusValue=attr&&host.contains(focused)?focused.getAttribute(attr):null;
    const groups=targetMembers(),query=state.targetQuery.trim().toLowerCase();
    const targets=(state.standard?.layers||[]).filter(t=>(!state.category||targetCategory(t)===state.category)&&(!state.assignedOnly||groups.get(t.name).length)&&(!query||[t.name,t.category,...(t.aliases||[])].join(' ').toLowerCase().includes(query)));
    const checks=row=>'<input type="checkbox" data-member-select="'+esc(row.name)+'" aria-label="勾選 '+esc(row.name)+'" '+(state.selected.has(row.name)?'checked':'')+'>';
    host.innerHTML=targets.map(target=>{
      const members=groups.get(target.name),done=members.filter(row=>C.humanApproved(row,state.actions)).length,open=state.expandedTarget===target.name;
      let content='';
      if(open){
        state.memberPage=Math.max(0,Math.min(state.memberPage,Math.ceil(members.length/MEMBER_PAGE_SIZE)-1));
        const page=members.slice(state.memberPage*MEMBER_PAGE_SIZE,(state.memberPage+1)*MEMBER_PAGE_SIZE);
        content='<div class="target-members"><div class="member-heading"><span>'+done+' 已確認 / '+members.length+' 個來源</span><button class="small-button" data-board-action="select-group" '+(!members.length?'data-unavailable="true" disabled':'')+'>選取此目標全部 '+members.length+' 層</button></div>'+page.map(row=>'<div class="member-row '+(row.name===state.focused?'is-focused':'')+'">'+checks(row)+'<button data-member-source="'+esc(row.name)+'" draggable="false"><strong>'+esc(row.name)+'</strong><span>'+Number(row.total||0).toLocaleString()+' 物件</span><span>'+rowStatus(row)[1]+'</span></button></div>').join('')+
          (members.length?'<div class="member-page"><span>'+(state.memberPage*MEMBER_PAGE_SIZE+1)+'–'+Math.min((state.memberPage+1)*MEMBER_PAGE_SIZE,members.length)+' / '+members.length+'</span><button class="small-button" data-board-action="members-prev" aria-label="上一頁目標內來源" '+(!state.memberPage?'data-unavailable="true" disabled':'')+'>'+pagingIcon(false)+'</button><button class="small-button" data-board-action="members-next" aria-label="下一頁目標內來源" '+((state.memberPage+1)*MEMBER_PAGE_SIZE>=members.length?'data-unavailable="true" disabled':'')+'>'+pagingIcon(true)+'</button></div>':'<p class="target-empty">尚無來源；可拖曳來源至此，或勾選後指定。</p>')+'</div>';
      }
      const aci=target.aci??7,weight=displayLineweight(target.lineweight);
      return '<article class="target-row '+(state.activeTarget===target.name?'is-active':'')+'" data-drop-target="'+esc(target.name)+'"><button class="target-toggle" data-target="'+esc(target.name)+'" aria-expanded="'+open+'" aria-pressed="'+(state.activeTarget===target.name)+'"><span class="chevron">'+chevron(open)+'</span><strong title="'+esc(target.name)+'">'+esc(target.name)+'</strong><small class="column-category">'+esc(targetCategory(target))+'</small><span class="column-aci" style="--aci-color:'+esc(colorACI[aci]||'#64748b')+'">ACI '+esc(aci)+'</span><span class="column-line">'+esc(target.linetype||'CONTINUOUS')+'</span><span class="column-weight">'+esc(weight)+'</span><span class="column-mobile">'+esc(targetProperties(target))+'</span><span class="target-count '+(!members.length?'empty':'')+'">'+members.length+' 來源'+(done?' · '+done+' 已確認':'')+'</span></button>'+content+'</article>';
    }).join('')||'<p class="target-empty">'+(state.standard?'沒有符合的標準目標。':'正在讀取標準…')+'</p>';
    host.scrollTop=scroll;
    if(focusValue!==null)host.querySelector('['+attr+'="'+CSS.escape(focusValue)+'"]')?.focus({preventScroll:true});
    const total=state.standard?.layers?.length||0,filtered=!!(query||state.category||state.assignedOnly);
    setText('#target-count',filtered?'符合 '+targets.length+' / 共 '+total+' 個標準目標':'全部 '+total+' 個標準目標');
    setText('#target-range',filtered?'顯示符合條件的 '+targets.length+' 個目標 · 清除篩選可查看全部':'完整清單 · 上下捲動，無須翻頁');
    $('#clear-target-filters').hidden=!filtered;
  }
  function renderList() {
    state.visible=C.filterRows(rows(),state.actions,proposals(),state.filter,state.query,state.searchIndex);
    const pages=Math.max(1,Math.ceil(state.visible.length/PAGE_SIZE));state.page=Math.min(state.page,pages-1);
    const pageRows=state.visible.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE);
    const body=$('#layer-rows'),names=new Set(pageRows.map(row=>row.name));
    for(const [name,node] of state.rowNodes)if(!names.has(name)){node.remove();state.rowNodes.delete(name);}
    let cursor=body.firstElementChild;
    for(const row of pageRows) {
      let node=state.rowNodes.get(row.name);
      if(!node){node=createRow(row);state.rowNodes.set(row.name,node);}
      if(node!==cursor)body.insertBefore(node,cursor);
      else cursor=cursor.nextElementSibling;
      updateRow(row,node);
    }
    const empty=!state.visible.length;$('#empty-list').hidden=!empty;
    if(empty) {
      const noSource=!state.inventory,noLayers=!!state.inventory&&!rows().length,complete=state.filter==='pending'&&!counts().pending&&!state.query.trim();
      setText('#empty-title',noSource?'尚未載入圖層':noLayers?'來源清單沒有圖層':complete?'所有圖層已完成審核':'無符合條件的圖層');
      setText('#empty-note',noSource?'匯入 DWG / DXF，或開啟示範圖面開始操作。':noLayers?'請確認來源檔案的圖層盤點內容。':complete?'可切換至全部圖層複查，或另存並驗證。':'請重設搜尋或切換審核狀態。');
      $('#clear-filters').hidden=noSource||noLayers;
    }
    setText('#range-note',state.visible.length?(state.page*PAGE_SIZE+1)+'–'+Math.min((state.page+1)*PAGE_SIZE,state.visible.length)+' / '+state.visible.length+' 層':'0 個圖層');
    setText('#page-note',(state.page+1)+' / '+pages);
  }
  function render() { renderList();renderTargets();updateCounts();renderInspector(); }
  function geometryFor(row) {
    if(!state.geometries.has(row.name))state.geometries.set(row.name,(row.preview||[]).map(C.geometry).filter(Boolean));
    return state.geometries.get(row.name);
  }
  function setView(view) {
    state.view=view;$('#preview-svg').setAttribute('viewBox',view.join(' '));
  }
  function previewEmpty(message) {
    $('#preview-svg').replaceChildren();setView([0,0,600,300]);state.baseView=null;
    setText('#preview-empty',message);$('#preview-empty').hidden=false;
    setText('#preview-count','0 個可繪製樣本');
    for(const id of ['zoom-in','zoom-out','fit-preview'])$('#'+id).disabled=true;
  }
  function drawPreview() {
    const svg=$('#preview-svg'),row=currentRow(),key=state.inventory?.job_id+'|'+state.focused+'|'+$('#preview-mode').value;
    if(drawingKey===key)return;drawingKey=key;svg.replaceChildren();
    svg.setAttribute('aria-label',row?row.name+' 的抽樣幾何證據':'尚未選取來源圖層');
    if(!row){previewEmpty('選取來源圖層以檢視幾何證據。');return;}
    const active=geometryFor(row);
    if(!active.length) {
      previewEmpty(!row.total?'此圖層沒有物件。':!(row.preview||[]).length?'此圖層的物件尚無支援的預覽樣本。':'預覽樣本缺少有效座標，請依內容證據判讀。');
      return;
    }
    const background=state.background.filter(g=>g.source!==row.name).map(g=>g.geometry);
    const all=$('#preview-mode').value==='context'?[...active,...background]:active;
    const bounds=C.bounds(all);
    if(!bounds){previewEmpty('沒有可繪製的有效座標。');return;}
    state.baseView=bounds;setView([...bounds]);$('#preview-empty').hidden=true;
    const fragment=document.createDocumentFragment();
    function paths(items,color,opacity,width) {
      for(const g of items) {
        const path=document.createElementNS(SVGNS,'path');
        path.setAttribute('d',g.points.map((p,i)=>(i?'L':'M')+' '+p[0]+' '+(-p[1])).join(' ')+(g.closed?' Z':''));
        path.setAttribute('fill','none');path.setAttribute('stroke',color);path.setAttribute('opacity',opacity);
        path.setAttribute('stroke-width',width);path.setAttribute('vector-effect','non-scaling-stroke');
        fragment.append(path);
      }
    }
    paths(background,'#8795a7',.42,.7);paths(active,'#84b4ff',1,1.7);svg.append(fragment);
    setText('#preview-count',active.length+' / '+(row.preview||[]).length+' 個選取樣本 · 背景 '+background.length);
    for(const id of ['zoom-in','zoom-out','fit-preview'])$('#'+id).disabled=false;
  }
  function renderInspector() {
    const row=currentRow();
    if(!row) {
      setText('#inspect-name','尚未選取');setText('#inspect-status','等待載入');setText('#current-target','尚未指定目標');
      setText('#relation-label','等待載入來源');setText('#relation-name','—');setText('#relation-properties','');
      setText('#decision-types','選取來源後顯示主要物件類型。');setText('#decision-reason','建議理由會顯示在此，確認前可直接核對。');
      $('#props').innerHTML=['ACI 色彩','線型','線寬','物件覆寫'].map(label=>'<div><dt>'+label+'</dt><dd>—</dd></div>').join('');
      setText('#evidence','讀取來源後顯示。');setText('#recommend','等待分析');drawPreview();return;
    }
    setText('#inspect-name',row.name);setText('#inspect-status',rowStatus(row)[1]);
    const target=targetFor(row),action=C.actionFor(state.actions,row);
    const kind=action.decision==='map'?'已確認映射':state.drafts.has(row.name)?'暫定映射':'建議映射';
    setText('#current-target',row.protected?'系統保護 · 保留原圖層':action.decision==='keep'?'已確認保留原圖層':target?kind+'：'+row.name+' → '+target+(action.decision==='map'?'':' · 尚未核准'):'尚未指定標準目標');
    const kept=row.protected||action.decision==='keep',definition=!kept&&state.standard?.layers.find(t=>t.name===target);
    setText('#relation-label',row.protected?'系統保護 · 保留原圖層':action.decision==='keep'?'已確認保留原圖層':target?action.decision==='map'?'已人工確認':state.drafts.has(row.name)?'暫定目標 · 尚未核准':'建議目標 · 尚未核准':'尚無建議 · 請選擇標準目標');
    setText('#relation-name',kept?row.name:target||'—');
    setText('#relation-properties',definition?targetProperties(definition):'');
    const weight=displayLineweight(row.lineweight);
    $('#props').innerHTML='<div><dt>ACI 色彩</dt><dd><i class="color-square" aria-hidden="true"></i>'+esc(row.color?.aci??'—')+'</dd></div><div><dt>線型</dt><dd>'+esc(row.linetype||'—')+'</dd></div><div><dt>線寬</dt><dd>'+weight+'</dd></div><div><dt>物件覆寫</dt><dd>'+(row.override_count||0)+' / '+(row.total||0)+'</dd></div>';
    $('#props .color-square').style.backgroundColor=colorACI[row.color?.aci]||'#94a4b0';
    const types=Object.entries(row.types||{}).sort((a,b)=>b[1]-a[1]);
    $('#evidence').innerHTML=types.map(([name,n])=>'<span class="evidence-chip">'+esc(name)+' '+n+'</span>').join('')+
      '<div>模型 '+(row.counts?.model||0)+' · 配置 '+(row.counts?.paper||0)+' · 圖塊定義 '+(row.counts?.block||0)+'</div>';
    const blocks=Object.entries(row.block_names||{}).slice(0,20);
    if(blocks.length)$('#evidence').innerHTML+='<details><summary>圖塊證據（前 '+blocks.length+' 項）</summary>'+blocks.map(([name,n])=>'<div>'+esc(name)+' × '+esc(n)+'</div>').join('')+'</details>';
    const suggestion=state.suggestions.get(row.name)||{},model=state.suggestionEngine==='semantic-model';
    setText('#decision-types',Number(row.total||0).toLocaleString()+' 個物件 · '+(types.slice(0,3).map(([name,n])=>name+' '+n).join(' / ')||'尚無物件類型證據'));
    setText('#decision-reason',row.protected?(row.protected_reason||'系統保護圖層，保留原圖層。'):
      action.decision==='keep'?'你已確認保留此來源的原圖層。':
      state.drafts.has(row.name)?'你已手動暫定至 '+target+'；請核對幾何與內容證據後再確認。':suggestion.reason||'尚無足夠證據提供建議，請依內容人工判讀。');
    setText('#suggestion-title',model?'模型語意建議':'規則建議');
    if(row.protected)$('#recommend').innerHTML='<strong>系統保護，保留原圖層</strong><p>'+esc(row.protected_reason||'系統保護圖層')+'</p>';
    else {
      const score=Number.isFinite(suggestion.confidence)?Math.round(suggestion.confidence*100):null;
      $('#recommend').innerHTML='<strong>'+esc(suggestion.target||'需要人工判讀')+'</strong>'+
        (score===null?'':'<span class="confidence">'+(model?'模型分數':'規則匹配分數')+' '+score+'%</span>')+
        '<p>'+esc(suggestion.reason||'尚無足夠證據提供目標建議。')+'</p>';
    }
    drawPreview();
  }
  function zoom(factor) {
    if(!state.baseView)return;
    const [x,y,w,h]=state.view,relative=w*factor/state.baseView[2];
    if(relative<.02||relative>20)return;
    setView([x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor]);
  }
  const payload=()=>({standard:state.standard,actions:rows().map(row=>{const action=C.actionFor(state.actions,row),target=targetFor(row);return action.decision==='pending'&&target?{...action,proposed_target:target}:action;})});
  async function preserveReviewBeforeImport() {
    if(state.busy)return false;
    if(!state.inventory)return true;
    await saveReview();
    if(!reviewDirty)return true;
    return await choose('目前草稿尚未保存','瀏覽器未能保存目前進度。請取消後使用「匯出映射」，或確認仍要開始另一份圖面；目前未保存的變更將無法恢復。',
      [['continue','仍要載入另一份圖面']])==='continue';
  }
  async function handleDemo() {
    if(!await preserveReviewBeforeImport())return;
    await runOperation('正在盤點示範 DXF',async valid=>{
      const result=await request('/api/demo',{method:'POST'});if(valid())reset(result);
    },handleDemo);
  }
  async function scanFile(file) {
    if(!await preserveReviewBeforeImport())return false;
    let scanned=false;
    await runOperation('正在盤點 '+file.name,async valid=>{
      const form=new FormData();form.append('file',file);
      const result=await request('/api/scan',{method:'POST',body:form});if(valid()){reset(result);scanned=true;}
    },()=>scanFile(file));
    return scanned;
  }
  async function changeStandard(file) {
    if(state.busy||!state.inventory)return;
    await runOperation('正在驗證圖層標準',async valid=>{
      const standard=JSON.parse(await file.text());
      const result=await jsonPost('/api/suggest',{inventory:state.inventory,standard});
      if(!valid())return;
      const compatible=C.compatibleActions(rows(),state.actions,state.standard,result.standard);
      const reviewed=counts().manual;
      // Keep the operation lock while the native modal asks how to handle reviewed decisions.
      const answer=await choose('切換圖層標準',
        '新標準：'+file.name+'\n目前已有 '+reviewed+' 個人工審核決策。\n可保留 '+compatible.preserved+' 個決策（保留原圖層，或目標屬性完全相同）；其餘須重新審核。操作後可復原。',
        [['preserve','保留相容決策'],['reset','全部重新審核']]);
      if(answer==='cancel'||!valid())return;
      remember('切換圖層標準');state.standard=result.standard;state.standardLabel=file.name;
      state.suggestions=new Map(result.suggestions.map(s=>[s.source,s]));state.suggestionEngine='rules';
      state.actions=answer==='preserve'?compatible.actions:C.createActions(rows());state.drafts.clear();
      state.selected.clear();state.activeTarget='';invalidateResult();updateProfile();render();
      toast('標準已切換；保留 '+(answer==='preserve'?compatible.preserved:0)+' 個人工決策。可使用復原。');
    },()=>changeStandard(file));
  }
  function stageSources(names,target) {
    if(state.busy)return;
    const result=C.stageMapping(rows(),state.actions,state.drafts,names,target,state.standard);
    if(!result.ok){toast(result.error||'沒有可指定的來源圖層。',true);return;}
    remember('指定 '+result.count+' 層至 '+target);state.actions=result.actions;state.drafts=result.drafts;
    invalidateResult();render();toast('已指定 '+result.count+' 層 → '+target+'；仍須確認映射。');
  }
  function confirmSources(names,keep=false) {
    if(state.busy)return;
    const result=C.confirmMappings(rows(),state.actions,state.suggestions,state.drafts,names,state.standard,keep);
    if(!result.ok){toast(result.error,true);return false;}
    remember((keep?'保留':'確認映射')+' '+result.count+' 層');state.actions=result.actions;
    invalidateResult();toast(result.count+' 層已'+(keep?'確認保留':'確認映射')+'。可使用復原。');return true;
  }
  function applyBulk(keep=false) {
    if(confirmSources([...state.selected],keep)){state.selected.clear();render();}
  }
  async function acceptSuggestions() {
    if(state.busy)return;
    const candidates=applicable();if(!candidates.length)return;
    const revision=state.revision;
    const answer=await choose('採用所有圖層的建議',
      '將核准 '+candidates.length+' 個來源圖層的建議。\n範圍包含目前搜尋、篩選及分頁以外的圖層，與勾選方框無關。手動指定的暫定目標、既有人工決策與保護圖層保持不變。操作後可復原。',
      [['accept','確認採用 '+candidates.length+' 個建議']]);
    if(answer!=='accept'||state.busy||revision!==state.revision)return;
    remember('採用所有圖層的建議');
    for(const row of candidates)state.actions.set(row.name,{source:row.name,decision:'map',target:state.suggestions.get(row.name).target,reason:'human_approved_suggestion'});
    invalidateResult();render();toast('已採用 '+candidates.length+' 個建議；仍有 '+counts().pending+' 個圖層待審核。可使用復原。');
  }
  async function modelSuggest() {
    if(!state.inventory||!state.semantic.available)return false;
    let completed=false;
    await runOperation('正在執行模型語意判定',async valid=>{
      const result=await jsonPost('/api/jobs/'+state.inventory.job_id+'/semantic-suggest',{standard:state.standard});
      if(!valid())return;
      remember('模型建議更新');
      state.suggestions=new Map(result.suggestions.map(s=>[s.source,s]));state.suggestionEngine='semantic-model';
      state.revision++;render();const box=$('#results');box.hidden=false;box.replaceChildren();
      const title=document.createElement('strong');title.textContent='模型已完成語意判定；建議仍需人工確認。';box.append(title);
      if(result.download)box.append(fileLink(result.download,result.receipt||'模型判定稽核 JSON'));
      toast('模型判定完成；請先檢視內容證據，再採用建議。');
      completed=true;
    },modelSuggest);
    return completed;
  }
  function approveSuggestedRows(candidates,label) {
    if(!candidates.length)return;
    remember(label);
    for(const row of candidates)state.actions.set(row.name,{source:row.name,decision:'map',target:state.suggestions.get(row.name).target,reason:'human_approved_suggestion'});
    invalidateResult();render();
  }
  async function quickFlow() {
    if(state.busy)return;
    if(!state.inventory) {
      state.quickAfterImport=true;$('#input-file').click();return;
    }
    if(!state.inventory.can_convert) {
      toast('這份來源只有盤點清單，不能執行轉換。',true);return;
    }
    if(canConvert()) {
      const answer=await choose('另存並驗證',
        '全部非保護圖層已有人工作出明確決策。將另存新的 '+state.inventory.filetype+' 副本，並以獨立程序重新開啟驗證；原始圖面不會被覆寫。',
        [['convert','另存副本並驗證']]);
      if(answer==='convert')await convert();
      return;
    }
    if(state.semantic.available&&state.suggestionEngine!=='semantic-model') {
      const answer=await choose('開始模型辨識',
        '將把抽取後的圖層名稱、屬性、物件類型、文字／屬性樣本與正規化幾何樣本傳給 '+state.semantic.model+'；不會傳送原始 DWG。模型只會從目前核准標準提出建議，建議仍需你核准。',
        [['recognize','開始辨識']]);
      if(answer!=='recognize')return;
      if(!await modelSuggest())return;
    }
    const candidates=applicable(),before=counts(),engine=state.suggestionEngine==='semantic-model'?'模型':'規則';
    if(!candidates.length) {
      setFilter('pending');toast('目前沒有可直接採用的'+engine+'建議；請人工判讀剩餘 '+before.pending+' 個圖層。');return;
    }
    const pendingAfter=Math.max(0,before.pending-candidates.length);
    const choice=pendingAfter===0?'approve-convert':'approve-review';
    const answer=await choose(engine+'辨識建議已就緒',
      engine+'提供 '+candidates.length+' 個可採用建議；'+pendingAfter+' 個圖層仍需人工判讀。\n按下確認即代表你明確核准這 '+candidates.length+' 個來源圖層的建議映射。'+(pendingAfter===0?'核准後將另存副本並執行 Save/Reopen 驗證。':'其餘圖層不會被自動處理。'),
      [[choice,pendingAfter===0?'核准建議並另存驗證':'核准建議並檢視剩餘圖層']]);
    if(answer!==choice||state.busy)return;
    approveSuggestedRows(candidates,'快速流程採用 '+candidates.length+' 個建議');
    if(pendingAfter===0&&canConvert())await convert();
    else {setFilter('pending');toast('已核准 '+candidates.length+' 個建議；請判讀剩餘 '+counts().pending+' 個圖層。');}
  }
  async function initialize() {
    await runOperation('正在讀取工作區與標準',async valid=>{
      const [health,standard]=await Promise.all([request('/api/health'),request('/api/standard')]);
      if(!valid())return;
      state.semantic=health.semantic_suggest||{available:false,model:null};state.standard=standard;state.initialized=true;
      document.querySelectorAll('[data-app-version]').forEach(node=>{node.textContent='v'+health.version;});
      setText('#connection-status','本機服務已連線');updateProfile();render();
    },initialize);
    if(!state.initialized) {
      setText('#connection-status','本機服務未就緒');
      $('#side-profile').innerHTML='<strong>無法讀取標準</strong><p>請查看錯誤原因並重試。</p>';
      setText('#standard-summary','標準：讀取失敗');
    }
    if(state.initialized)offerSavedReviews();
    const completedJob=new URLSearchParams(location.search).get('result_job');
    if(state.initialized&&completedJob&&/^[0-9a-f-]{36}$/i.test(completedJob)) {
      await runOperation('正在讀取已驗證的 DWG 結果',async valid=>{
        const result=await request('/api/jobs/'+completedJob+'/result');if(!valid())return;
        showVerifiedResult(result,result.output.toLowerCase().endsWith('.dwg')?'DWG':'DXF');
      });
    }
  }
  async function exportPlan() {
    if(!state.inventory)return;
    await runOperation('正在匯出映射',async valid=>{
      const result=await jsonPost('/api/jobs/'+state.inventory.job_id+'/plan',payload());if(!valid())return;
      const box=$('#results');box.hidden=false;box.replaceChildren();
      box.append(result.approved?'✓ 完整映射已建立。':'映射草稿已建立；審核未完成，尚不可轉換。',fileLink(result.download,'下載 Mapping JSON'));
      toast('映射已匯出。');
    },exportPlan);
  }
  function showVerifiedResult(result,format) {
    const box=$('#results');box.hidden=false;box.replaceChildren();
    const title=document.createElement('strong');title.textContent='✓ '+format+' 已另存並通過 Save/Reopen 驗證';
    const detail=document.createElement('p');detail.textContent='物件數 '+result.report.entity_count_before+' → '+result.report.entity_count_after+'；原始檔未修改。'+(format==='DWG'?'模型、配置與可編輯圖塊定義已套用，XREF 保留。':'圖塊定義保留。');
    box.append(title,detail);
    if(format==='DWG')box.append(saveButton(result.download_prefix+result.output,result.output));
    else box.append(fileLink(result.download_prefix+result.output,'下載 '+result.output));
    const audit=document.createElement('div');audit.className='result-audit';audit.append('稽核檔：',fileLink(result.download_prefix+result.mapping,'mapping.json'),fileLink(result.download_prefix+result.validation,'validation.json'));box.append(audit);
  }
  async function convert() {
    if(!canConvert())return;
    const format=state.inventory.filetype;
    await runOperation('正在另存 '+format+' 副本並執行回讀驗證',async valid=>{
      const job=state.inventory.job_id;
      let result;
      try { result=await jsonPost('/api/jobs/'+job+'/convert',payload()); }
      catch(error) {
        const recovered=await request('/api/jobs/'+job+'/result').catch(()=>null);
        if(!recovered?.report || recovered.report.status!=='PASS')throw error;
        result=recovered;
      }
      if(!valid())return;
      showVerifiedResult(result,format);
      toast(format==='DWG'?'另存與驗證完成，請選擇 DWG 儲存位置。':'DXF 另存與驗證完成，請下載驗證後的檔案。');
    }); // A write is never retried automatically or through a generic retry action.
  }
  function setFilter(filter) {
    clearTimeout(searchTimer);state.filter=filter;state.query=$('#search').value;state.page=0;
    $('#status-filter').value=filter;renderList();updateCounts();$('#mapping-list').scrollTop=0;
  }
  $('#demo-btn').addEventListener('click',handleDemo);
  $('#import-btn').addEventListener('click',()=>{if(!state.busy){state.quickAfterImport=false;$('#input-file').click();}});
  $('#quick-flow-btn').addEventListener('click',quickFlow);
  $('#standard-btn').addEventListener('click',()=>{if(!state.busy)$('#standard-file').click();});
  $('#input-file').addEventListener('change',async event=>{
    const file=event.target.files[0],continueQuick=state.quickAfterImport;state.quickAfterImport=false;event.target.value='';
    if(file&&!state.busy&&await scanFile(file)&&continueQuick)await quickFlow();
  });
  $('#input-file').addEventListener('cancel',()=>{state.quickAfterImport=false;});
  $('#standard-file').addEventListener('change',event=>{
    const file=event.target.files[0];event.target.value='';if(file&&!state.busy)changeStandard(file);
  });
  $('#search').addEventListener('input',event=>{
    state.query=event.target.value;state.page=0;clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>{renderList();updateCounts();$('#mapping-list').scrollTop=0;},90);
  });
  $('#status-filter').addEventListener('change',event=>setFilter(event.target.value));
  $('#nav-review').addEventListener('click',()=>setFilter('pending'));
  $('#nav-mapping').addEventListener('click',()=>setFilter('all'));
  $('#clear-filters').addEventListener('click',()=>{$('#search').value='';state.query='';setFilter('all');$('#search').focus();});
  $('#select-all').addEventListener('change',event=>{
    if(state.busy)return;
    for(const row of state.visible.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE))if(!row.protected) {
      if(event.target.checked)state.selected.add(row.name);else state.selected.delete(row.name);
    }
    for(const [name,node] of state.rowNodes)updateRow(state.rowByName.get(name),node);
    renderTargets();updateCounts();
  });
  $('#bulk-stage').addEventListener('click',()=>stageSources([...state.selected],state.activeTarget));
  $('#current-stage').addEventListener('click',()=>stageSources([state.focused],state.activeTarget));
  $('#current-approve').addEventListener('click',()=>{if(confirmSources([state.focused]))render();});
  $('#current-keep').addEventListener('click',()=>{if(confirmSources([state.focused],true))render();});
  $('#select-filtered').addEventListener('click',()=>{
    if(state.busy)return;for(const row of state.visible)if(!row.protected)state.selected.add(row.name);render();
  });
  $('#clear-selection').addEventListener('click',()=>{if(!state.busy){state.selected.clear();render();}});
  $('#clear-target-filters').addEventListener('click',()=>{
    state.targetQuery=state.category='';state.assignedOnly=false;$('#target-search').value='';$('#target-category').value='';$('#assigned-only').checked=false;
    $('#target-list').scrollTop=0;renderTargets();syncControls();$('#target-search').focus();
  });
  $('#target-search').addEventListener('input',event=>{
    state.targetQuery=event.target.value;$('#target-list').scrollTop=0;renderTargets();syncControls();
  });
  $('#target-category').addEventListener('change',event=>{
    state.category=event.target.value;$('#target-list').scrollTop=0;renderTargets();syncControls();
  });
  $('#assigned-only').addEventListener('change',event=>{
    state.assignedOnly=event.target.checked;$('#target-list').scrollTop=0;renderTargets();syncControls();
  });
  $('#target-list').addEventListener('click',event=>{
    if(state.busy)return;
    const button=event.target.closest('button');if(!button||button.disabled)return;
    if(button.dataset.target){
      state.activeTarget=button.dataset.target;state.expandedTarget=state.expandedTarget===state.activeTarget?'':state.activeTarget;state.memberPage=0;
    } else if(button.dataset.memberSource){focusRow(button.dataset.memberSource);return;}
    else if(button.dataset.boardAction==='select-group')for(const row of targetMembers().get(state.expandedTarget)||[])state.selected.add(row.name);
    else if(button.dataset.boardAction==='members-prev')state.memberPage--;
    else if(button.dataset.boardAction==='members-next')state.memberPage++;
    renderTargets();for(const [name,node] of state.rowNodes)updateRow(state.rowByName.get(name),node);updateCounts();
  });
  $('#target-list').addEventListener('change',event=>{
    if(state.busy||!event.target.dataset.memberSelect)return;
    const name=event.target.dataset.memberSelect;
    if(event.target.checked)state.selected.add(name);else state.selected.delete(name);
    const node=state.rowNodes.get(name);if(node)updateRow(state.rowByName.get(name),node);updateCounts();
  });
  const board=$('#workbench');
  const dragSource=element=>element.closest('.source-button,[data-member-source]');
  function finishBoardDrag() {
    if(!boardDrag)return;
    const active=boardDrag;boardDrag=null;active.ghost?.remove();board.classList.remove('is-dragging');
    document.querySelectorAll('.target-row.drag-over').forEach(node=>node.classList.remove('drag-over'));
    if(board.hasPointerCapture(active.id))board.releasePointerCapture(active.id);
  }
  board.addEventListener('dragstart',event=>{if(dragSource(event.target))event.preventDefault();});
  board.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.pointerType==='touch'||state.busy||boardDrag)return;
    const source=dragSource(event.target);if(!source)return;
    const name=source.dataset.memberSource||source.closest('[data-source]')?.dataset.source,row=state.rowByName.get(name);
    if(!row||row.protected)return;
    boardDrag={id:event.pointerId,name,x:event.clientX,y:event.clientY,source,active:false,ghost:null};
  });
  document.addEventListener('pointermove',event=>{
    const drag=boardDrag;if(!drag||event.pointerId!==drag.id)return;
    if(!drag.active&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6)return;
    if(!drag.active){
      drag.active=true;board.setPointerCapture(event.pointerId);board.classList.add('is-dragging');drag.source.classList.remove('pointer-pressed');
      drag.ghost=document.createElement('div');drag.ghost.className='board-drag-preview';drag.ghost.setAttribute('aria-hidden','true');$('.app').append(drag.ghost);
    }
    event.preventDefault();
    const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-drop-target]');
    document.querySelectorAll('.target-row.drag-over').forEach(node=>{if(node!==target)node.classList.remove('drag-over');});
    target?.classList.add('drag-over');
    const n=state.selected.has(drag.name)?state.selected.size:1;
    drag.ghost.textContent=n+' 個來源 → '+(target?.dataset.dropTarget||'拖至標準目標');
    const x=Math.max(8,Math.min(event.clientX+14,document.documentElement.clientWidth-268)),y=Math.max(8,Math.min(event.clientY+14,innerHeight-48));
    drag.ghost.style.transform='translate3d('+x+'px,'+y+'px,0)';
  },{passive:false});
  document.addEventListener('pointerup',event=>{
    const drag=boardDrag;if(!drag||event.pointerId!==drag.id)return;
    const target=drag.active?document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-drop-target]'):null;
    if(drag.active)suppressBoardClickUntil=performance.now()+150;
    finishBoardDrag();
    if(!target||state.busy)return;
    state.activeTarget=state.expandedTarget=target.dataset.dropTarget;state.memberPage=0;state.focused=drag.name;
    stageSources(state.selected.has(drag.name)?[...state.selected]:[drag.name],state.activeTarget);
  });
  document.addEventListener('pointercancel',event=>{if(boardDrag?.id===event.pointerId)finishBoardDrag();});
  board.addEventListener('lostpointercapture',()=>{if(boardDrag?.active)finishBoardDrag();});
  window.addEventListener('blur',finishBoardDrag);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')finishBoardDrag();});
  document.addEventListener('click',event=>{
    if(performance.now()<suppressBoardClickUntil){event.preventDefault();event.stopPropagation();}
  },true);
  $('#bulk-approve').addEventListener('click',()=>applyBulk());
  $('#bulk-keep').addEventListener('click',()=>applyBulk(true));
  $('#accept-suggestions').addEventListener('click',acceptSuggestions);
  $('#model-suggest-btn').addEventListener('click',modelSuggest);
  $('#undo-btn').addEventListener('click',undo);
  $('#export-btn').addEventListener('click',exportPlan);
  $('#convert-btn').addEventListener('click',convert);
  $('#retry-btn').addEventListener('click',()=>{if(!state.busy)state.retry?.();});
  $('#dismiss-error').addEventListener('click',()=>{clearError();syncControls();});
  $('#review-drafts').addEventListener('change',offerSavedReviews);
  $('#restore-review').addEventListener('click',restoreSavedReview);
  $('#discard-review').addEventListener('click',async()=>{
    if(state.busy||!pendingReview)return;
    const job=pendingReview.identity.jobId;
    if(await choose('刪除保存草稿','只刪除 '+pendingReview.filename+' 的瀏覽器審核草稿；來源圖面與已驗證輸出不受影響。',
      [['discard','刪除這份保存草稿']])!=='discard')return;
    try {localStorage.removeItem(REVIEW_PREFIX+job);offerSavedReviews();}catch {reviewStatus('無法刪除瀏覽器草稿。',true);}
  });
  $('#resume-save').addEventListener('click',async()=>{
    if(state.busy)return;
    if(await choose('以本頁進度繼續保存','另一個分頁已更新同一份草稿。確認後將以本頁的進度取代保存版本；可先匯出映射備份。',
      [['resume','使用本頁進度保存']])!=='resume')return;
    reviewPaused=false;lastReview='';$('#resume-save').hidden=true;saveReview();
  });
  window.addEventListener('storage',event=>{
    if(!state.inventory){offerSavedReviews();return;}
    if(event.key!==REVIEW_PREFIX+state.inventory.job_id)return;
    reviewPaused=true;reviewDirty=true;clearTimeout(reviewTimer);$('#resume-save').hidden=false;
    reviewStatus('另一個分頁已更新或刪除此草稿，本頁暫停自動保存。可先匯出映射，再選擇使用哪份進度。',true);
  });
  window.addEventListener('beforeunload',event=>{
    flushReview();
    if(state.busy||reviewDirty){event.preventDefault();event.returnValue='';}
  });
  window.addEventListener('pagehide',flushReview);
  for(const [id,delta] of [['prev-page',-1],['next-page',1]])$('#'+id).addEventListener('click',()=>{
    state.page+=delta;renderList();updateCounts();$('#mapping-list').scrollTop=0;
  });
  $('#preview-mode').addEventListener('change',drawPreview);
  $('#zoom-in').addEventListener('click',()=>zoom(.8));
  $('#zoom-out').addEventListener('click',()=>zoom(1.25));
  $('#fit-preview').addEventListener('click',()=>{if(state.baseView)setView([...state.baseView]);});
  $('#expand-preview').addEventListener('click',()=>{
    $('#preview-dialog-body').append($('#preview-panel'));
    setText('#preview-dialog-title','幾何證據 · '+(state.focused||'尚未選取'));
    $('#expand-preview').hidden=true;$('#preview-dialog').showModal();$('#close-preview').focus();
  });
  $('#close-preview').addEventListener('click',()=>$('#preview-dialog').close());
  $('#preview-dialog').addEventListener('close',()=>{
    $('#canvas-home').append($('#preview-panel'));$('#expand-preview').hidden=false;$('#expand-preview').focus();
  });
  const svg=$('#preview-svg');
  svg.addEventListener('pointerdown',event=>{
    if(!state.baseView||state.drag||event.button!==0)return;
    const matrix=svg.getScreenCTM();if(!matrix)return;
    state.drag={pointer:event.pointerId,x:event.clientX,y:event.clientY,view:[...state.view],inverse:matrix.inverse()};
    svg.setPointerCapture(event.pointerId);svg.classList.add('dragging');
  });
  svg.addEventListener('pointermove',event=>{
    const drag=state.drag;if(!drag||event.pointerId!==drag.pointer)return;
    const p0=new DOMPoint(drag.x,drag.y).matrixTransform(drag.inverse),p1=new DOMPoint(event.clientX,event.clientY).matrixTransform(drag.inverse);
    const [x,y,w,h]=drag.view;setView([x+p0.x-p1.x,y+p0.y-p1.y,w,h]);
  });
  const endDrag=event=>{
    if(state.drag?.pointer!==event.pointerId)return;
    state.drag=null;svg.classList.remove('dragging');
    if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);
  };
  svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);svg.addEventListener('lostpointercapture',endDrag);
  document.addEventListener('keydown',()=>document.documentElement.classList.add('keyboard-input'),true);
  document.addEventListener('pointerdown',event=>{
    document.documentElement.classList.remove('keyboard-input');
    if(event.pointerType==='mouse')event.target.closest('button:not(:disabled)')?.classList.add('pointer-pressed');
  },true);
  const releasePress=()=>document.querySelectorAll('.pointer-pressed').forEach(node=>node.classList.remove('pointer-pressed'));
  document.addEventListener('pointerup',releasePress,true);document.addEventListener('pointercancel',releasePress,true);
  window.addEventListener('blur',releasePress);
  window.installHoverPreview($('#preview-panel'));
  render();initialize();
})();
