import { app } from '/scripts/app.js';
import { api } from '/scripts/api.js';
app.registerExtension({
 name: 'ClipSequencePreview.Fixed',
 async beforeRegisterNodeDef(type, data) {
  if(!['PreviousClipPreview', 'ClipSequencePreview'].includes(data.name)) return;
  const created = type.prototype.onNodeCreated;
  type.prototype.onNodeCreated = function() {
   created?.apply(this, arguments);
   const node = this;
   const box = document.createElement('div');
   box.style.cssText = 'padding:8px;color:#ddd;background:#181818;overflow:auto;height:100%;box-sizing:border-box';
   const status = document.createElement('div');
   status.style.whiteSpace = 'pre-wrap';
   const video = document.createElement('video');
   video.controls = true;
   video.style.cssText = 'width:100%;max-height:270px;background:#000;margin-top:8px';
   const buttons = document.createElement('div');
   buttons.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin:8px 0';
   box.append(status, buttons, video);
   const widget = node.addDOMWidget('fixed_preview', 'preview', box, {serialize:false});
   widget.computeSize = () => [440, 380];
   const value = name => node.widgets?.find(w=>w.name===name)?.value;
   node.showClipState = state => {
    node.clipState = state;
    status.textContent = `已固定 ${state.fixed.length} 段\n` + state.fixed.map((x,i)=>`${i+1}. ${x.name}`).join('\n') + `\n候选：${state.candidate?.name || '无（等待生成或导入）'}`;
    if(state.preview) { video.src=api.apiURL('/view?'+new URLSearchParams(state.preview)); video.hidden=false; }
    else { video.removeAttribute('src'); video.load(); video.hidden=true; }
   };
   const refresh = async()=>{
    const response=await api.fetchApi('/fixed-clip-preview/state?'+new URLSearchParams({session:value('session'),node:String(node.id)}));
    if(!response.ok) throw new Error('读取状态失败');
    node.showClipState(await response.json());
   };
   let busy=false;
   const perform=async(action,path='')=>{
    const response=await api.fetchApi('/fixed-clip-preview/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:value('session'),node:String(node.id),fps:value('frame_rate'),revision:node.clipState?.revision,action,path})});
    const result=await response.json(); if(!response.ok) throw new Error(result.error); node.showClipState(result);
   };
   const button=(text,fn)=>{
    const b=document.createElement('button'); b.textContent=text; buttons.append(b);
    b.onclick=async()=>{if(busy)return;busy=true; buttons.querySelectorAll('button').forEach(x=>x.disabled=true);
     try { await fn(); } catch(error){alert(error.message);}
     finally {busy=false;buttons.querySelectorAll('button').forEach(x=>x.disabled=false);}
    };
   };
   button('固定候选为下一段',()=>perform('fix'));
   button('不固定／舍弃候选',()=>perform('discard'));
   button('取消最后一段固定',()=>perform('undo'));
   button('导入路径',async()=>{const path=prompt('输入视频完整路径');if(path?.trim())await perform('import',path);});
   const input=document.createElement('input'); input.type='file';input.accept='.mp4,.mov,.webm,.mkv';input.hidden=true;box.append(input);
   button('选择本地视频',()=>input.click());
   input.onchange=async()=>{
    const file=input.files[0];if(!file||busy)return;busy=true;buttons.querySelectorAll('button').forEach(x=>x.disabled=true);
    try {const form=new FormData();form.append('file',file);const response=await api.fetchApi('/fixed-clip-preview/upload',{method:'POST',body:form});const result=await response.json();if(!response.ok)throw new Error(result.error);await perform('import',result.path);}
    catch(error){alert(error.message);}finally{input.value='';busy=false;buttons.querySelectorAll('button').forEach(x=>x.disabled=false);}
   };
   button('刷新片段状态',refresh);
   status.textContent='生成完成后，先预览，再固定；未固定的候选会被下一次生成替换。';
   const old=node.onRemoved;node.onRemoved=function(){video.pause();video.removeAttribute('src');old?.apply(this,arguments);};
   setTimeout(()=>refresh().catch(error=>{status.textContent=error.message;}),600);
   node.setSize([500,600]);
  };
  const executed=type.prototype.onExecuted;
  type.prototype.onExecuted=function(message){executed?.apply(this,arguments);if(message.fixed_clip_state?.[0])this.showClipState?.(message.fixed_clip_state[0]);};
 }
});
