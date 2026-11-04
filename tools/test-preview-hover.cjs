'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require(process.env.LAYERLAB_JSDOM_MODULE||'../temp/layerlab-dom-tests/node_modules/jsdom');
function fixture(fine=true){
  const dom=new JSDOM('<div class="app"><h2 id="inspect-name">A-DOOR</h2><div id="home"><div id="drawing"><div class="canvas-toolbar"><button>符合</button></div><div class="canvas-grid"><svg id="preview-svg" viewBox="0 0 80 40"><path d="M0 0L80 40"/></svg><div id="preview-empty" hidden></div></div></div></div><dialog></dialog></div>',{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,timers=new Map();let clock=0,next=0;
  w.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,at:clock+delay});return id;};w.clearTimeout=id=>timers.delete(id);
  const media=new w.EventTarget();media.matches=fine;w.matchMedia=()=>media;
  w.eval(fs.readFileSync('src/dwg-layer-standardizer/ui/preview-hover.js','utf8'));
  const drawing=w.document.querySelector('#drawing'),anchor=drawing.querySelector('.canvas-grid'),button=drawing.querySelector('button');
  const destroy=w.installHoverPreview(drawing);
  function tick(ms){clock+=ms;for(const[id,t]of [...timers])if(t.at<=clock){timers.delete(id);t.fn();}}
  const event=(node,name,props={})=>{const e=new w.Event(name,{bubbles:true});Object.assign(e,props);node.dispatchEvent(e);};
  return {w,drawing,anchor,button,media,event,tick,destroy,close:()=>dom.window.close(),idle:()=>drawing.classList.contains('preview-tools-idle')};
}
test('preview tools fade after leaving, wake immediately, and distinguish mouse focus from keyboard focus',()=>{
  const p=fixture();try{
    p.tick(1499);assert.equal(p.idle(),false);p.tick(1);assert.equal(p.idle(),true);
    p.event(p.drawing,'pointerenter');assert.equal(p.idle(),false);p.tick(3000);assert.equal(p.idle(),false);
    p.event(p.drawing,'pointerdown');p.button.focus();p.event(p.drawing,'pointerleave');
    p.tick(1499);assert.equal(p.idle(),false);p.tick(1);assert.equal(p.idle(),true,'mouse focus must not pin the tools open');
    p.event(p.w.document,'keydown',{key:'Tab'});assert.equal(p.idle(),false);
    p.tick(3000);assert.equal(p.idle(),false,'keyboard interaction keeps controls visible');
    p.button.blur();p.tick(1500);assert.equal(p.idle(),true);
    p.destroy();assert.equal(p.idle(),false);p.event(p.drawing,'pointerleave');p.tick(3000);assert.equal(p.idle(),false);
  }finally{p.close();}
});
test('hover enlargement has no hint, preserves the whole view, and Escape dismisses it; touch controls stay visible',()=>{
  const p=fixture();try{
    assert.equal(p.w.document.querySelector('.preview-enlarge-hint'),null);
    p.event(p.anchor,'pointerenter',{pointerType:'mouse'});p.tick(140);
    const panel=p.w.document.querySelector('.preview-enlargement');assert.equal(panel.hidden,false);
    assert.equal(panel.querySelector('svg').getAttribute('viewBox'),p.anchor.querySelector('svg').getAttribute('viewBox'));
    p.event(p.w.document,'keydown',{key:'Escape'});assert.equal(panel.hidden,true);
    p.event(p.anchor,'pointerleave');p.event(p.anchor,'pointerenter',{pointerType:'touch'});p.tick(140);assert.equal(panel.hidden,true);
    p.media.matches=false;p.event(p.media,'change');p.tick(4000);assert.equal(p.idle(),false);
  }finally{p.close();}
});
test('large geometry dialog keeps controls available and returns the same preview element',()=>{
  const p=fixture();try{
    p.w.document.querySelector('dialog').append(p.drawing);p.event(p.drawing,'pointerleave');p.tick(3000);assert.equal(p.idle(),false);
    p.w.document.querySelector('#home').append(p.drawing);p.event(p.drawing,'pointerleave');p.tick(1500);assert.equal(p.idle(),true);
    p.anchor.focus();assert.equal(p.idle(),false);
  }finally{p.close();}
});
