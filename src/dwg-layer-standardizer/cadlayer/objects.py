"""Conservative, local per-object DWG recognition and independently verified routing.

This is a deterministic evidence classifier, not an AI/model or a general plan parser.
Shared block contents are inventoried but never reassigned by this object workflow.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import html
import json
import re
import uuid

from .contracts import validate_standard, verify_actions
from .native import scan_dwg, convert_dwg


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def classify(obj, allowed):
    """Return target, evidence grade, explanation; unsupported evidence stays put."""
    n, kind = obj['source'], obj['type'].upper()
    if obj.get('protected_reason') or n.upper() in ('0', 'DEFPOINTS') or '|' in n:
        return None, 'protected', '系統／外參保護層，保留。'
    if obj['context'] not in ('model', 'paper'):
        return None, 'shared_definition', '共用圖塊定義內部物件保留；不影響其他插入實例。'
    if '拆' in n and '不拆' not in n:
        return None, 'uncertain', '拆除狀態無核准專用目標，保留來源。'
    if kind in ('VIEWPORT', 'ATTDEF', 'WIPEOUT', 'REGION', 'PROXYENTITY'):
        return None, 'uncertain', '輔助或不支援物件，保留。'
    existing, ceiling = n.startswith('0_'), '天花' in n
    target, why = None, ''
    # Intrinsic CAD types are independent object evidence. Layer text supplies scope only.
    if kind == 'DIMENSION':
        target = 'RC-dim' if ceiling else ('A-dim' if existing else 'LA-dim')
        why = '原生 DIMENSION 尺寸物件；依原有／室內／天花用途選擇。'
    elif kind in ('TEXT', 'MTEXT') and (obj.get('text') or '').strip():
        if any(x in n for x in ('圖框', '表格', '外參', '拆除', '出圖範圍')):
            return None, 'uncertain', '圖框表格或參考文字用途未明，保留。'
        target = 'RC-text' if ceiling else ('A-text' if existing else 'LA-text')
        why = '原生文字物件且含文字內容；文字從同層構件中獨立分類。'
    elif kind == 'HATCH' and ('HATCH' in n.upper() or '牆' in n):
        target = 'A-hatch' if existing else 'LA-hatch'
        if ceiling: return None, 'uncertain', '天花填充與輪廓不同，標準無專用填充層，保留。'
        why = '原生 HATCH 填充與來源用途相符。'
    elif kind == 'INSERT':
        name = obj.get('block_name') or ''
        # Anonymous/generated names never constitute semantic evidence.
        if name.startswith(('*', 'A$')):
            return None, 'uncertain', '圖塊名稱為匿名／產生名稱，缺少可確認構件語意。'
        matches = []
        def add(pattern, t, label):
            if re.search(pattern, name, re.I): matches.append((t, label))
        add(r'窗簾|curtain', 'LA-curtain', '窗簾')
        add(r'沙發|餐桌|茶几|椅|床|sofa|chair|table|bed', 'LA-furn', '家具')
        add(r'馬桶|洗手|洗面|面盆|浴缸|淋浴|蓮蓬|水龍頭|小便斗|bathtub|toilet|basin|^toto', 'A-sanitary' if existing else 'LA-sanitary', '衛浴')
        add(r'植栽|盆栽|樹|plant|tree', 'LA-plant', '植栽')
        add(r'出風口|回風口|出回風|空調主機|冷氣主機', 'RC-e&m' if ceiling else '', '空調')
        add(r'吊燈|崁燈|嵌燈|筒燈|壁燈|立燈|吸頂燈|軌道燈|light|lamp', 'RC-lighting' if ceiling else 'LA-light', '燈具')
        add(r'高櫃|全高櫃', 'LA-built-in-full', '高櫃')
        add(r'矮櫃|浮櫃|廚櫃|廚具', 'LA-built-in', '固定櫃')
        if '衣櫃' in name:
            return None, 'uncertain', '衣櫃圖塊的門是櫃體構件，非建築門；櫃高未確認，保留。'
        add(r'柱|\bcolumn\b', 'A-col' if existing else 'LA-col', '柱')
        add(r'隔間牆|\bwall\b', 'A-wall' if existing else 'LA-wall', '牆')
        if not re.search(r'窗簾|curtain|門窗', name, re.I):
            add(r'固定窗|推窗|橫拉窗|氣密窗|\bwindow\b', 'A-wind' if existing else '', '窗')
        add(r'開門|拉門|推門|滑門|摺門|折門|\bdoor\b', 'A-door' if existing else 'LA-door', '門')
        choices = {t for t, _ in matches}
        if len(choices) != 1 or '' in choices:
            return None, 'uncertain', '圖塊名稱無唯一且符合標準範圍的構件用途。'
        target = next(iter(choices))
        # Avoid semantic disagreement between block and its containing layer.
        expected = []
        for word, prefix in [('燈','light'),('衛浴','sanitary'),('家具','furn'),('窗簾','curtain'),('門','door'),('插座','socket'),('開關','switch')]:
            if word in n: expected.append(prefix)
        if expected and not any(x in target for x in expected):
            return None, 'uncertain', '圖塊內容與來源圖層用途衝突，保留待確認。'
        why = f'具名圖塊「{name}」唯一對應 {target}；只移動此 INSERT，內部幾何不拆解。'
    elif kind == 'LWPOLYLINE' and '欄杆' not in n and any(x in n for x in ('隔間牆', '承重柱牆', '矮牆')):
        g = obj.get('geometry') or {}
        pts = g.get('points') or []
        if g.get('closed') and not g.get('truncated') and len(pts) == 4:
            xs, ys = [v[0] for v in pts], [v[1] for v in pts]
            w, h = max(xs)-min(xs), max(ys)-min(ys)
            area = abs(sum(pts[j][0]*pts[(j+1)%4][1]-pts[(j+1)%4][0]*pts[j][1] for j in range(4)))/2
            if w > 0 and h > 0 and max(w,h)/min(w,h) >= 8 and area/(w*h) >= .98 and not g.get('has_bulges'):
                target = 'A-wall' if existing else 'LA-wall'
                why = '封閉四角直線輪廓，包圍盒長寬比至少 8 且面積吻合；來源亦標示牆。柱／短牆不猜測。'
    if target not in allowed:
        return None, 'uncertain', '缺少足以唯一判定的物件證據，或標準無適用目標。'
    return target, 'corroborated', why


def decisions(inventory, standard):
    allowed = {x['name'] for x in standard['layers']}
    result = []
    for obj in inventory.get('objects', []):
        target, grade, reason = classify(obj, allowed)
        result.append({**obj, 'target': target, 'decision': 'map' if target else 'keep',
                       'evidence_grade': grade, 'reason': reason})
    return result


def validate_object_actions(inventory, standard, rows):
    allowed = {x['name'] for x in standard['layers']}
    originals = {x['handle'].upper(): x for x in inventory['objects']}
    seen = set()
    for row in rows:
        key = row['handle'].upper()
        if key in seen or key not in originals:
            raise ValueError('Unknown or duplicate object handle.')
        seen.add(key)
        old = originals[key]
        if any(row.get(k) != old.get(k) for k in ('source', 'type', 'context')):
            raise ValueError('Stale object identity.')
        if row.get('decision') not in ('map', 'keep'):
            raise ValueError('Object requires map or keep.')
        if row['decision'] == 'map':
            if (row.get('target') not in allowed or old.get('protected_reason') or
                    old['source'].upper() in ('0', 'DEFPOINTS') or '|' in old['source'] or
                    old['context'] not in ('model', 'paper')):
                raise ValueError('Protected, shared or nonstandard object assignment.')


def report(out, inventory, standard, rows, conversion=None, error=None):
    e = lambda value: html.escape(str(value))
    changed = [x for x in rows if x['decision'] == 'map' and x['target'] != x['source']]
    layouts = [x for x in rows if x['context'] in ('model','paper')]
    summary = Counter((x['source'], x['target'] or '保留原層') for x in layouts)
    table = ''.join(f'<tr><td>{e(a)}</td><td>{e(b)}</td><td>{c}</td></tr>' for (a,b),c in sorted(summary.items()))
    payload = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    status = '已轉換並通過獨立重開驗證' if conversion else ('轉換失敗，未交付未驗證 DWG' if error else '辨識完成，尚未轉換')
    page = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>逐物件辨識與轉層結果</title><style>body{font:15px system-ui;margin:32px;background:#f3f5f7;color:#172a3d}h1{font-size:30px}p{line-height:1.8;max-width:1100px}.cards{display:flex;flex-wrap:wrap;gap:16px}.card{padding:20px;background:white;border-top:3px solid #286292;min-width:160px}.card b{display:block;font-size:28px}table{width:100%;border-collapse:collapse;background:white;margin-top:16px}td,th{padding:12px;border-bottom:1px solid #dde3ea;text-align:left;vertical-align:top}th{background:#173e63;color:white}input,select,button{padding:10px;margin:8px 5px 8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:500px;font-size:12px}small{color:#5a6a7c}code{overflow-wrap:anywhere}#preview{height:340px;background:#142132;margin-top:16px}svg{width:100%;height:100%}button,summary{cursor:pointer}a{color:#236398}</style><h1>1.2F｜逐物件辨識與轉層結果</h1>'''
    page += f'<p><b>{e(status)}</b><br>{e(standard["name"])} v{e(standard["version"])} · {len(standard["layers"])} 層限定</p>'
    page += f'<div class="cards"><div class="card"><b>{len(rows):,}</b>盤點物件（含定義）</div><div class="card"><b>{len(layouts):,}</b>模型／圖紙物件</div><div class="card"><b>{len(changed):,}</b>實際轉層物件</div><div class="card"><b>{len(rows)-len(changed):,}</b>保留原層物件</div></div>'
    page += '<p>這一版採本機規則辨識：原生物件類型、具名圖塊語意、封閉輪廓幾何，搭配來源圖層確認用途。不是 AI 模型辨識，也未完成整張平面的牆門窗拓樸重建。短牆／柱無法唯一區分時保留。每個決策綁定 Handle；不確定、保護層、共用圖塊定義內部與尺寸產生快取均保留。圖塊只移動 INSERT 實例；內部具獨立圖層的幾何不會因此全部改層。</p>'
    page += '<p>驗證範圍：比對物件 Handle、圖層、數量與非圖層特徵。動態文字欄位比對未求值的欄位公式；AutoCAD 可能在另存／開啟時更新日期顯示快取。編號 1 的整體圖紙視埠範圍為重建快取；浮動視埠則核對位置、尺寸與視角設定。這兩種快取不列入內容雜湊。</p>'
    if conversion:
        page += f'<p><a href="converted/{e(conversion["output"])}">下載測試 DWG 副本</a> · <a href="converted/validation.json">驗證報告</a> · <a href="object-decisions.json">完整辨識紀錄</a></p><details><summary>完整驗證結果</summary><pre>{e(json.dumps(conversion["report"],ensure_ascii=False,indent=2))}</pre></details>'
    if error: page += f'<p>{e(error)}</p>'
    page += '<details><summary>各來源圖層分流結果（模型／圖紙）</summary><table><tr><th>來源</th><th>結果</th><th>物件數</th></tr>'+table+'</table></details>'
    page += '<h2>逐物件結果</h2><p>搜尋 Handle、圖層、圖塊名稱或原因。點選檢視可查看該物件的局部幾何；預覽為抽樣幾何／包圍盒，不是完整渲染。</p><input id="q" placeholder="搜尋物件"><select id="filter"><option value="layout">模型／圖紙</option><option value="changed">轉層物件</option><option value="kept">保留物件</option><option value="all">全部含定義</option></select><button id="prev">上一頁</button><button id="next">下一頁</button><span id="page"></span><div id="preview"></div><table><thead><tr><th>Handle／類型</th><th>來源 → 結果</th><th>判讀原因</th><th>內容</th></tr></thead><tbody id="rows"></tbody></table>'
    page += f'<p>來源 SHA-256：<code>{e(inventory["sha256"])}</code><br>原始檔未覆寫；顏色、線型、線寬覆寫保留。辨識正確率尚無人工標註基準，驗證通過代表轉層與物件完整性符合決策，不代表所有語意判斷已經人工確認。</p><script id="data" type="application/json">{payload}</script>'
    page += '''<script>
const data=JSON.parse(document.getElementById('data').textContent);let page=0,visible=[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(){let q=document.getElementById('q').value.toLowerCase(),f=document.getElementById('filter').value;
visible=data.filter(x=>(f==='all'||(f==='layout'&&x.context!=='block')||(f==='changed'&&x.decision==='map')||(f==='kept'&&x.decision==='keep'))&&JSON.stringify(x).toLowerCase().includes(q));page=Math.min(page,Math.max(0,Math.ceil(visible.length/80)-1));
document.getElementById('page').textContent=`${visible.length} 物件 · 第 ${page+1} / ${Math.max(1,Math.ceil(visible.length/80))} 頁`;
document.getElementById('rows').innerHTML=visible.slice(page*80,page*80+80).map((x,i)=>`<tr><td>${esc(x.handle)}<br>${esc(x.type)}<br><small>${esc(x.context)}</small></td><td>${esc(x.source)}<br>→ <b>${esc(x.target||'保留原層')}</b></td><td>${esc(x.reason)}</td><td><button data-index="${page*80+i}">檢視</button><details><summary>${esc(x.block_name||x.text||'物件證據')}</summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details></td></tr>`).join('');}
function preview(x){let b=x.bounds;if(!b){document.getElementById('preview').textContent='此物件無可用幾何範圍';return;}let w=Math.max(b[2]-b[0],1),h=Math.max(b[3]-b[1],1),pad=Math.max(w,h)*.1,g=x.geometry||{},shape='';
if(g.points)shape=`<polyline points="${g.points.map(p=>p[0]+','+p[1]).join(' ')}${g.closed?' '+g.points[0].join(','):''}"/>`;
else if(g.kind==='circle')shape=`<circle cx="${g.center[0]}" cy="${g.center[1]}" r="${g.radius}"/>`;
else shape=`<rect x="${b[0]}" y="${b[1]}" width="${w}" height="${h}" stroke-dasharray="${Math.max(w,h)/40}"/>`;
document.getElementById('preview').innerHTML=`<svg viewBox="${b[0]-pad} ${-b[3]-pad} ${w+2*pad} ${h+2*pad}" aria-label="局部幾何預覽"><g transform="scale(1,-1)" fill="none" stroke="#8ad6f2" stroke-width="${Math.max(w,h)/250}">${shape}</g></svg>`;}
document.getElementById('rows').onclick=e=>{let b=e.target.closest('button[data-index]');if(b)preview(visible[Number(b.dataset.index)]);};
document.getElementById('q').oninput=()=>{page=0;render()};document.getElementById('filter').onchange=()=>{page=0;render()};document.getElementById('prev').onclick=()=>{page=Math.max(0,page-1);render()};document.getElementById('next').onclick=()=>{page++;render()};render();
</script></html>'''
    (out/'object-results.html').write_text(page, encoding='utf-8')


def run(source, standard_path, out, convert=False):
    if source.suffix.lower() != '.dwg': raise ValueError('Object recognition currently requires native DWG.')
    standard = json.loads(standard_path.read_text(encoding='utf-8-sig'))
    validate_standard(standard)
    out.mkdir(parents=True, exist_ok=False)
    before = digest(source)
    inventory = scan_dwg(source, str(uuid.uuid4()), standard, out)
    if before != digest(source) or before != inventory['sha256'].upper(): raise ValueError('Source hash drift.')
    if not inventory.get('objects'): raise ValueError('Rebuild native scanner: individual objects missing.')
    rows = decisions(inventory, standard)
    validate_object_actions(inventory, standard, rows)
    (out/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'standard.json').write_text(json.dumps(standard,ensure_ascii=False,indent=2),encoding='utf-8')
    receipt = {'schema':'cad-object-decisions/v1','engine':'local-evidence-rules/v1',
               'source_sha256':before,'standard_sha256':digest(standard_path),
               'authorization':'explicit user instruction: conservative test conversion' if convert else 'review only',
               'objects':rows}
    (out/'object-decisions.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    conversion = None
    try:
        if convert:
            actions = [{'source':l['name'],'decision':'keep','target':None} for l in inventory['layers']]
            verify_actions(inventory,standard,actions)
            # Explicit overrides only for layout objects; every other entity stays on its source layer.
            overrides = [{k:x[k] for k in ('handle','source','type','context','decision','target')}
                         for x in rows if x['decision']=='map']
            conversion = convert_dwg(source,inventory,standard,actions,out/'converted',overrides)
            expectation = json.loads((out/'converted/write-expectation.json').read_text(encoding='utf-8'))
            for x in rows:
                if x['context'] not in ('model','paper'): continue
                expected = x['target'] if x['decision']=='map' else x['source']
                if expectation['expected_layers'].get(x['handle']) != expected:
                    raise ValueError('Object plan and native expected state disagree: '+x['handle'])
            if digest(source) != before: raise ValueError('Source changed during conversion.')
        report(out,inventory,standard,rows,conversion)
    except Exception as exc:
        report(out,inventory,standard,rows,error=str(exc))
        raise
    return str(out/'object-results.html')
