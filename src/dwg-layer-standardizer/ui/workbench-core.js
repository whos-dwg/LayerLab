/* Domain rules shared by the browser and dependency-free regression tests. */
((root, factory) => {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LayerLabCore = api;
})(typeof globalThis === 'object' ? globalThis : this, () => {
  'use strict';
  const pending = source => ({source, decision:'pending', target:null, reason:'pending_human_review'});
  const actionFor = (actions, row) => actions.get(row.name) || pending(row.name);
  const humanApproved = (row, actions) => !row.protected && ['map','keep'].includes(actionFor(actions,row).decision);
  const approved = (row, actions) => !!row.protected || humanApproved(row,actions);
  function status(row, actions, suggestions) {
    if (row.protected) return ['protected','系統保護'];
    if (humanApproved(row,actions)) return ['approved','已審核'];
    return suggestions.get(row.name)?.target ? ['pending','待確認'] : ['unknown','待判讀'];
  }
  function summary(rows, actions, suggestions) {
    const counts = {total:rows.length, manual:0, protected:0, suggested:0, unknown:0, pending:0};
    for (const row of rows) {
      if (row.protected) counts.protected++;
      else if (humanApproved(row,actions)) counts.manual++;
      else if (suggestions.get(row.name)?.target) counts.suggested++;
      else counts.unknown++;
    }
    counts.pending = counts.suggested + counts.unknown;
    counts.reviewable = counts.total - counts.protected;
    return counts;
  }
  function filterRows(rows, actions, suggestions, filter, query, searchIndex) {
    const q = query.trim().toLowerCase();
    return rows.filter(row => {
      const text = searchIndex?.get(row.name) ?? [row.name,...Object.keys(row.types||{}),...Object.keys(row.block_names||{})].join(' ').toLowerCase();
      if (q && !text.includes(q)) return false;
      const done = approved(row,actions), suggested = !!suggestions.get(row.name)?.target;
      return filter === 'pending' ? !done : filter === 'suggested' ? !done && suggested
        : filter === 'unknown' ? !done && !suggested : filter === 'approved' ? humanApproved(row,actions)
        : filter === 'protected' ? !!row.protected : true;
    });
  }
  function selectionState(rows, selected) {
    const selectable = rows.filter(row => !row.protected);
    const n = selectable.filter(row => selected.has(row.name)).length;
    return {checked:selectable.length>0 && n===selectable.length, indeterminate:n>0 && n<selectable.length, disabled:!selectable.length};
  }
  function createActions(rows) {
    return new Map(rows.map(row => [row.name,row.protected
      ? {source:row.name,decision:'keep',target:null,reason:'system_protected'} : pending(row.name)]));
  }
  function draftTarget(row, actions, suggestions, drafts, standard) {
    if (!row || row.protected || actionFor(actions,row).decision==='keep') return '';
    const action=actionFor(actions,row);
    const target=action.decision==='map'?action.target:drafts.has(row.name)?drafts.get(row.name):suggestions.get(row.name)?.target;
    return (standard?.layers||[]).some(layer=>layer.name===target)?target:'';
  }
  function stageMapping(rows, actions, drafts, names, target, standard) {
    if (!(standard?.layers||[]).some(layer=>layer.name===target)) return {ok:false,error:'請先選擇標準目標。'};
    const selected=new Set(names),eligible=rows.filter(row=>selected.has(row.name)&&!row.protected);
    const nextActions=new Map(actions),nextDrafts=new Map(drafts);
    for(const row of eligible){nextActions.set(row.name,pending(row.name));nextDrafts.set(row.name,target);}
    return {ok:eligible.length>0,actions:nextActions,drafts:nextDrafts,count:eligible.length};
  }
  function confirmMappings(rows, actions, suggestions, drafts, names, standard, keep=false) {
    const selected=new Set(names),eligible=rows.filter(row=>selected.has(row.name)&&!row.protected);
    if(!eligible.length)return {ok:false,error:'請先勾選可審核的來源圖層。'};
    // Validate every target before accepting any member of a batch.
    if(!keep&&eligible.some(row=>!draftTarget(row,actions,suggestions,drafts,standard)))
      return {ok:false,error:'勾選圖層中仍有未指定目標的來源；請先指定後再確認。'};
    const next=new Map(actions);
    for(const row of eligible)next.set(row.name,{source:row.name,decision:keep?'keep':'map',
      target:keep?null:draftTarget(row,actions,suggestions,drafts,standard),reason:eligible.length>1?'human_bulk_review':'human_review'});
    return {ok:true,actions:next,count:eligible.length};
  }
  function compatibleActions(rows, actions, oldStandard, newStandard) {
    // Compare CAD output properties, not aliases or optional JSON serialization defaults.
    const spec = t => JSON.stringify([t.aci??7,String(t.linetype||'CONTINUOUS').toUpperCase(),t.lineweight??-3,t.plot??true]);
    const oldTargets = new Map((oldStandard.layers||[]).map(t=>[t.name,spec(t)]));
    const targets = new Map((newStandard.layers||[]).map(t=>[t.name,spec(t)]));
    const next = createActions(rows);
    let preserved = 0;
    for (const row of rows) {
      const act = actionFor(actions,row);
      if (!humanApproved(row,actions)) continue;
      if (act.decision==='keep' || (targets.has(act.target) && targets.get(act.target)===oldTargets.get(act.target))) {
        next.set(row.name,{...act}); preserved++;
      }
    }
    return {actions:next,preserved};
  }
  function stableJSON(value) {
    if(Array.isArray(value))return '['+value.map(stableJSON).join(',')+']';
    if(value&&typeof value==='object')return '{'+Object.keys(value).sort().filter(k=>value[k]!==undefined)
      .map(k=>JSON.stringify(k)+':'+stableJSON(value[k])).join(',')+'}';
    return JSON.stringify(value);
  }
  async function fingerprint(value) {
    if(!globalThis.crypto?.subtle)throw new Error('此瀏覽器無法核對草稿來源，請使用匯出映射保存。');
    const bytes=new TextEncoder().encode(stableJSON(value));
    return [...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
  }
  function reviewSnapshot(state, identity) {
    return {schema:'layerlab-review/v1',identity:{...identity},filename:state.inventory.filename,
      standardName:state.standardLabel||'default',standardLabel:state.standardLabel||'default',standardVersion:state.standard.version,
      actions:[...state.actions.values()].map(a=>({...a})),drafts:[...state.drafts],
      suggestions:[...state.suggestions].map(([name,s])=>[name,{...s}]),suggestionEngine:state.suggestionEngine,
      focused:state.focused,selected:[...state.selected]};
  }
  function restoreReview(record, inventory, standard, identity) {
    if(record?.schema!=='layerlab-review/v1'||!record.identity||!Array.isArray(record.actions)||
      !Array.isArray(record.drafts)||!Array.isArray(record.suggestions))throw new Error('草稿格式不完整，請重新匯入圖面。');
    if(record.identity.jobId!==inventory.job_id||record.identity.inventory!==identity.inventory)
      throw new Error('來源圖面的盤點內容已變更，不能沿用這份草稿的核准結果。請重新匯入圖面。');
    if(record.identity.standard!==identity.standard)throw new Error('目前標準與保存時不同，不能沿用舊核准。請切回原標準或重新審核。');
    const sourceRows=inventory.layers,byName=new Map(sourceRows.map(r=>[r.name,r])),targets=new Set(standard.layers.map(t=>t.name));
    const actions=createActions(sourceRows),drafts=new Map(),suggestions=new Map(),seen=new Set();
    if(record.actions.length!==sourceRows.length)throw new Error('草稿來源清單不完整，請重新匯入圖面。');
    for(const a of record.actions) {
      const row=byName.get(a?.source);
      if(!row||seen.has(a.source)||!['map','keep','pending'].includes(a.decision)||
        (a.decision==='map'&&(!targets.has(a.target)||row.protected))||
        (a.decision!=='map'&&a.target!=null))throw new Error('草稿含有無效的映射決策，未恢復任何核准。');
      seen.add(a.source);
      if(!row.protected)actions.set(a.source,{source:a.source,decision:a.decision,target:a.decision==='map'?a.target:null,
        reason:typeof a.reason==='string'?a.reason:'restored_human_review'});
    }
    for(const entry of record.drafts) {
      if(!Array.isArray(entry)||entry.length!==2||!byName.has(entry[0])||byName.get(entry[0]).protected||
        !targets.has(entry[1])||drafts.has(entry[0]))throw new Error('草稿含有無效的暫定目標，未恢復任何核准。');
      drafts.set(entry[0],entry[1]);
    }
    for(const entry of record.suggestions) {
      if(!Array.isArray(entry)||entry.length!==2||!byName.has(entry[0])||!entry[1]||
        (entry[1].target&&!targets.has(entry[1].target))||suggestions.has(entry[0]))throw new Error('草稿的建議內容無效，請重新審核。');
      suggestions.set(entry[0],{...entry[1],source:entry[0]});
    }
    return {actions,drafts,suggestions,standardLabel:typeof record.standardLabel==='string'&&record.standardLabel.trim()&&record.standardLabel.length<=260?record.standardLabel:'default',
      suggestionEngine:record.suggestionEngine==='semantic-model'?'semantic-model':'rules',
      focused:byName.has(record.focused)?record.focused:sourceRows.find(r=>!r.protected)?.name||null,
      selected:new Set((Array.isArray(record.selected)?record.selected:[]).filter(name=>byName.has(name)&&!byName.get(name).protected))};
  }
  function geometry(g) {
    let points=[];
    if (g.kind==='line' || g.kind==='polyline') points=g.points||[];
    if (g.kind==='circle' || g.kind==='arc') {
      const start=g.kind==='circle'?0:g.start;
      const end=g.kind==='circle'?360:(g.end<g.start?g.end+360:g.end);
      const count=Math.max(2,Math.min(72,Math.ceil((end-start)/8)));
      if (Array.isArray(g.center)) for(let i=0;i<=count;i++) {
        const a=(start+(end-start)*i/count)*Math.PI/180;
        points.push([g.center[0]+Math.cos(a)*g.radius,g.center[1]+Math.sin(a)*g.radius]);
      }
    }
    // Reject malformed geometry rather than bridging disconnected invalid vertices.
    if (points.length<2 || points.some(p=>!Array.isArray(p)||!Number.isFinite(p[0])||!Number.isFinite(p[1]))) return null;
    return {points,closed:!!g.closed || g.kind==='circle'};
  }
  function bounds(geometries) {
    let minx=Infinity,miny=Infinity,maxx=-Infinity,maxy=-Infinity;
    for(const g of geometries) for(const [x,y] of g.points) {
      minx=Math.min(minx,x);maxx=Math.max(maxx,x);miny=Math.min(miny,-y);maxy=Math.max(maxy,-y);
    }
    if (!Number.isFinite(minx)) return null;
    const w=Math.max(maxx-minx,1),h=Math.max(maxy-miny,1),pad=Math.max(w,h)*.08;
    return [minx-pad,miny-pad,w+pad*2,h+pad*2];
  }
  return {pending,actionFor,humanApproved,approved,status,summary,filterRows,selectionState,createActions,draftTarget,stageMapping,confirmMappings,compatibleActions,stableJSON,fingerprint,reviewSnapshot,restoreReview,geometry,bounds};
});
