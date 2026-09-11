'use strict';
// All previews run locally in the browser. No third-party document viewer.
window.CmpFilePreview = {
 mount(root, blob, filename) {
  let disposed=false, objectURL, loadingTask, pdf, rendering, observer, resizeTimer;
  const status=document.createElement('p');status.className='preview-status';status.setAttribute('role','status');
  const stage=document.createElement('div');stage.className='preview-stage';root.replaceChildren(status,stage);
  const error=message=>{if(!disposed){status.textContent=message;status.classList.add('error');}};
  const ready=(async()=>{
   if(blob.type.startsWith('image/') && ['image/png','image/jpeg','image/gif','image/webp'].includes(blob.type)) {
    status.textContent='正在载入图片…';objectURL=URL.createObjectURL(blob);
    const img=document.createElement('img');img.alt=filename;img.src=objectURL;
    img.onload=()=>{if(!disposed)status.textContent='图片预览';};img.onerror=()=>error('图片无法预览，可以下载原文件。');stage.append(img);return;
   }
   if(blob.type.startsWith('text/plain')) {
    const text=await blob.text();if(disposed)return;
    const pre=document.createElement('pre');pre.textContent=text.slice(0,300000);stage.append(pre);
    status.textContent=text.length>300000?'显示前 30 万字，完整内容请下载。':'文本预览';return;
   }
   if(blob.type!=='application/pdf') {status.textContent='此格式暂不支持在线预览，请下载后打开。';return;}
   status.textContent='正在载入 PDF…';
   const lib=await import('/static/vendor/pdfjs/build/pdf.min.mjs');if(disposed)return;
   lib.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/build/pdf.worker.min.mjs';
   const data=new Uint8Array(await blob.arrayBuffer());if(disposed)return;
   loadingTask=lib.getDocument({data,isEvalSupported:false,enableXfa:false,
    cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,
    standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',wasmUrl:'/static/vendor/pdfjs/wasm/',
    iccUrl:'/static/vendor/pdfjs/iccs/'});
   pdf=await loadingTask.promise;if(disposed){await pdf.destroy();return;}
   const bar=document.createElement('div');bar.className='pdf-controls';bar.setAttribute('aria-label','PDF 翻页与缩放');
   const prev=document.createElement('button');prev.textContent='上一页';prev.className='secondary';
   const next=document.createElement('button');next.textContent='下一页';next.className='secondary';
   const count=document.createElement('span');count.setAttribute('aria-live','polite');
   const zoom=document.createElement('select');zoom.setAttribute('aria-label','PDF 缩放');
   for(const [value,label] of [['fit','适合宽度'],['1','100%'],['1.5','150%'],['2','200%']]){const o=document.createElement('option');o.value=value;o.textContent=label;zoom.append(o);}
   bar.append(prev,count,next,zoom);root.insertBefore(bar,stage);
   const canvas=document.createElement('canvas');stage.append(canvas);let page=1,serial=0;
   const render=async()=>{
    const seq=++serial;if(rendering){rendering.cancel();await rendering.promise.catch(()=>{});}if(disposed||seq!==serial)return;
    prev.disabled=page===1;next.disabled=page===pdf.numPages;count.textContent=`${page} / ${pdf.numPages}`;
    status.textContent='正在绘制第 '+page+' 页…';
    const sheet=await pdf.getPage(page);if(disposed||seq!==serial)return;
    const natural=sheet.getViewport({scale:1});
    const scale=zoom.value==='fit'?Math.max(0.1,(stage.clientWidth-24)/natural.width):Number(zoom.value);
    const viewport=sheet.getViewport({scale});
    const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(12000000/(viewport.width*viewport.height)));
    canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);
    canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';canvas.setAttribute('aria-label',filename+'，第 '+page+' 页');
    rendering=sheet.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]});
    try{await rendering.promise;if(!disposed&&seq===serial){status.textContent='第 '+page+' 页，共 '+pdf.numPages+' 页';stage.scrollTo(0,0);}}
    catch(e){if(e.name!=='RenderingCancelledException')throw e;}
   };
   const refresh=()=>render().catch(()=>error('这一页暂时无法显示，可以重试翻页或下载原文件。'));
   prev.onclick=()=>{if(page>1){page--;refresh();}};next.onclick=()=>{if(page<pdf.numPages){page++;refresh();}};zoom.onchange=refresh;
   let width=stage.clientWidth;
   observer=new ResizeObserver(()=>{if(stage.clientWidth!==width){width=stage.clientWidth;clearTimeout(resizeTimer);resizeTimer=setTimeout(refresh,150);}});observer.observe(stage);
   await render();
  })().catch(e=>error(e.name==='PasswordException'?'这是加密 PDF，请下载后使用密码打开。':'文件暂时无法预览，可以下载原文件。'));
  return {ready,destroy(){disposed=true;clearTimeout(resizeTimer);observer?.disconnect();rendering?.cancel();loadingTask?.destroy().catch(()=>{});if(objectURL)URL.revokeObjectURL(objectURL);root.replaceChildren();}};
 }
};
