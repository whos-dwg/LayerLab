// Enlarge the preview surface without changing its SVG viewBox or review state.
(() => {
let nextId=0;
window.installHoverPreview = function(drawing) {
  if(!drawing)return ()=>{};
  const anchor=drawing.querySelector('.canvas-grid'),svg=drawing.querySelector('#preview-svg');
  if(!anchor||!svg)return ()=>{};
  const controller=new AbortController(),{signal}=controller;
  const listen=(node,event,fn,options={})=>node.addEventListener(event,fn,{...options,signal});
  const panel=document.createElement('aside');
  panel.className='preview-enlargement is-light';
  panel.id='preview-enlargement-'+(++nextId);panel.hidden=true;panel.setAttribute('role','tooltip');
  panel.innerHTML='<div class="enlargement-heading"><strong></strong><span>放大縮圖</span></div><div class="enlargement-content"></div><div class="enlargement-footer">移開游標或按 Esc 收回 · 保留目前視野</div>';
  drawing.closest('.app').append(panel);
  const previousTabindex=anchor.getAttribute('tabindex'),previousDescription=anchor.getAttribute('aria-describedby'),previousLabel=anchor.getAttribute('aria-label');
  anchor.tabIndex=0;anchor.setAttribute('aria-describedby',[previousDescription,panel.id].filter(Boolean).join(' '));
  anchor.setAttribute('aria-label','圖形縮圖；懸浮或鍵盤聚焦可暫時放大，按 Escape 收回');
  let timer=null,hovered=false,focused=false,pressed=false,dismissed=false;
  let toolsTimer=null,toolsHovered=false,pointerFocused=false;
  const finePointer=matchMedia('(any-hover: hover) and (any-pointer: fine)');
  const keyboardOwnsPreview=()=>!pointerFocused&&drawing.contains(document.activeElement);
  function showTools(){clearTimeout(toolsTimer);toolsTimer=null;drawing.classList.remove('preview-tools-idle');}
  function hideToolsLater(){
    clearTimeout(toolsTimer);
    if(!finePointer.matches||toolsHovered||keyboardOwnsPreview()||drawing.closest('dialog'))return;
    toolsTimer=setTimeout(()=>{
      toolsTimer=null;
      if(finePointer.matches&&!toolsHovered&&!keyboardOwnsPreview()&&!drawing.closest('dialog'))drawing.classList.add('preview-tools-idle');
    },1500);
  }
  listen(drawing,'pointerenter',()=>{toolsHovered=true;showTools();});
  listen(drawing,'pointerleave',()=>{toolsHovered=false;hideToolsLater();});
  listen(drawing,'focusin',()=>{showTools();if(pointerFocused&&!toolsHovered)hideToolsLater();});
  listen(drawing,'focusout',event=>{if(!drawing.contains(event.relatedTarget))hideToolsLater();});
  listen(drawing,'pointerdown',()=>{pointerFocused=true;showTools();});
  listen(document,'keydown',event=>{if(event.key==='Tab'){pointerFocused=false;if(drawing.contains(document.activeElement))showTools();}});
  listen(finePointer,'change',()=>{showTools();hideToolsLater();});
  hideToolsLater();
  function close(){clearTimeout(timer);timer=null;panel.hidden=true;}
  function position(){
    const r=drawing.getBoundingClientRect(),margin=12,gap=10;
    const width=Math.min(640,Math.max(320,r.width*3),document.documentElement.clientWidth-margin*2);
    const height=Math.min(Math.max(240,width*r.height/r.width),window.innerHeight-margin*2);
    const left=Math.max(margin,Math.min(r.left,document.documentElement.clientWidth-width-margin));
    const above=r.top-height-gap,below=r.bottom+gap;
    const top=above>=margin?above:below+height<=window.innerHeight-margin?below:Math.max(margin,window.innerHeight-height-margin);
    Object.assign(panel.style,{width:width+'px',height:height+'px',left:left+'px',top:top+'px'});
  }
  function sync(){
    panel.querySelector('strong').textContent=document.querySelector('#inspect-name')?.textContent||'圖形預覽';
    const frame=panel.querySelector('.enlargement-content'),empty=drawing.querySelector('#preview-empty');
    if(empty&&!empty.hidden){const message=document.createElement('p');message.textContent=empty.textContent;frame.replaceChildren(message);}
    else {
      const copy=svg.cloneNode(true);copy.removeAttribute('data-preview');copy.removeAttribute('id');copy.setAttribute('aria-hidden','true');copy.removeAttribute('aria-label');
      frame.replaceChildren(copy);
    }
  }
  function open(){timer=null;if(dismissed||pressed||(!hovered&&!focused)||drawing.closest('dialog'))return;sync();position();panel.hidden=false;}
  function schedule(){clearTimeout(timer);timer=setTimeout(open,140);}
  listen(anchor,'pointerenter',e=>{
    if(e.pointerType==='touch'||!matchMedia('(any-hover: hover) and (any-pointer: fine)').matches)return;
    hovered=true;dismissed=false;schedule();
  });
  listen(anchor,'pointerleave',()=>{hovered=false;dismissed=false;close();});
  listen(anchor,'focus',()=>{if(pressed)return;focused=true;dismissed=false;open();});
  listen(anchor,'blur',()=>{focused=false;hovered=false;dismissed=false;close();});
  listen(anchor,'pointerdown',()=>{pressed=true;focused=false;close();});
  listen(window,'pointerup',()=>{if(!pressed)return;pressed=false;if(hovered||focused)schedule();});
  listen(window,'pointercancel',()=>{pressed=false;close();});
  listen(document,'keydown',e=>{if(e.key==='Escape'&&!panel.hidden){dismissed=true;close();}});
  listen(window,'blur',()=>{hovered=focused=false;close();});
  listen(window,'resize',()=>{if(!panel.hidden)position();});
  listen(document,'scroll',()=>{if(!panel.hidden)position();},{capture:true,passive:true});
  const observer=new MutationObserver(()=>{if(!panel.hidden){sync();position();}});
  observer.observe(drawing,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['viewBox','hidden']});
  return ()=>{
    close();clearTimeout(toolsTimer);controller.abort();observer.disconnect();panel.remove();drawing.classList.remove('preview-tools-idle');
    for(const [name,value] of [['tabindex',previousTabindex],['aria-describedby',previousDescription],['aria-label',previousLabel]]){
      if(value===null)anchor.removeAttribute(name);else anchor.setAttribute(name,value);
    }
  };
}

})();
