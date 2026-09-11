'use strict';
(function(root) {
 const MAX_BYTES=20*1024*1024, MAX_FILES=10;
 class ProjectAttachmentDraft {
  constructor(request, makeKey=()=>crypto.randomUUID()) {
   this.request=request;this.makeKey=makeKey;this.creationKey=makeKey();
   this.files=[];this.project=null;this.body=null;this.busy=false;
  }
  add(files) {
   if(this.busy)throw new Error('请等待本次上传完成');
   const additions=Array.from(files).filter(file=>!this.files.some(x=>x.file.name===file.name&&x.file.size===file.size&&x.file.lastModified===file.lastModified));
   if(this.files.length+additions.length>MAX_FILES)throw new Error('一次最多选择 10 个附件');
   const oversized=additions.find(file=>file.size>MAX_BYTES);
   if(oversized)throw new Error(oversized.name+' 超过 20 MB，请上传到 Google Drive 后在项目附件中添加链接');
   this.files.push(...additions.map(file=>({key:this.makeKey(),file,status:'pending',error:''})));
  }
  remove(key) {if(!this.busy)this.files=this.files.filter(x=>x.key!==key||x.status==='done');}
  async save(body, changed=()=>{}) {
   if(this.busy)throw new Error('正在保存，请稍候');
   this.busy=true;
   try {
    if(!this.project) {
     this.body ||= {...body,creation_key:this.creationKey,with_attachments:this.files.length>0};
     changed();
     try {this.project=await this.request('/records/projects','POST',this.body);}
     catch(error) {if((error.status>=400&&error.status<500)||error.status===503)this.body=null;throw error;}
     changed();
    }
    for(const entry of this.files.filter(x=>x.status!=='done')) {
     entry.status='uploading';entry.error='';changed();
     const form=new FormData();form.append('project_id',this.project.id);form.append('file',entry.file);form.append('upload_key',entry.key);
     try {await this.request('/upload','POST',form);entry.status='done';}
     catch(error) {entry.status='failed';entry.error=error.message;}
     changed();
    }
    const failed=this.files.filter(x=>x.status==='failed').length;
    if(failed)throw new Error(`项目已保存，${failed} 个附件未完成。点击“重试未完成附件”继续；已完成的附件会保留。`);
    return this.project;
   } finally {this.busy=false;changed();}
  }
 }
 const api={ProjectAttachmentDraft,MAX_BYTES,MAX_FILES};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 else root.CmpProjectAttachments=api;
})(typeof window!=='undefined'?window:globalThis);
