'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const links=require('../static/drive-links.js');
const folder='https://drive.google.com/drive/u/0/folders/folder-one?usp=sharing&resourcekey=key-one';
const file='https://drive.google.com/file/d/file-one/view?usp=sharing';
test('recognizes shared Drive resources and preserves access keys and sheet tabs',()=>{
 assert.equal(links.parse(folder).type,'文件夹');assert.match(links.parse(folder).url,/resourcekey=key-one/);
 assert.equal(links.parse(file).key,links.parse('https://drive.google.com/open?id=file-one').key);
 const sheet='https://docs.google.com/spreadsheets/d/sheet-one/edit#gid=42';
 assert.equal(links.parse(sheet).type,'表格');assert.equal(links.parse(sheet).url,sheet);
 for(const bad of ['javascript:alert(1)','http://drive.google.com/file/d/id/view','https://drive.google.com.evil.test/file/d/id/view','https://drive.google.com@evil.test/file/d/id/view','https://evil@drive.google.com/file/d/id/view','https://drive.google.com:444/file/d/id/view','https://drive.google.com\\@evil.test/file/d/id/view','https://drive.google.com/','https://docs.google.com/redirect?url=https://evil.test'])assert.equal(links.parse(bad),null,bad);
});
test('extracts old pasted links without turning arbitrary URLs or text into markup',()=>{
 const body=`大型资料\n${folder}。\n(${file})\n${file}\n<script>alert(1)</script> https://evil.test/`;
 assert.equal(links.extract(body).length,2);
 assert.equal(links.bodyText(body).includes(folder),false);
 assert.match(links.bodyText(body),/<script>alert\(1\)<\/script> https:\/\/evil.test/);
 assert.equal(links.bodyText('纯文字'), '纯文字');
});
test('aggregates only this project, deduplicates pasted references, and retains section sources',()=>{
 const records=[
  {id:'stored',kind:'files',project_id:'p',title:'Known file',url:file},
  {id:'upload',kind:'files',project_id:'p',storage_provider:'gcs',url:'/api/files/upload/content'},
  {id:'n1',kind:'notes',project_id:'p',subsection_id:'s1',body:`${folder}\n${file}`,attachment_ids:['stored','upload']},
  {id:'n2',kind:'notes',project_id:'p',subsection_id:'s2',body:folder,attachment_ids:[]},
  {id:'secret',kind:'notes',project_id:'other',body:'https://drive.google.com/file/d/secret/view'},
 ];
 const before=JSON.stringify(records),files=links.projectFiles(records,'p');
 assert.equal(files.length,3);assert.equal(JSON.stringify(records),before);
 assert.equal(files.filter(f=>f.virtual).length,1);
 assert.deepEqual(files.find(f=>f.virtual).source_note_ids,['n1','n2']);
 assert.equal(links.noteFiles(records,records[2]).length,3);
 assert.equal(links.noteFiles(records,records[3]).length,1);
 const removed=records.map(r=>r.kind==='notes'?{...r,body:'no links'}:r);
 assert.equal(links.projectFiles(removed,'p').length,2);
});
