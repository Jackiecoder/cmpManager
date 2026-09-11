const test=require('node:test');
const assert=require('node:assert/strict');
const {ProjectAttachmentDraft,MAX_BYTES}=require('../static/project-attachments.js');
const file=name=>new File(['synthetic'],name,{type:'text/plain'});
let n=0;const key=()=>String(++n);

test('multi-file creation saves the project before any upload and keeps membership',async()=>{
 const calls=[];const draft=new ProjectAttachmentDraft(async(path,method,data)=>{calls.push({path,data});return {id:'p1'};},key);
 draft.add([file('one.txt'),file('two.txt')]);
 const members=[{user_id:'member',role:'editor'}];
 await draft.save({title:'Project',member_access:members});
 assert.deepEqual(calls.map(x=>x.path),['/records/projects','/upload','/upload']);
 assert.deepEqual(calls[0].data.member_access,members);
 assert.equal(calls[0].data.with_attachments,true);
 assert.equal(calls[1].data.get('project_id'),'p1');
 assert.ok(draft.files.every(x=>x.status==='done'));
});
test('partial failure retries only the failed attachment without creating another project',async()=>{
 const calls=[];let fail=true;
 const draft=new ProjectAttachmentDraft(async(path,method,data)=>{
  calls.push({path,data});
  if(path==='/upload'&&data.get('file').name==='two.txt'&&fail)throw new Error('Drive unavailable');
  return {id:'p1'};
 },key);
 draft.add([file('one.txt'),file('two.txt')]);
 await assert.rejects(draft.save({title:'Project'}),/项目已保存，1 个附件未完成/);
 assert.deepEqual(draft.files.map(x=>x.status),['done','failed']);
 fail=false;await draft.save({});
 assert.equal(calls.filter(x=>x.path==='/records/projects').length,1);
 assert.equal(calls.filter(x=>x.path==='/upload').length,3);
 assert.equal(calls[2].data.get('upload_key'),calls[3].data.get('upload_key'));
});
test('lost project response retries the same creation key and frozen form values',async()=>{
 const bodies=[];const draft=new ProjectAttachmentDraft(async(path,method,data)=>{
  bodies.push(data);if(bodies.length===1)throw new Error('Network error');return {id:'p1'};
 },key);
 await assert.rejects(draft.save({title:'Original'}));
 await draft.save({title:'Changed'});
 assert.equal(bodies[0],bodies[1]);assert.equal(bodies[1].title,'Original');
});
test('validation failure allows correction; size/count validation happens before saving',async()=>{
 let calls=0;const draft=new ProjectAttachmentDraft(async()=>{calls++;throw Object.assign(new Error('Validation'),{status:400});},key);
 assert.throws(()=>draft.add([{name:'big.pdf',size:MAX_BYTES+1}]),/20 MB/);
 assert.throws(()=>draft.add(Array.from({length:11},(_,i)=>file(i+'.txt'))),/10 个/);
 assert.equal(calls,0);assert.equal(draft.files.length,0);
 await assert.rejects(draft.save({title:''}));assert.equal(draft.body,null);
});
test('in-flight submission cannot run twice or change selected files',async()=>{
 let release;const draft=new ProjectAttachmentDraft(()=>new Promise(resolve=>release=resolve),key);
 const pending=draft.save({title:'Project'});
 await assert.rejects(draft.save({title:'Duplicate'}),/正在保存/);
 assert.throws(()=>draft.add([file('new.txt')]),/等待/);
 release({id:'p1'});await pending;assert.equal(draft.busy,false);
});
