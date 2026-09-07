'use strict';
const $ = (q, root = document) => root.querySelector(q);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
 home:'<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
 projects:'<rect x="3" y="6" width="18" height="15" rx="2"/><path d="M8 6V3h8v3M3 12h18M10 12v3h4v-3"/>',
 finance:'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18m-5 5h3M7 3v2"/>',
 tasks:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m7 9 2 2 3-4m2 3h3M8 16h9"/>',
 activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>', users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
 plus:'<path d="M12 5v14M5 12h14"/>', refresh:'<path d="M20 7a9 9 0 1 0 1 8M20 2v6h-6"/>',
 arrow:'<path d="m9 5 7 7-7 7"/>', back:'<path d="m14 5-7 7 7 7"/>', close:'<path d="m6 6 12 12M6 18 18 6"/>', check:'<path d="m5 12 4 4L19 6"/>',
 note:'<path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h6"/>', file:'<path d="M14 3H5v18h14V8zM14 3v5h5M8 13h8m-8 4h5"/>', logout:'<path d="M10 3H4v18h6m4-14 5 5-5 5M8 12h12"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.note}</svg>`;
const labels = {home:'工作台',projects:'项目',finance:'财务',tasks:'待办',activity:'操作记录',users:'团队账号'};
const kindLabels = {subsections:'分区',projects:'项目',transactions:'财务记录',tasks:'待办',notes:'沟通笔记',files:'附件',user:'账号'};
let me, records = [], people = [], config = {}, events = [], view = 'home', projectId = null, projectTab = 'notes', search = '', filter = '', taskFilter = '未完成';
let refreshTimer, toastTimer, lastSnapshot, subsectionId = '';
const sectionName = id => find(id)?.title || '未分区';
const inSection = r => !subsectionId || (subsectionId==='unassigned' ? !r.subsection_id : r.subsection_id===subsectionId);
const byKind = kind => records.filter(r => r.kind === kind);
const find = id => records.find(r => r.id === id);
const person = id => people.find(p => p.id === id)?.name || '未指定';
const projectName = id => find(id)?.title || '未关联项目';
const today = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const formatTime = s => new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const cash = (value, currency='USD') => new Intl.NumberFormat('en-US',{style:'currency',currency,maximumFractionDigits:2}).format(value || 0);
const initials = name => esc((name || '?').slice(0,1).toUpperCase());
const badge = status => `<span class="badge ${status==='已完成'?'done':status==='暂停'||status==='未付'?'wait':''}">${esc(status)}</span>`;
function toast(message) {clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').classList.add('visible'); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4000);}
async function api(path, method='GET', data) {
 const opts = {method, credentials:'same-origin',headers:{'X-Cmp-Request':'1'}};
 if (data instanceof FormData) opts.body = data;
 else if (data !== undefined) {opts.body=JSON.stringify(data); opts.headers['Content-Type']='application/json';}
 let response;
 try {response = await fetch('/api'+path,opts);} catch {throw new Error('连接失败，请检查网络后重试。未保存的内容仍保留在表单中。');}
 const result = await response.json().catch(() => ({}));
 if (!response.ok) {
  if(response.status===401 && path!=='/login') {me=null; closeModal(); loginPage();}
  throw new Error(typeof result.detail === 'string' ? result.detail : '请求未完成，请检查输入后重试');
 }
 return result;
}
async function load(render=true) {
 const [state, users, activity] = await Promise.all([api('/state'),api('/users'),api('/activity')]);
 const snapshot=JSON.stringify([state,users,activity]);
 const changed=snapshot!==lastSnapshot;lastSnapshot=snapshot;
 records=state.records;config=state.config;people=users;events=activity;
 if(render && (changed || !$('.workspace'))) draw();
}
function empty(title, text, action, button, symbol='projects') {return `<div class="empty">${icon(symbol)}<h3>${title}</h3><p>${text}</p>${action?`<button class="primary" data-action="${action}">${esc(button)}</button>`:''}</div>`;}
function nav(mobile=false) {return `<nav class="${mobile?'bottom-nav':'nav'}" aria-label="主导航">${(mobile?['home','projects','finance','tasks']:['home','projects','finance','tasks','activity',...(me.role==='admin'?['users']:[])]).filter(v=>v!=='finance'||config.finance_access).map(v=>`<button data-view="${v}" class="${view===v?'active':''}" ${view===v?'aria-current="page"':''}>${icon(v)}<span>${labels[v]}</span></button>`).join('')}</nav>`;}
function draw() {
 if(!me) return;
 const title = projectId && view==='projects' ? '项目记录' : labels[view];
 const subtitles={home:'把公司的每一项工作，记录清楚。',projects:'进度、沟通和资料，在同一个地方。',finance:'每一笔收支，都有据可查。',tasks:'明确负责人，跟进每一个截止日。',activity:'团队的每次更新，都留有记录。',users:'一个人一个账号，每次更新都有署名。'};
 const add={projects:'projects',finance:'transactions',tasks:'tasks',users:'users'}[view];
 $('#root').innerHTML=`${!navigator.onLine?'<div class="network">当前离线。连接网络后才能查看最新记录和保存修改。</div>':''}<div class="shell"><aside class="sidebar"><div class="brand"><img src="/static/icon.svg" alt=""><div>公司工作台<small>Company Manager</small></div></div>${nav()}<button class="account" data-action="account"><span class="avatar">${initials(me.name)}</span><span>${esc(me.name)}<small>${me.role==='admin'?'管理员':'团队成员'}</small></span></button></aside><main class="workspace"><header class="topbar"><div><h1>${esc(title)}</h1><p class="subtle">${subtitles[view]}</p></div><div class="actions"><button class="icon-button refresh" data-action="refresh" aria-label="刷新记录" title="刷新记录">${icon('refresh')}</button>${add&&!projectId?`<button class="primary" data-new="${add}">${icon('plus')}<span>${view==='users'?'添加成员':'新增'}</span></button>`:''}<button class="mobile-account" data-action="account" aria-label="账号与设置"><span class="avatar">${initials(me.name)}</span></button></div></header><div id="page">${renderPage()}</div></main>${nav(true)}</div>`;
}
function renderPage() {return ({home:homePage,projects:projectsPage,finance:financePage,tasks:tasksPage,activity:activityPage,users:usersPage}[view]||homePage)();}
function projectCard(p) {return `<button class="project-card" data-project="${p.id}"><div class="spread"><h3>${esc(p.title)}</h3>${badge(p.status)}</div><p>${esc(p.description || '还没有项目说明')}</p><div class="spread meta"><span>${esc(person(p.owner_id))}</span><span>${p.progress}%</span></div><progress class="progress" value="${p.progress}" max="100" aria-label="${esc(p.title)}进度"></progress><div class="spread meta"><span>${p.due_date?esc(p.due_date)+' 截止':'未设截止日'}</span><span>${byKind('tasks').filter(t=>t.project_id===p.id&&t.status!=='已完成').length} 项待办</span></div></button>`;}
function homePage() {
 const active=byKind('projects').filter(p=>p.status==='进行中'), pending=byKind('tasks').filter(t=>t.status!=='已完成');
 return `<section class="welcome"><div><h2>${esc(me.name)}，欢迎回来</h2><p>${new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date())}<br>${active.length} 个项目正在推进</p></div><div class="welcome-count"><strong>${pending.filter(t=>t.assignee_id===me.id).length}</strong><small>我的未完成待办</small></div></section><div class="two-col"><section><div class="section-title"><h2>正在推进</h2><button class="text-button" data-view="projects">全部项目 ${icon('arrow')}</button></div>${active.length?`<div class="project-grid">${active.slice(0,4).map(projectCard).join('')}</div>`:`<div class="panel">${empty('从一个项目开始','新建项目后，可以一起记录沟通、上传资料、分配待办。','new-project','新建项目')}</div>`}<div class="section-title section-space"><h2>接下来要做</h2><button class="text-button" data-view="tasks">全部待办 ${icon('arrow')}</button></div><div class="panel">${pending.length?pending.slice().sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999')).slice(0,5).map(taskRow).join(''):empty('暂无待办','把需要跟进的事情记录下来，分配给负责的成员。','new-task','添加待办','tasks')}</div></section><section><div class="section-title"><h2>团队动态</h2><button class="text-button" data-view="activity">全部记录 ${icon('arrow')}</button></div><div class="panel">${activityList(events.slice(0,8))}</div><div class="section-title"><h2>常用资料</h2></div><div class="panel">${config.finance_access?`<a class="row" href="${esc(config.sheet_url)}" target="_blank" rel="noopener">${icon('finance')}<div class="row-main"><h3>原始收支记账本</h3><p>查看 Google Sheets 原表</p></div>${icon('arrow')}</a>`:''}<a class="row" href="${esc(config.drive_folder_url)}" target="_blank" rel="noopener">${icon('file')}<div class="row-main"><h3>公司文件夹</h3><p>打开 Google Drive</p></div>${icon('arrow')}</a></div></section></div>`;
}
function searchBar(options, placeholder) {return `<div class="toolbar"><input id="search" type="search" placeholder="${placeholder}" aria-label="${placeholder}" value="${esc(search)}"><select id="filter" aria-label="筛选状态"><option value="">全部状态</option>${options.map(x=>`<option ${filter===x?'selected':''}>${x}</option>`).join('')}</select></div>`;}
function projectsPage() {
 if(projectId) return projectPage();
 const items=byKind('projects').filter(p=>(!filter||p.status===filter)&&JSON.stringify(p).toLowerCase().includes(search.toLowerCase()));
 return searchBar(['进行中','待启动','暂停','已完成'],'搜索项目')+(items.length?`<div class="project-grid">${items.map(projectCard).join('')}</div>`:`<div class="panel">${empty(byKind('projects').length?'没有匹配的项目':'还没有项目','按公司实际开展的工作创建项目，逐步记录进展。','new-project','新建项目')}</div>`);
}
function projectPage() {
 const p=find(projectId);if(!p){projectId=null;return projectsPage();}
 const items=byKind(projectTab).filter(r=>r.project_id===p.id && inSection(r));
 const sections=byKind('subsections').filter(r=>r.project_id===p.id);
 const currentSection=sections.find(r=>r.id===subsectionId);
 const sectionBar=`<section class="subsections"><div class="section-title"><h2>项目分区</h2><button class="text-button" data-new="subsections">${icon('plus')} 新建分区</button></div><nav class="section-options" aria-label="项目分区">${[['','全部记录'],['unassigned','未分区'],...sections.map(r=>[r.id,r.title])].map(([id,title])=>`<button data-section="${id}" class="${subsectionId===id?'active':''}" aria-pressed="${subsectionId===id}">${esc(title)}</button>`).join('')}</nav>${currentSection?`<div class="spread"><p class="hint">${esc(currentSection.description||currentSection.title)}</p><button class="text-button" data-edit="${currentSection.id}">编辑分区</button></div>`:'<p class="hint">按店家、工厂或工作类别，把项目记录分开管理。</p>'}</section>`;
 return `<button class="back" data-action="back-projects">${icon('back')} 全部项目</button><div class="panel detail-head"><div class="spread">${badge(p.status)}<button class="text-button" data-edit="${p.id}">编辑项目</button></div><h2>${esc(p.title)}</h2><p>${esc(p.description||'还没有项目说明')}</p><div class="spread meta"><span>负责人：${esc(person(p.owner_id))}</span><span>${p.progress}%</span></div><progress class="progress" value="${p.progress}" max="100" aria-label="项目进度"></progress><p class="meta">${p.due_date?'截止 '+esc(p.due_date)+' · ':''}最近由 ${esc(person(p.updated_by))} 更新于 ${formatTime(p.updated_at)}</p></div>${sectionBar}<nav class="detail-tabs" aria-label="项目内容">${[['notes','沟通笔记'],['tasks','待办'],['files','附件'],['transactions','收支']].filter(([k])=>k!=='transactions'||config.finance_access).map(([k,v])=>`<button data-tab="${k}" class="${projectTab===k?'active':''}">${v} ${byKind(k).filter(x=>x.project_id===p.id && inSection(x)).length}</button>`).join('')}</nav><div class="section-title"><h2>${{notes:'沟通与进展',tasks:'项目待办',files:'项目资料',transactions:'项目收支'}[projectTab]}</h2><button class="primary" data-new="${projectTab}">${icon('plus')} 添加</button></div>${projectTab==='files'&&!config.drive_upload_ready?'<div class="notice">Google Drive 直接上传尚未连接。现在可以添加已有文件链接，或打开公司文件夹上传后粘贴链接。</div>':''}${items.length?projectTab==='notes'?items.map(noteCard).join(''):`<div class="panel">${items.map(projectTab==='tasks'?taskRow:projectTab==='transactions'?transactionRow:fileRow).join('')}</div>`:`<div class="panel">${empty('暂无'+kindLabels[projectTab],'点击添加，把这个项目的实际信息记录下来。',null,null,projectTab==='files'?'file':'note')}</div>`}`;
}
function noteCard(n) {return `<article class="note"><div class="spread"><h3>${esc(n.title)}</h3><button class="text-button" data-edit="${n.id}">编辑</button></div><p class="meta">${esc(sectionName(n.subsection_id))} · ${esc(n.date)}${n.contact?' · 与 '+esc(n.contact)+' 沟通':''}</p><div class="note-body">${esc(n.body)}</div>${n.linked_task_id&&find(n.linked_task_id)?`<div class="linked-task"><button class="text-button" data-edit="${n.linked_task_id}">${icon('tasks')} ${esc(find(n.linked_task_id).title)}</button>${badge(find(n.linked_task_id).status)}</div>`:''}<p class="meta">${esc(person(n.updated_by))} · ${formatTime(n.updated_at)} <button class="text-button" data-history="${n.id}">修改记录</button></p></article>`;}
function fileRow(f) {return `<div class="row">${icon('file')}<div class="row-main"><h3><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.title)}</a></h3><p>${esc(sectionName(f.subsection_id))} · ${esc(person(f.created_by))} · ${formatTime(f.created_at)}</p></div><button class="text-button" data-edit="${f.id}">编辑</button></div>`;}
function transactionRow(t) {return `<button class="row clickable" data-edit="${t.id}"><div class="row-main"><h3>${esc(t.title)}</h3><p>${esc(t.date)} · ${esc(t.event||projectName(t.project_id))}</p><p>${esc(t.responsible||person(t.created_by))} · ${esc(t.payment_status)}</p></div><div class="amount ${t.direction==='收入'?'income':'expense'}">${t.direction==='收入'?'+':'−'}${cash(t.amount,t.currency)}<small>${t.currency==='CNY'?(t.usd_amount?'折合 '+cash(t.usd_amount):'待确认美元金额'):esc(t.posting_status)}</small></div></button>`;}
function financePage() {
 if(!config.finance_access)return empty('暂无财务权限','请联系管理员。');
 const all=byKind('transactions'), items=all.filter(t=>(!filter||t.direction===filter||t.payment_status===filter)&&JSON.stringify(t).toLowerCase().includes(search.toLowerCase())).sort((a,b)=>b.date.localeCompare(a.date));
 const total=direction=>items.filter(t=>t.direction===direction&&t.usd_amount!==null).reduce((sum,t)=>sum+Math.round(Number(t.usd_amount)*100),0)/100;
 const income=total('收入'),expense=total('支出'),missing=items.filter(t=>t.usd_amount===null).length;
 return `<div class="finance-summary"><div><span>收入 · USD</span><strong class="income">${cash(income)}</strong></div><div><span>支出 · USD</span><strong>${cash(expense)}</strong></div><div><span>收支净额 · USD</span><strong>${cash(income-expense)}</strong></div></div><p class="hint">汇总当前筛选的已录入流水，包含未付记录；不代表银行余额。${missing?` ${missing} 笔缺少美元折算金额，尚未计入。`:''}</p>${searchBar(['收入','支出','已付','未付','部分支付'],'搜索事项、负责人或备注')}<div class="section-title"><h2>收支流水 <span class="meta">${items.length} 笔</span></h2><a class="text-button" href="${esc(config.sheet_url)}" target="_blank" rel="noopener">原始表格</a></div><div class="panel">${items.length?items.map(transactionRow).join(''):empty(all.length?'没有匹配的流水':'开始记录第一笔收支','支持美元、人民币、支付状态与入账情况。原表历史数据尚未导入。','new-transaction','记录收支','finance')}</div>`;
}
function taskRow(t) {const done=t.status==='已完成', overdue=t.due_date&&t.due_date<today()&&!done;return `<div class="row ${done?'completed':''}"><button class="task-check ${done?'checked':''}" data-toggle-task="${t.id}" aria-label="${done?'重新打开':'完成'}：${esc(t.title)}">${done?icon('check'):''}</button><button class="row-main" data-edit="${t.id}"><h3>${esc(t.title)}</h3><p>${esc(person(t.assignee_id))} · ${esc(projectName(t.project_id))}${t.subsection_id?' / '+esc(sectionName(t.subsection_id)):''}</p><p class="${overdue?'overdue':''}">${t.due_date?esc(t.due_date)+(overdue?' 已逾期':' 截止'):'未设截止日'}</p></button>${badge(t.status)}</div>`;}
function tasksPage() {const items=byKind('tasks').filter(t=>(taskFilter==='全部'||taskFilter==='我的待办'&&t.assignee_id===me.id&&t.status!=='已完成'||taskFilter==='未完成'&&t.status!=='已完成'||taskFilter==='已完成'&&t.status==='已完成')&&t.title.toLowerCase().includes(search.toLowerCase())).sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999'));return `<nav class="detail-tabs" aria-label="待办筛选">${['未完成','我的待办','已完成','全部'].map(f=>`<button data-task-filter="${f}" class="${taskFilter===f?'active':''}">${f}</button>`).join('')}</nav><div class="toolbar"><input type="search" id="search" value="${esc(search)}" placeholder="搜索待办" aria-label="搜索待办"></div><div class="panel">${items.length?items.map(taskRow).join(''):empty('这里没有待办','新增要跟进的事项，或切换上面的筛选查看其他任务。','new-task','添加待办','tasks')}</div>`;}
function activityList(items) {return items.length?`<div class="timeline">${items.map(a=>`<div class="event"><button data-audit="${a.id}"><h3><strong>${esc(a.actor_name||'成员')}</strong> ${esc(a.action)}了${esc(kindLabels[a.kind]||a.kind)}<br>${esc(a.after?.title||a.after?.name||a.before?.title||'')}</h3><p>${formatTime(a.at)} · 查看详情</p></button></div>`).join('')}</div>`:empty('还没有团队更新','成员创建或修改记录后，操作人和时间会显示在这里。',null,null,'activity');}
function activityPage() {return `<div class="panel">${activityList(events)}</div><p class="hint">显示最近 100 次操作。每条记录的编辑窗口可以查看该记录的修改历史。</p>`;}
function usersPage() {if(me.role!=='admin')return '';return `<div class="notice">已启用 ${people.filter(p=>p.active).length} / 6 个账号。初始密码由你设置，成员首次登录后必须修改。停用账号会立即退出该成员的所有会话。</div><div class="panel">${people.map(p=>`<div class="user-row"><span class="avatar">${initials(p.name)}</span><div class="row-main"><h3>${esc(p.name)} ${p.role==='admin'?badge('管理员'):''}</h3><p class="meta">${esc(p.username)} · ${p.active?'已启用':'已停用'}${p.must_change?' · 待修改初始密码':''}</p></div>${p.role!=='admin'?`<button class="secondary" data-reset-user="${p.id}">重置密码</button><button class="secondary" data-user-active="${p.id}">${p.active?'停用':'启用'}</button>`:''}</div>`).join('')}</div>`;}
function field(name,label,value='',type='text',extra='') {return `<label class="field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;}
function area(name,label,value='',required=false) {return `<label class="field wide"><span>${label}</span><textarea name="${name}" ${required?'required':''}>${esc(value)}</textarea></label>`;}
function select(name,label,options,value='',required=false) {return `<label class="field"><span>${label}</span><select name="${name}" ${required?'required':''}>${options.map(o=>{const [v,t]=Array.isArray(o)?o:[o,o];return `<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(t)}</option>`;}).join('')}</select></label>`;}
const projectSelect = (value,required=false) => select('project_id','关联项目',[['',required?'请选择项目':'不关联项目'],...byKind('projects').map(p=>[p.id,p.title])],value,required);
const peopleSelect = (name,label,value) => select(name,label,[['','未指定'],...people.filter(p=>p.active).map(p=>[p.id,p.name])],value);
function openModal(title,body) {const modal=$('#modal');modal.innerHTML=`<div class="dialog-head"><h2>${esc(title)}</h2><button data-action="close-modal" aria-label="关闭">${icon('close')}</button></div>${body}`;if(!modal.open)modal.showModal();}
function closeModal() {$('#modal').close();}
function formWrap(content,button='保存记录') {return `<form id="editor"><div class="form-grid">${content}</div><p class="error" role="alert"></p><div class="dialog-footer"><button type="button" class="secondary" data-action="close-modal">取消</button><button class="primary" type="submit">${button}</button></div></form>`;}
function bindSubmit(handler) {$('#editor').addEventListener('submit', async e=>{e.preventDefault();const form=e.currentTarget,button=$('button[type=submit]',form);button.disabled=true;$('.error',form).textContent='';try{await handler(new FormData(form));}catch(error){$('.error',form).textContent=error.message;}finally{button.disabled=false;}});}

function sectionSelect(project, value='') {
 return `<div id="section-field">${select('subsection_id','所属分区',[['','未分区'],...byKind('subsections').filter(r=>r.project_id===project).map(r=>[r.id,r.title])],value)}</div>`;
}
function noteTaskOptions(project, task='') {
 return [['','请选择待办'],...byKind('tasks').filter(t=>t.project_id===project).map(t=>[t.id,`${t.title}（${sectionName(t.subsection_id)} · ${t.status}）`])];
}
function noteTaskControls(data) {
 return `<fieldset class="note-task-controls wide"><legend>关联待办</legend>${select('task_action','保存这条笔记时',[['none','只保存笔记，不关联待办'],['link','关联已有待办'],['create','同时创建一个待办']],data.linked_task_id?'link':'none')}<div id="existing-task" ${data.linked_task_id?'':'hidden'}>${select('linked_task_id','选择待办',noteTaskOptions(data.project_id),data.linked_task_id)}</div><div id="new-task" hidden>${field('task_title','待办标题','','text','maxlength="200" placeholder="默认：跟进：笔记标题"')}${peopleSelect('task_assignee_id','待办负责人',me.id)}${field('task_due_date','待办截止日','','date')}<p class="hint">默认待办与笔记属于同一项目和分区。截止日可留空。</p></div><p class="hint">解除或更换关联不会删除原来的待办。</p></fieldset>`;
}
function syncTaskControls() {
 const mode=$('[name=task_action]')?.value;
 if(!mode)return;
 $('#existing-task').hidden=mode!=='link';$('#new-task').hidden=mode!=='create';
 $('[name=linked_task_id]').required=mode==='link';
}
function refreshProjectFields() {
 const project=$('#editor [name=project_id]')?.value || '';
 if($('#section-field')) $('#section-field').outerHTML=sectionSelect(project);
 const task=$('#existing-task');
 if(task) task.innerHTML=select('linked_task_id','选择待办',noteTaskOptions(project));
 syncTaskControls();
}

function editor(kind,id) {
 if(kind==='users'){userEditor();return;}
 const record=id?find(id):null, data=record||{date:today(),project_id:projectId||'',subsection_id:subsectionId==='unassigned'?'':subsectionId,currency:'USD',direction:'支出'};
 let content='';
 if(kind==='subsections')content=field('title','分区名称',data.title,'text','required maxlength="120" placeholder="例如：店家联系、工厂一联系"')+projectSelect(data.project_id,true)+area('description','分区说明',data.description);
 if(kind==='projects') content=field('title','项目名称',data.title,'text','required maxlength="120"')+select('status','状态',['进行中','待启动','暂停','已完成'],data.status||'进行中')+peopleSelect('owner_id','负责人',data.owner_id)+field('due_date','截止日期',data.due_date,'date')+field('progress','进度（%）',data.progress??0,'number','min="0" max="100" step="1" required')+area('description','项目说明',data.description);
 if(kind==='transactions')content=field('title','事项 / 项目名称',data.title,'text','required maxlength="200"')+field('date','日期',data.date,'date','required')+select('direction','收支',['支出','收入'],data.direction)+select('currency','原币币种',['USD','CNY'],data.currency)+field('amount','原币金额',data.amount,'number','min="0" step="0.01" required')+field('usd_amount','折合美元（人民币流水填写）',data.usd_amount,'number','min="0" step="0.01"')+field('event','事件 / 活动',data.event)+projectSelect(data.project_id)+select('payment_status','支付状态',['未付','已付','部分支付'],data.payment_status||'未付')+field('payment_method','付款形式',data.payment_method,'text','placeholder="现金、支票、转账…"')+select('posting_status','入账状态',['未入账','平帐','部分入账'],data.posting_status||'未入账')+field('booked_amount','入账金额（原币）',data.booked_amount,'number','min="0" step="0.01"')+field('responsible','负责人',data.responsible)+field('category','类别',data.category)+area('note','备注',data.note);
 if(kind==='notes')content=field('title','笔记标题',data.title,'text','required maxlength="200"')+field('date','沟通日期',data.date,'date','required')+field('contact','客户 / 联系人',data.contact)+projectSelect(data.project_id,true)+area('body','沟通内容与项目进展',data.body,true);
 if(kind==='tasks')content=field('title','要做什么',data.title,'text','required maxlength="200"')+select('status','状态',['待办','进行中','已完成'],data.status||'待办')+peopleSelect('assignee_id','负责人',data.assignee_id)+field('due_date','截止日期',data.due_date,'date')+projectSelect(data.project_id)+area('description','补充说明',data.description);
 if(kind==='files')content=field('title','文件名称',data.title,'text','required')+projectSelect(data.project_id,true)+field('url','Google Drive 文件链接',data.url,'url','required placeholder="https://drive.google.com/…"');
 if(['notes','tasks','files','transactions'].includes(kind)) content+=sectionSelect(data.project_id,data.subsection_id);
 if(kind==='notes') content+=noteTaskControls(data);
 if(kind==='tasks'&&id) {const linked=byKind('notes').filter(n=>n.linked_task_id===id);if(linked.length)content+=`<section class="wide"><h3>关联笔记</h3>${linked.map(n=>`<button type="button" class="text-button" data-edit="${n.id}">${icon('note')} ${esc(n.title)}</button>`).join('<br>')}</section>`;}
 const uploads=kind==='files'&&!id?`<div class="notice">${config.drive_upload_ready?'可以直接上传文件到公司 Drive 文件夹。':'直接上传需要管理员配置 Google Drive 授权。可先在公司文件夹上传，再把文件链接填入下面。'} <a href="${esc(config.drive_folder_url)}" target="_blank" rel="noopener">打开文件夹</a></div>${config.drive_upload_ready?'<button class="secondary" data-action="upload-form">从手机 / 电脑上传</button><br><br>':''}`:'';
 openModal((id?'编辑':'新增')+kindLabels[kind],uploads+formWrap(content)+(id?`<p class="hint">创建：${esc(person(record.created_by))} · ${formatTime(record.created_at)}<br>最近修改：${esc(person(record.updated_by))} · ${formatTime(record.updated_at)}<br><button class="text-button" data-history="${id}">查看修改历史</button></p>`:''));
 if(kind==='notes')syncTaskControls();
 if(kind==='subsections'&&id)$('#editor [name=project_id]').disabled=true;
 bindSubmit(async form=>{const body=Object.fromEntries(form);if(kind==='subsections'&&id)body.project_id=record.project_id;if(id)body.version=record.version;await api('/records/'+kind+(id?'/'+id:''),id?'PATCH':'POST',body);closeModal();await load();toast('已保存，更新人：'+me.name);});
}
function userEditor(id) {const p=people.find(x=>x.id===id);openModal(id?'重置 '+p.name+' 的密码':'添加团队成员',formWrap((id?'':field('name','姓名','','text','required maxlength="80"')+field('username','登录账号','','text','required pattern="[a-z0-9_.-]{3,40}" autocomplete="off"'))+field('password','初始密码','','password','required minlength="12" maxlength="128" autocomplete="new-password"'),id?'重置密码':'创建账号'));bindSubmit(async form=>{await api('/users'+(id?'/'+id:''),id?'PATCH':'POST',Object.fromEntries(form));closeModal();await load();toast(id?'密码已重置，请将初始密码交给该成员':'账号已创建，请将账号与初始密码交给该成员');});}
function passwordForm() {openModal('修改密码',formWrap(field('current_password','当前密码','','password','required autocomplete="current-password"')+field('password','新密码（至少 12 位）','','password','required minlength="12" maxlength="128" autocomplete="new-password"')+field('confirm_password','再次输入新密码','','password','required minlength="12" autocomplete="new-password"'),'修改并重新登录'));bindSubmit(async form=>{if(form.get('password')!==form.get('confirm_password'))throw new Error('两次输入的新密码不一致');await api('/password','POST',Object.fromEntries(form));me=null;closeModal();loginPage();toast('密码已更新，请使用新密码登录');});}
function account() {openModal('账号与设置',`<div class="account"><span class="avatar">${initials(me.name)}</span><div>${esc(me.name)}<small>${esc(me.username)} · ${me.role==='admin'?'管理员':'团队成员'}</small></div></div><div class="panel"><button class="row clickable" data-action="password"><div class="row-main"><h3>修改密码</h3></div>${icon('arrow')}</button><button class="row clickable" data-view="activity"><div class="row-main"><h3>操作记录</h3></div>${icon('arrow')}</button>${me.role==='admin'?'<button class="row clickable" data-view="users"><div class="row-main"><h3>团队账号管理</h3></div>'+icon('arrow')+'</button>':''}<button class="row clickable" data-action="refresh"><div class="row-main"><h3>刷新全部记录</h3></div>${icon('refresh')}</button><button class="row clickable" data-action="logout"><div class="row-main"><h3>退出登录</h3></div>${icon('logout')}</button></div><p class="hint">手机安装：iPhone Safari → 分享 → 添加到主屏幕；Android Chrome → 菜单 → 安装应用。使用时需要网络连接。</p>`);}
const fieldLabels={subsection_id:'分区',linked_task_id:'关联待办',title:'标题',name:'姓名',date:'日期',event:'事件',amount:'原币金额',usd_amount:'美元折算金额',currency:'币种',direction:'收支',payment_status:'支付状态',posting_status:'入账状态',booked_amount:'入账金额',payment_method:'付款形式',responsible:'负责人',category:'类别',note:'备注',description:'说明',body:'正文',contact:'联系人',status:'状态',progress:'进度',due_date:'截止日',project_id:'项目',owner_id:'负责人',assignee_id:'负责人',username:'账号',role:'角色',active:'启用',must_change:'需改密码',url:'链接'};
function auditText(data) {if(!data)return '无';return Object.entries(data).filter(([k])=>k!=='id').map(([k,v])=>`${fieldLabels[k]||k}：${k==='project_id'?projectName(v):k==='subsection_id'?sectionName(v):k==='linked_task_id'?(find(v)?.title||'未关联'):['owner_id','assignee_id'].includes(k)?person(v):v??'未填写'}`).join('\n');}
function showAudit(a) {openModal('修改详情',`<p class="meta">${esc(a.actor_name)} · ${formatTime(a.at)} · ${esc(a.action)}</p><div class="audit-columns"><section><h3>修改前</h3><div class="audit-data">${esc(auditText(a.before))}</div></section><section><h3>修改后</h3><div class="audit-data">${esc(auditText(a.after))}</div></section></div>`);}
async function history(id) {const data=await api('/activity?entity_id='+encodeURIComponent(id));openModal('这条记录的修改历史',activityList(data));$('#modal').querySelectorAll('[data-audit]').forEach(button=>button.addEventListener('click',e=>{e.stopPropagation();showAudit(data.find(a=>a.id===button.dataset.audit));}));}
function uploadForm() {openModal('上传项目附件',formWrap(projectSelect(projectId||'',true)+sectionSelect(projectId||'',subsectionId==='unassigned'?'':subsectionId)+field('file','文件（最大 20 MB）','','file','required'),'上传到 Google Drive'));bindSubmit(async form=>{if(form.get('file').size>20*1024*1024)throw new Error('文件不能超过 20 MB');await api('/upload','POST',form);closeModal();await load();toast('文件已保存到 Google Drive');});}
function loginPage() {clearInterval(refreshTimer);$('#root').innerHTML=`<main class="login-shell"><section class="login-intro"><img src="/static/icon.svg" alt="Company Manager"><h1>公司的工作，<br>一起记录。</h1><p>从一笔收支到一个项目。<br>让进度、沟通和每次更新都有迹可循。</p></section><section class="login-form-wrap"><form id="login" class="login-form"><h2>登录公司工作台</h2><p class="subtle">使用管理员分配给你的账号。</p>${field('username','账号','','text','required autocomplete="username" autocapitalize="none"')}${field('password','密码','','password','required autocomplete="current-password"')}<p class="error" role="alert"></p><button class="primary" type="submit">登录</button><p class="hint">忘记密码？请联系管理员 Kevin 重置。</p></form></section></main>`;$('#login').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=$('button',f);b.disabled=true;try{me=await api('/login','POST',Object.fromEntries(new FormData(f)));view='home';projectId=null;search='';filter='';if(me.must_change){forcePassword();}else{await load();startRefresh();}}catch(error){$('.error',f).textContent=error.message;}finally{b.disabled=false;}});}
function forcePassword() {$('#root').innerHTML='<div class="loading">首次登录，请修改初始密码后开始使用。</div>';passwordForm();}
function startRefresh() {clearInterval(refreshTimer);refreshTimer=setInterval(()=>{if(me&&!document.hidden&&!$('#modal').open&&!search)load().catch(()=>{});},30000);}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 try {
  if(b.dataset.view){view=b.dataset.view;projectId=null;subsectionId='';search='';filter='';closeModal();draw();window.scrollTo(0,0);}
  else if(b.dataset.new)editor(b.dataset.new);
  else if(b.dataset.edit){const r=find(b.dataset.edit);editor(r.kind,r.id);}
  else if(b.dataset.project){view='projects';projectId=b.dataset.project;subsectionId='';projectTab='notes';draw();window.scrollTo(0,0);}
  else if(b.hasAttribute('data-section')){subsectionId=b.dataset.section;draw();}
  else if(b.dataset.tab){projectTab=b.dataset.tab;draw();}
  else if(b.dataset.taskFilter){taskFilter=b.dataset.taskFilter;draw();}
  else if(b.dataset.toggleTask){b.disabled=true;const t=find(b.dataset.toggleTask);await api('/records/tasks/'+t.id,'PATCH',{...t,status:t.status==='已完成'?'待办':'已完成'});await load();toast('待办状态已更新');}
  else if(b.dataset.history)await history(b.dataset.history);
  else if(b.dataset.audit){const a=events.find(x=>x.id===b.dataset.audit);if(a)showAudit(a);}
  else if(b.dataset.resetUser)userEditor(b.dataset.resetUser);
  else if(b.dataset.userActive){const u=people.find(p=>p.id===b.dataset.userActive);openModal((u.active?'停用':'启用')+' '+u.name,formWrap(`<p class="wide">${u.active?'停用后，该成员将立即退出登录，历史署名会保留。':'启用后，该成员可以再次登录。'}</p>`,'确认'+(u.active?'停用':'启用')));bindSubmit(async()=>{await api('/users/'+u.id,'PATCH',{active:!u.active});closeModal();await load();toast('账号已更新');});}
  else if(b.dataset.action){switch(b.dataset.action){
   case 'new-project':editor('projects');break;case 'new-task':editor('tasks');break;case 'new-transaction':editor('transactions');break;
   case 'account':account();break;case 'close-modal':if(me?.must_change){me=null;loginPage();}closeModal();break;
   case 'back-projects':projectId=null;draw();break;case 'password':passwordForm();break;case 'upload-form':uploadForm();break;
   case 'refresh':await load();closeModal();toast('已刷新');break;
   case 'logout':await api('/logout','POST');me=null;closeModal();loginPage();break;
  }}
 }catch(error){toast(error.message);b.disabled=false;}
});
document.addEventListener('input',e=>{if(e.target.id==='search'){const pos=e.target.selectionStart;search=e.target.value;$('#page').innerHTML=renderPage();const input=$('#search');input.focus();input.setSelectionRange(pos,pos);}});
document.addEventListener('change',e=>{if(e.target.name==='task_action')syncTaskControls();if(e.target.name==='project_id')refreshProjectFields();if(e.target.id==='filter'){filter=e.target.value;$('#page').innerHTML=renderPage();}});
window.addEventListener('online',()=>{if(me&&!me.must_change)load().catch(e=>toast(e.message));});
window.addEventListener('offline',()=>toast('当前离线，请联网后保存记录'));
$('#modal').addEventListener('cancel',()=>{if(me?.must_change){me=null;loginPage();}});
(async()=>{try{me=await api('/me');if(me.must_change)forcePassword();else{await load();startRefresh();}}catch{loginPage();}if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});})();
