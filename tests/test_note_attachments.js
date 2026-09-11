const test=require('node:test');
const assert=require('node:assert/strict');
const {NoteAttachmentDraft}=require('../static/note-attachments.js');
const file=name=>new File(['synthetic'],name,{type:'text/plain'});
let n=0;const key=()=>String(++n);

test('save a note and existing links once, then associate all new uploads with its project and section',async()=>{
 const calls=[];
 const draft=new NoteAttachmentDraft(async(path,method,data)=>{calls.push({path,method,data});return {id:'n1',project_id:'p1',subsection_id:'s1'};},null,key);
 draft.add([file('a.txt'),file('b.txt')]);
 await draft.save({title:'Note',project_id:'p1',attachment_ids:['existing'],task_action:'create',show_on_timeline:false});
 assert.equal(calls[0].path,'/records/notes');assert.deepEqual(calls[0].data.attachment_ids,['existing']);
 assert.equal(calls[0].data.task_action,'create');assert.equal(calls[0].data.show_on_timeline,false);
 for(const call of calls.slice(1)) {assert.equal(call.path,'/upload');assert.equal(call.data.get('note_id'),'n1');assert.equal(call.data.get('project_id'),'p1');assert.equal(call.data.get('subsection_id'),'s1');}
});
test('partial failures retry only failed uploads without saving the note or creating its todo again',async()=>{
 const calls=[];let fail=true;
 const draft=new NoteAttachmentDraft(async(path,method,data)=>{
  calls.push({path,data});if(path==='/upload'&&data.get('file').name==='b.txt'&&fail)throw new Error('Offline');
  return {id:'n1',project_id:'p1',subsection_id:''};
 },null,key);
 draft.add([file('a.txt'),file('b.txt')]);
 await assert.rejects(draft.save({title:'Original',task_action:'create'}),/沟通记录已保存/);
 fail=false;await draft.save({title:'Changed'});
 assert.equal(calls.filter(x=>x.path==='/records/notes').length,1);
 assert.equal(calls.filter(x=>x.path==='/upload').length,3);
 assert.equal(calls[2].data.get('upload_key'),calls[3].data.get('upload_key'));
});
test('lost create response reuses its creation key and immutable body',async()=>{
 const calls=[];const draft=new NoteAttachmentDraft(async(path,method,data)=>{calls.push(data);if(calls.length===1)throw new Error('Lost response');return {id:'n1',project_id:'p1'};},null,key);
 await assert.rejects(draft.save({title:'Original'}));await draft.save({title:'Changed'});
 assert.equal(calls[0],calls[1]);assert.equal(calls[1].title,'Original');
});
test('edits send version and stale edits stop before uploading',async()=>{
 let count=0;
 const draft=new NoteAttachmentDraft(async(path,method,data)=>{count++;assert.equal(path,'/records/notes/n1');assert.equal(method,'PATCH');assert.equal(data.version,3);throw Object.assign(new Error('Conflict'),{status:409});},{id:'n1',version:3},key);
 draft.add([file('a.txt')]);await assert.rejects(draft.save({attachment_ids:[]}),/请关闭窗口并刷新/);
 assert.equal(count,1);assert.equal(draft.files[0].status,'pending');
});
