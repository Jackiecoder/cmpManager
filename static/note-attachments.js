'use strict';
(function(root) {
 const {ProjectAttachmentDraft}=typeof module!=='undefined'&&module.exports?require('./project-attachments.js'):root.CmpProjectAttachments;
 class NoteAttachmentDraft extends ProjectAttachmentDraft {
  constructor(request, record=null, makeKey=()=>crypto.randomUUID()) {
   super(request,makeKey);this.record=record;this.saved=null;
  }
  async save(body, changed=()=>{}) {
   if(this.busy)throw new Error('正在保存，请稍候');
   if(!this.saved&&(body.attachment_ids||[]).length+this.files.length>50)throw new Error('每条沟通记录最多关联 50 份附件');
   this.busy=true;
   try {
    if(!this.saved) {
     this.body ||= this.record?{...body,version:this.record.version}:{...body,creation_key:this.creationKey,with_attachments:this.files.length>0};
     changed();
     try {this.saved=await this.request('/records/notes'+(this.record?'/'+this.record.id:''),this.record?'PATCH':'POST',this.body);}
     catch(error) {
      if(error.status>=400&&error.status<500||error.status===503)this.body=null;
      if(this.record&&error.status===409)throw new Error('笔记已更新。请关闭窗口并刷新，核对内容后重新添加附件。');
      throw error;
     }
     changed();
    }
    for(const entry of this.files.filter(x=>x.status!=='done')) {
     entry.status='uploading';entry.error='';changed();
     const form=new FormData();form.append('project_id',this.saved.project_id);form.append('subsection_id',this.saved.subsection_id||'');
     form.append('note_id',this.saved.id);form.append('file',entry.file);form.append('upload_key',entry.key);
     try {await this.request('/upload','POST',form);entry.status='done';}
     catch(error) {entry.status='failed';entry.error=error.message;}
     changed();
    }
    const failed=this.files.filter(x=>x.status==='failed').length;
    if(failed)throw new Error(`沟通记录已保存，${failed} 个附件未完成。可重试未完成附件，或关闭后稍后添加。`);
    return this.saved;
   } finally {this.busy=false;changed();}
  }
 }
 const api={NoteAttachmentDraft};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 else root.CmpNoteAttachments=api;
})(typeof window!=='undefined'?window:globalThis);
