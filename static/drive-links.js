'use strict';
(function(root) {
 // Links are rendered from the already-authorized state; never fetch Drive metadata.
 function parse(value) {
  if(typeof value!=='string'||value.length>2000||/[\\\s]/u.test(value))return null;
  let url;try{url=new URL(value);}catch{return null;}
  if(url.protocol!=='https:'||url.username||url.password||url.port)return null;
  let match,id,type;
  if(url.hostname==='drive.google.com') {
   if((match=url.pathname.match(/^\/drive\/(?:u\/\d+\/)?folders\/([\w-]+)\/?$/))){id=match[1];type='文件夹';}
   else if((match=url.pathname.match(/^\/file\/d\/([\w-]+)(?:\/(?:view|edit|preview))?\/?$/))){id=match[1];type='文件';}
   else if(['/open','/uc'].includes(url.pathname)&&/^[\w-]+$/.test(url.searchParams.get('id')||'')){id=url.searchParams.get('id');type='文件';}
  } else if(url.hostname==='docs.google.com') {
   match=url.pathname.match(/^\/(document|spreadsheets|presentation|forms)\/(?:u\/\d+\/)?d\/(?:e\/)?([\w-]+)(?:\/(?:edit|view|preview|viewform|copy|pub|pubhtml))?\/?$/);
   if(match){id=match[2];type={document:'文档',spreadsheets:'表格',presentation:'演示文稿',forms:'表单'}[match[1]];}
  }
  if(!id)return null;
  return {url:url.href,key:(type==='文件夹'?'folder:':'file:')+id,type,title:'Google Drive '+type};
 }
 function matches(body) {
  const found=[];
  for(const match of (body||'').matchAll(/https:\/\/[^\s<>"'`\u3000-\u303f\u4e00-\u9fff]+/gu)) {
   const raw=match[0].replace(/[.,;:!?，。；：！？、）)\]}]+$/u,'');
   const link=parse(raw);if(link)found.push({...link,start:match.index,end:match.index+raw.length});
  }
  return found;
 }
 function extract(body) {return [...new Map(matches(body).map(link=>[link.key,link])).values()];}
 function bodyText(body) {
  let text='',last=0;
  for(const link of matches(body)){text+=body.slice(last,link.start);last=link.end;}
  return (text+body.slice(last)).trim();
 }
 function projectFiles(records,projectId) {
  const files=records.filter(r=>r.kind==='files'&&r.project_id===projectId).map(f=>({...f,source_note_ids:[]}));
  const byLink=new Map();
  for(const file of files){const link=parse(file.url);if(link&&!byLink.has(link.key))byLink.set(link.key,file);}
  for(const note of records.filter(r=>r.kind==='notes'&&r.project_id===projectId)) {
   for(const file of files)if(note.attachment_ids?.includes(file.id))file.source_note_ids.push(note.id);
   for(const link of extract(note.body)) {
    let file=byLink.get(link.key);
    if(!file){file={id:'note-link:'+note.id+':'+link.key,kind:'files',project_id:projectId,subsection_id:note.subsection_id,title:link.title,url:link.url,created_at:note.created_at,created_by:note.created_by,virtual:true,source_note_ids:[]};files.push(file);byLink.set(link.key,file);}
    // Prefer a link carrying Google's resource key when the same file is pasted twice.
    if(!new URL(file.url).searchParams.has('resourcekey')&&new URL(link.url).searchParams.has('resourcekey'))file.url=link.url;
    if(!file.source_note_ids.includes(note.id))file.source_note_ids.push(note.id);
   }
  }
  return files;
 }
 function noteFiles(records,note) {return projectFiles(records,note.project_id).filter(f=>f.source_note_ids.includes(note.id));}
 const api={parse,extract,bodyText,projectFiles,noteFiles};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CmpDriveLinks=api;
})(typeof window!=='undefined'?window:globalThis);
