'use strict';
const $ = (q, root = document) => root.querySelector(q);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
 home:'<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
 projects:'<rect x="3" y="6" width="18" height="15" rx="2"/><path d="M8 6V3h8v3M3 12h18M10 12v3h4v-3"/>',
 finance:'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18m-5 5h3M7 3v2"/>',
 inventory:'<path d="m3 8 9-5 9 5v13H3zM3 8h18M7 21V11h10v10M7 16h10"/>',
 tasks:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m7 9 2 2 3-4m2 3h3M8 16h9"/>',
 activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>', users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
 plus:'<path d="M12 5v14M5 12h14"/>', refresh:'<path d="M20 7a9 9 0 1 0 1 8M20 2v6h-6"/>',
 arrow:'<path d="m9 5 7 7-7 7"/>', back:'<path d="m14 5-7 7 7 7"/>', close:'<path d="m6 6 12 12M6 18 18 6"/>', check:'<path d="m5 12 4 4L19 6"/>',
 note:'<path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h6"/>', file:'<path d="M14 3H5v18h14V8zM14 3v5h5M8 13h8m-8 4h5"/>', logout:'<path d="M10 3H4v18h6m4-14 5 5-5 5M8 12h12"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.note}</svg>`;
const labels = {home:'工作台',projects:'项目',finance:'财务',inventory:'库存',tasks:'待办',activity:'操作记录',users:'账号管理',team:'公司成员',security:'账号安全'};
const kindLabels = {warehouses:'仓库',products:'商品',stock_movements:'出入库记录',finance_profiles:'财务账本',subsections:'分区',projects:'项目',transactions:'财务记录',tasks:'待办',notes:'沟通笔记',files:'附件',user:'账号',workspace:'团队',membership:'公司成员',resource_access:'访问权限',maintenance:'数据维护'};
let me, records = [], people = [], config = {}, events = [], view = 'home', projectId = null, projectTab = 'notes', search = '', filter = '', taskFilter = '未完成';
let refreshTimer, toastTimer, lastSnapshot, subsectionId = '';
let financeProfileId = '', reimbursementFilter = '';
const financeProfileName = id => find(id)?.title || '默认账本';
const sectionName = id => find(id)?.title || '未分区';
const inSection = r => !subsectionId || (subsectionId==='unassigned' ? !r.subsection_id : r.subsection_id===subsectionId);
const byKind = kind => records.filter(r => r.kind === kind);
const find = id => records.find(r => r.id === id);
const person = id => people.find(p => p.id === id)?.name || '未指定';
const projectName = id => {
 const active=find(id),historical=records.find(r=>r.project_id===id&&r._project_deleted);
 return active?.title || (historical?historical._project_title+'（已删除）':id?'未授权项目':'未关联项目');
};
const today = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const formatTime = s => new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const cash = (value, currency='USD') => new Intl.NumberFormat('en-US',{style:'currency',currency,maximumFractionDigits:2}).format(value || 0);
const initials = name => esc((name || '?').slice(0,1).toUpperCase());
const badge = status => `<span class="badge ${status==='已完成'?'done':status==='暂停'||status==='未付'?'wait':''}">${esc(status)}</span>`;
function toast(message) {clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').classList.add('visible'); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4000);}
async function api(path, method='GET', data) {
 const opts = {method, credentials:'same-origin',headers:{'X-Cmp-Request':'1','X-Cmp-Workspace':workspaceId}};
 if(maintenanceId)opts.headers['X-Cmp-Maintenance']=maintenanceId;
 if (data instanceof FormData) opts.body = data;
 else if (data !== undefined) {opts.body=JSON.stringify(data); opts.headers['Content-Type']='application/json';}
 let response;
 try {response = await fetch('/api'+path,opts);} catch {throw new Error('连接失败，请检查网络后重试。未保存的内容仍保留在表单中。');}
 const result = await response.json().catch(() => ({}));
 if (!response.ok) {
  if(response.status===401 && path!=='/login') {me=null; closeModal(true); loginPage();}
  const error=new Error(typeof result.detail === 'string' ? result.detail : '请求未完成，请检查输入后重试');error.status=response.status;throw error;
 }
 return result;
}
async function load(render=true) {
 const epoch=++loadEpoch, requested=workspaceId;
 let responses;
 try {responses=await Promise.all([api('/state'),api('/users'),api('/activity'),me.role==='admin'?api('/admin/users'):Promise.resolve([]),api('/maintenance'),api('/account/activity')]);}
 catch(error){if(epoch===loadEpoch&&error.status===403&&requested!=='personal'&&me){await switchWorkspace('personal');toast('空间权限已变化，已返回个人空间');return;}throw error;}
 if(epoch!==loadEpoch||!me)return;
 const [state,users,activity,accounts,maintenance,security]=responses;
 const snapshot=JSON.stringify(responses), changed=snapshot!==lastSnapshot;lastSnapshot=snapshot;
 records=state.records;config=state.config;people=users;events=activity;allAccounts=accounts;maintenanceItems=maintenance;accountEvents=security;
 currentWorkspace=state.workspace;availableSpaces=state.workspaces;workspaceId=currentWorkspace.id;syncMaintenanceTimer();
 if(!config.full_access&&!config.ledger_access?.[financeProfileId])financeProfileId=Object.keys(config.ledger_access||{})[0]||'';
 if(projectId&&!find(projectId))projectId=null;
 if(render && (changed || !$('.workspace'))) draw();
}
function empty(title, text, action, button, symbol='projects') {return `<div class="empty">${icon(symbol)}<h3>${title}</h3><p>${text}</p>${action?`<button class="primary" data-action="${action}">${esc(button)}</button>`:''}</div>`;}
function nav(mobile=false) {return `<nav class="${mobile?'bottom-nav':'nav'}" aria-label="主导航">${(mobile?['home','projects','inventory','finance','tasks']:['home','projects','inventory','finance','tasks','activity',...(me.role==='admin'?['users']:[])]).filter(v=>v!=='finance'||config.finance_access).map(v=>`<button data-view="${v}" class="${view===v?'active':''}" ${view===v?'aria-current="page"':''}>${icon(v)}<span>${labels[v]}</span></button>`).join('')}</nav>`;}
function draw() {
 if(!me) return;
 const title = projectId && view==='projects' ? '项目记录' : view==='home'&&currentWorkspace?.kind==='personal'?'个人工作台':labels[view];
 const subtitles={home:'把公司的每一项工作，记录清楚。',projects:'进度、沟通和资料，在同一个地方。',inventory:'每一笔出入库，都能追溯到项目和收支。',finance:'每一笔收支，都有据可查。',tasks:'明确负责人，跟进每一个截止日。',activity:'团队的每次更新，都留有记录。',users:'管理账号、初始密码与数据维护。',team:'按团队设置每位成员的访问权限。',security:'查看密码操作和个人空间的维护访问。'};
 if(view==='home'&&currentWorkspace?.kind==='personal')subtitles.home='记录你自己的项目、财务与待办。';
 const add={projects:'projects',inventory:'stock_movements',finance:'transactions',tasks:'tasks',users:'users'}[view];
 $('#root').innerHTML=`${!navigator.onLine?'<div class="network">当前离线。连接网络后才能查看最新记录和保存修改。</div>':''}<div class="shell"><aside class="sidebar"><div class="brand"><img src="/static/icon.svg" alt=""><div>公司工作台<small>Company Manager</small></div></div>${nav()}<button class="account" data-action="account"><span class="avatar">${initials(me.name)}</span><span>${esc(me.name)}<small>${me.role==='admin'?'管理员':'团队成员'}</small></span></button></aside><main class="workspace"><header class="topbar"><div><h1>${esc(title)}</h1><p class="subtle">${subtitles[view]}</p></div><div class="actions"><button class="icon-button refresh" data-action="refresh" aria-label="刷新记录" title="刷新记录">${icon('refresh')}</button>${add&&!projectId&&canCreate(add)?`<button class="primary" data-new="${add}">${icon('plus')}<span>${view==='users'?'添加成员':'新增'}</span></button>`:''}<button class="mobile-account" data-action="account" aria-label="账号与设置"><span class="avatar">${initials(me.name)}</span></button></div></header>${workspaceControls()}<div id="page">${renderPage()}</div></main>${nav(true)}</div>`;
 applyTimelineColors();applyWorkspaceAccess();
}
function renderPage() {return ({home:homePage,projects:projectsPage,inventory:inventoryPage,finance:financePage,tasks:tasksPage,activity:activityPage,users:usersPage,team:teamPage,security:securityPage}[view]||homePage)();}
function projectCard(p) {
 const latest = CmpTimeline.notesForProject(records, p.id)[0];
 return `<button class="project-card" data-project="${p.id}"><div class="spread"><h3>${esc(p.title)}</h3>${badge(p.status)}</div><p>${esc(p.description || '还没有项目说明')}</p>
 <div class="card-timeline"><span class="meta">最近时间线</span>${latest ? `<div class="card-timeline-entry" data-timeline-project="${p.id}" data-timeline-section="${esc(latest.subsection_id||'')}"><span class="timeline-section">${esc(sectionName(latest.subsection_id))} · <time datetime="${esc(latest.date)}">${esc(latest.date)}</time></span><strong>${esc(latest.title)}</strong></div>` : '<span class="meta">暂无时间线记录</span>'}</div>
 <div class="spread meta"><span>${p.due_date?esc(p.due_date)+' 截止':'未设截止日'}</span><span>${byKind('tasks').filter(t=>t.project_id===p.id&&t.status!=='已完成').length} 项待办</span></div></button>`;
}
function homePage() {
 const active=byKind('projects').filter(p=>p.status==='进行中'), pending=byKind('tasks').filter(t=>t.status!=='已完成');
 return `<section class="welcome"><div><h2>${currentWorkspace?.maintenance?esc(person(currentWorkspace.owner_id))+' 的个人空间':esc(me.name)+'，欢迎回来'}</h2><p>${new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date())}<br>${active.length} 个项目正在推进</p></div><div class="welcome-count"><strong>${pending.filter(t=>t.assignee_id===(currentWorkspace?.maintenance?currentWorkspace.owner_id:me.id)).length}</strong><small>${currentWorkspace?.maintenance?'该成员的':'我的'}未完成待办</small></div></section><div class="two-col"><section><div class="section-title"><h2>正在推进</h2><button class="text-button" data-view="projects">全部项目 ${icon('arrow')}</button></div>${active.length?`<div class="project-grid">${active.slice(0,4).map(projectCard).join('')}</div>`:`<div class="panel">${empty(canCreate('projects')?'从一个项目开始':'暂无已授权项目',canCreate('projects')?'新建项目后，可以一起记录沟通、上传资料、分配待办。':'请联系公司 Admin 将你加入需要参与的项目。','new-project','新建项目')}</div>`}<div class="section-title section-space"><h2>接下来要做</h2><button class="text-button" data-view="tasks">全部待办 ${icon('arrow')}</button></div><div class="panel">${pending.length?pending.slice().sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999')).slice(0,5).map(taskRow).join(''):empty('暂无待办','把需要跟进的事情记录下来，分配给负责的成员。','new-task','添加待办','tasks')}</div></section><section><div class="section-title"><h2>${currentWorkspace?.kind==='personal'?'个人动态':'团队动态'}</h2><button class="text-button" data-view="activity">全部记录 ${icon('arrow')}</button></div><div class="panel">${activityList(events.slice(0,8))}</div>${config.finance_access&&config.sheet_url?`<div class="section-title"><h2>常用资料</h2></div><div class="panel"><a class="row" href="${esc(config.sheet_url)}" target="_blank" rel="noopener">${icon('finance')}<div class="row-main"><h3>原始收支记账本</h3><p>查看 Google Sheets 原表</p></div>${icon('arrow')}</a></div>`:''}</section></div>`;
}
function searchBar(options, placeholder) {return `<div class="toolbar"><input id="search" type="search" placeholder="${placeholder}" aria-label="${placeholder}" value="${esc(search)}"><select id="filter" aria-label="筛选状态"><option value="">全部状态</option>${options.map(x=>`<option ${filter===x?'selected':''}>${x}</option>`).join('')}</select></div>`;}
function projectsPage() {
 if(projectId) return projectPage();
 const items=byKind('projects').filter(p=>(!filter||p.status===filter)&&JSON.stringify(p).toLowerCase().includes(search.toLowerCase()));
 return searchBar(['进行中','待启动','暂停','已完成'],'搜索项目')+(items.length?`<div class="project-grid">${items.map(projectCard).join('')}</div>`:`<div class="panel">${empty(byKind('projects').length?'没有匹配的项目':canCreate('projects')?'还没有项目':'暂无已授权项目',canCreate('projects')?'按公司实际开展的工作创建项目，逐步记录进展。':'请联系公司 Admin 将你加入需要参与的项目。','new-project','新建项目')}</div>`);
}
function projectTimeline(project) {
 const notes = CmpTimeline.notesForProject(records, project.id, subsectionId);
 const sections = byKind('subsections').filter(s => s.project_id === project.id)
   .sort((a,b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
 const legend = [...sections.map(s => [s.id,s.title]), ['', '未分区']];
 return `<section class="project-timeline" aria-label="项目时间线">
   <div class="section-title"><h3>项目时间线</h3><span class="meta">${notes.length} 条记录 · 最近在前</span></div>
   <div class="timeline-legend">${legend.filter(([id]) => notes.some(n => (n.subsection_id||'')===id)).map(([id,title]) => `<span data-timeline-section="${esc(id)}"><i aria-hidden="true"></i>${esc(title)}</span>`).join('')}</div>
   ${notes.length ? `<ol class="project-timeline-list" tabindex="0" aria-label="按沟通日期排列的笔记">${notes.map(n => `<li data-timeline-section="${esc(n.subsection_id||'')}">
     <time datetime="${esc(n.date)}">${esc(n.date)}</time>
     <button class="timeline-note" data-edit="${n.id}" aria-label="查看笔记：${esc(n.title)}">
       <span class="timeline-section">${esc(sectionName(n.subsection_id))}</span>
       <strong>${esc(n.title)}</strong>
       <span class="timeline-excerpt">${esc(n.body.slice(0,140))}${n.body.length>140?'…':''}</span>
       <span class="meta">${n.contact?esc(n.contact)+' · ':''}${esc(person(n.created_by))}</span>
     </button></li>`).join('')}</ol>` : '<p class="timeline-empty">暂无时间线记录。添加笔记时勾选“加入项目时间线”，就会显示在这里。</p>'}
 </section>`;
}
function applyTimelineColors() {
 const colorMaps = new Map();
 document.querySelectorAll('[data-timeline-section]').forEach(el => {
   const id = el.dataset.timelineProject || projectId;
   if (!colorMaps.has(id)) colorMaps.set(id, CmpTimeline.sectionColors(records, id));
   const colors = colorMaps.get(id);
   el.style.setProperty('--section-color', colors[el.dataset.timelineSection] || colors['']);
 });
}
function projectPage() {
 const p=find(projectId);if(!p){projectId=null;return projectsPage();}
 const items=byKind(projectTab).filter(r=>r.project_id===p.id && inSection(r));
 const sections=byKind('subsections').filter(r=>r.project_id===p.id);
 const currentSection=sections.find(r=>r.id===subsectionId);
 const sectionBar=`<section class="subsections"><div class="section-title"><h2>项目分区</h2><button class="text-button" data-new="subsections">${icon('plus')} 新建分区</button></div><nav class="section-options" aria-label="项目分区">${[['','全部记录'],['unassigned','未分区'],...sections.map(r=>[r.id,r.title])].map(([id,title])=>`<button data-section="${id}" class="${subsectionId===id?'active':''}" aria-pressed="${subsectionId===id}">${esc(title)}</button>`).join('')}</nav>${currentSection?`<div class="spread"><p class="hint">${esc(currentSection.description||currentSection.title)}</p><button class="text-button" data-edit="${currentSection.id}">编辑分区</button></div>`:'<p class="hint">按店家、工厂或工作类别，把项目记录分开管理。</p>'}</section>`;
 return `<button class="back" data-action="back-projects">${icon('back')} 全部项目</button>
 <div class="panel detail-head"><div class="spread">${badge(p.status)}<div class="project-actions"><button class="text-button" data-edit="${p.id}">编辑项目</button>${config.full_edit?`<button class="text-button danger-text" data-delete-project="${p.id}">删除项目</button>`:''}</div></div><h2>${esc(p.title)}</h2>${resourceMembersButton('projects',p.id)}<p>${esc(p.description||'还没有项目说明')}</p>${projectTimeline(p)}</div>
 ${sectionBar}<nav class="detail-tabs" aria-label="项目内容">${[['notes','沟通笔记'],['tasks','待办'],['files','附件'],['stock_movements','库存'],['transactions','收支']].filter(([k])=>k!=='transactions'||config.finance_access).map(([k,v])=>`<button data-tab="${k}" class="${projectTab===k?'active':''}">${v} ${byKind(k).filter(x=>x.project_id===p.id && inSection(x)).length}</button>`).join('')}</nav><div class="section-title"><h2>${{notes:'沟通与进展',tasks:'项目待办',files:'项目资料',stock_movements:'项目出入库',transactions:'项目收支'}[projectTab]}</h2><button class="primary" data-new="${projectTab}">${icon('plus')} 添加</button></div>${projectTab==='stock_movements'?`<p class="hint">这里展示当前项目与分区的出入库流水；${config.full_access?'仓库的实际可用余额请在“库存”中查看。':'公司总库存请联系公司 Admin 查看。'}</p>`:''}${projectTab==='files'&&!config.upload_ready?'<div class="notice">此空间暂不支持直接上传。可以添加已有文件链接；请核对文件本身的共享权限，平台权限不会改变 Drive 的共享设置。</div>':''}${items.length?projectTab==='notes'?items.map(noteCard).join(''):`<div class="panel">${projectTab==='stock_movements'?stockMovementList(items):items.map(projectTab==='tasks'?taskRow:projectTab==='transactions'?transactionRow:fileRow).join('')}</div>`:`<div class="panel">${empty('暂无'+kindLabels[projectTab],'点击添加，把这个项目的实际信息记录下来。',null,null,projectTab==='files'?'file':'note')}</div>`}`;
}
function noteCard(n) {return `<article class="note"><div class="spread"><h3>${esc(n.title)}</h3><button class="text-button" data-edit="${n.id}">编辑</button></div><p class="meta">${esc(sectionName(n.subsection_id))} · ${esc(n.date)}${n.contact?' · 与 '+esc(n.contact)+' 沟通':''}</p><div class="note-body">${esc(n.body)}</div>${n.linked_task_id&&find(n.linked_task_id)?`<div class="linked-task"><button class="text-button" data-edit="${n.linked_task_id}">${icon('tasks')} ${esc(find(n.linked_task_id).title)}</button>${badge(find(n.linked_task_id).status)}</div>`:''}<p class="meta">${esc(person(n.updated_by))} · ${formatTime(n.updated_at)} <button class="text-button" data-history="${n.id}">修改记录</button></p></article>`;}
const fileSize=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.ceil(n/1024))+' KB';
function fileRow(f) {if(f.storage_provider!=='gcs')return legacyFileRow(f);return `<div class="row">${icon('file')}<div class="row-main"><h3><button class="file-title" data-preview-file="${f.id}">${esc(f.title)}</button></h3><p class="file-meta">${esc(sectionName(f.subsection_id))} · ${esc(person(f.created_by))} · ${formatTime(f.created_at)} · ${fileSize(f.size||0)}</p></div><div class="file-actions"><button class="text-button" data-preview-file="${f.id}">预览</button>${canEdit(f.id)?`<button class="text-button" data-edit="${f.id}">编辑</button>`:''}</div></div>`;}
function legacyFileRow(f) {return `<div class="row">${icon('file')}<div class="row-main"><h3><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.title)}</a></h3><p>${esc(sectionName(f.subsection_id))} · ${esc(person(f.created_by))} · ${formatTime(f.created_at)}</p></div><button class="text-button" data-edit="${f.id}">编辑</button></div>`;}
function transactionRow(t) {return `<button class="row clickable" data-edit="${t.id}"><div class="row-main"><h3>${esc(t.title)}</h3><p>${esc(t.date||'日期待确认')} · ${esc(t.event||projectName(t.project_id))}</p><p>${esc(t.responsible||person(t.created_by))} · ${esc(t.payment_status)} · ${esc(financeProfileName(t.profile_id))}</p>${reimbursementBadge(t)}</div><div class="amount ${t.direction==='收入'?'income':t.direction==='支出'?'expense':''}">${t.direction==='收入'?'+':t.direction==='支出'?'−':'其他 ' }${cash(t.amount,t.currency)}<small>${t.currency==='CNY'?(t.usd_amount?'折合 '+cash(t.usd_amount):'待确认美元金额'):esc(t.posting_status)}</small></div></button>`;}
function sourceLabel(source) {return source ? `Google Sheets「${source.sheet_title}」第 ${source.row} 行` : '未记录';}
function sourceLink(source) {
 if(!/^[a-zA-Z0-9_-]+$/.test(source.spreadsheet_id)||!Number.isInteger(source.sheet_id)||!Number.isInteger(source.row))return '';
 return `<p class="hint">导入来源：<a href="https://docs.google.com/spreadsheets/d/${source.spreadsheet_id}/edit#gid=${source.sheet_id}&amp;range=A${source.row}:Q${source.row}" target="_blank" rel="noopener">${esc(sourceLabel(source))}</a></p>`;
}
function financeProfitSummary() {
 const summary=CmpFinance.profitSummary(records,financeProfileId);
 const label={profit:'当前盈利',loss:'当前亏损',balanced:'收支平衡',empty:'暂无收支',incomplete:'待补全折算'}[summary.state];
 const incomplete=summary.state==='incomplete', empty=summary.state==='empty';
 const unit=(summary.converted?'折合 ':'')+summary.currency;
 return `<section class="profit-summary ${summary.state}" aria-label="账本盈亏">
   <div class="profit-result"><h2>${label}</h2><strong>${incomplete||empty?'—':cash(summary.net/100,summary.currency)}</strong><span>${esc(unit)} · 账本累计收支</span></div>
   <dl class="profit-breakdown"><div><dt>${incomplete?'已折算收入':'总收入'}</dt><dd>${cash(summary.income/100,summary.currency)}</dd></div><div><dt>${incomplete?'已折算支出':'总支出'}</dt><dd>${cash(summary.expense/100,summary.currency)}</dd></div></dl>
   <p class="profit-basis">${empty?'记录收入和支出后自动计算。':incomplete?`${summary.missingConversions} 笔流水缺少美元折算金额，补全后显示整体盈亏。`:`收入减支出，包含未付流水。${summary.converted?'人民币沿用每笔记录的美元折算。':''}`}其他往来与报销不重复计入。${empty?'':'下方筛选不影响此处总额。'}</p>
 </section>`;
}
function financePage() {
 if(!config.finance_access)return empty('暂无财务权限','请联系管理员。');
 const {rows,totals,balances}=CmpFinance.ledger(records,financeProfileId,search,filter,reimbursementFilter);
 const profile=find(financeProfileId);
 if(!config.finance_access)return empty('暂无可访问的账本','请联系公司 Admin 分配账本权限。');
 return `<div class="finance-profile-bar">${financeProfileSelect(financeProfileId,'finance_profile','财务 Profile / 账本')}<button class="secondary" data-new="finance_profiles">${icon('plus')} 新建账本</button>${profile?`<button class="text-button" data-edit="${profile.id}">编辑账本</button>`:''}${resourceMembersButton('finance_profiles',financeProfileId)}</div>${profile?.description?`<p class="finance-profile-description">${esc(profile.description)}</p>`:''}
 ${financeProfitSummary()}
 ${rows.length?`<div class="ledger-summary">${Object.entries(totals).map(([currency,t])=>`<div class="ledger-currency"><span class="currency-code">${currency}</span><div><small>收入</small><strong class="income">${cash(t.income/100,currency)}</strong></div><div><small>支出</small><strong>${cash(t.expense/100,currency)}</strong></div><div><small>收支净额</small><strong>${cash((t.income-t.expense)/100,currency)}</strong></div><div><small>待报销余额</small><strong>${cash(t.pending/100,currency)}</strong>${t.unknown?`<small>${t.unknown} 笔报销待确认</small>`:''}</div></div>`).join('')}</div>`:''}
 <p class="hint">各币种分别汇总当前筛选，含未付流水；收支净额不代表银行余额。报销只更新原支出，不重复计入收支。待确认记录不计入待报销余额。其他往来不计入收支净额。</p>${Object.entries(totals).filter(([,t])=>t.other).map(([currency,t])=>`<p class="hint">${esc(currency)} 其他往来：${cash(t.other/100,currency)}</p>`).join('')}
 <div class="finance-filters"><input id="search" type="search" placeholder="搜索事项、负责人或备注" aria-label="搜索事项、负责人或备注" value="${esc(search)}"><select id="filter" aria-label="筛选收支与支付"><option value="">全部收支与支付</option>${['收入','支出','其他','已付','未付','部分支付','待确认'].map(x=>`<option ${filter===x?'selected':''}>${x}</option>`).join('')}</select><select id="reimbursement-filter" aria-label="筛选报销状态"><option value="">全部报销状态</option>${CmpFinance.statuses.map(x=>`<option ${reimbursementFilter===x?'selected':''}>${x}</option>`).join('')}</select></div>
 <div class="section-title"><h2>收支明细 <span class="meta">${rows.length} 笔</span></h2>${config.sheet_url?`<a class="text-button" href="${esc(config.sheet_url)}" target="_blank" rel="noopener">原始表格</a>`:''}</div>
 ${rows.length?`<p class="hint">按日期排列，日期待确认的流水列在最后；可横向滚动查看全部列；${ledgerAccess(financeProfileId)==='editor'?'点击事项编辑，点击报销状态更新报销。':'点击事项查看详情。'}</p><div class="ledger-scroll" tabindex="0" role="region" aria-label="收支与报销明细表"><table class="ledger-table"><thead><tr>${['事项 / 项目','日期','事件 / 活动','负责人 / 垫付人','币种','收入','支出','其他往来','筛选内累计','支付状态','付款形式','报销状态','应报金额','已报金额','待报金额','报销日期','入账状态','入账金额','折合美元','备注','报销备注','最近更新','历史'].map(x=>`<th scope="col">${x}</th>`).join('')}</tr></thead><tbody>${rows.map(t=>financeTableRow(t,balances[t.id])).join('')}</tbody></table></div>`:`<div class="panel">${empty(search||filter||reimbursementFilter?'没有匹配的流水':'这个账本还没有记录','可以记录收支，并单独跟进每笔支出的报销情况。','new-transaction','记录收支','finance')}</div>`}${ledgerTasksSection()}`;
}
function financeProfileSelect(value,name='profile_id',label='所属财务账本') {
 return select(name,label,[...(config.full_access||config.ledger_access?.['']?[['','默认账本']]:[]),...byKind('finance_profiles').slice().sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id)).map(p=>[p.id,p.title])],value);
}
function reimbursementBadge(t) {
 const status=CmpFinance.status(t);
 return `<span class="reimbursement-badge ${['待报销','部分报销'].includes(status)?'pending':status==='已报销'?'done':''}">${status}</span>`;
}
function financeTableRow(t,balance) {
 const status=CmpFinance.status(t), applies=['待报销','部分报销','已报销'].includes(status);
 const amount=v=>v===null||v===undefined||v===''?'—':cash(v,t.currency);
 return `<tr><td><button class="text-button" data-edit="${t.id}">${esc(t.title)}</button>${t.project_id?`<small>${esc(projectName(t.project_id))}</small>`:''}</td><td>${esc(t.date||'日期待确认')}</td><td>${esc(t.event)||'—'}</td><td>${esc(t.responsible)||'—'}</td><td>${esc(t.currency)}</td><td class="numeric income">${t.direction==='收入'?amount(t.amount):'—'}</td><td class="numeric">${t.direction==='支出'?amount(t.amount):'—'}</td><td class="numeric">${t.direction==='其他'?amount(t.amount):'—'}</td><td class="numeric">${cash(balance/100,t.currency)}</td><td>${esc(t.payment_status)}</td><td>${esc(t.payment_method)||'—'}</td><td>${t.direction==='支出'?`<button data-reimburse="${t.id}" aria-label="更新报销：${esc(t.title)}">${reimbursementBadge(t)}</button>`:reimbursementBadge(t)}</td><td class="numeric">${applies?amount(t.claim_amount):'—'}</td><td class="numeric">${applies?amount(t.reimbursed_amount||0):'—'}</td><td class="numeric">${applies?cash((CmpFinance.cents(t.claim_amount)-CmpFinance.cents(t.reimbursed_amount))/100,t.currency):'—'}</td><td>${esc(t.reimbursement_date)||'—'}</td><td>${esc(t.posting_status)}</td><td class="numeric">${amount(t.booked_amount)}</td><td class="numeric">${t.usd_amount==null?'—':cash(t.usd_amount)}</td><td class="ledger-note">${esc(t.note)||'—'}</td><td class="ledger-note">${esc(t.reimbursement_note)||'—'}</td><td>${esc(person(t.updated_by))}<br>${formatTime(t.updated_at)}</td><td><button class="text-button" data-history="${t.id}">修改记录</button></td></tr>`;
}
function reimbursementFields(data) {
 return `<fieldset class="reimbursement-fields wide"><legend>报销跟进</legend><div class="reimbursement-grid">${select('reimbursement_status','报销状态',CmpFinance.statuses,CmpFinance.status(data))}${field('claim_amount','应报金额（原币）',data.claim_amount,'number','min="0" step="0.01"')}${field('reimbursed_amount','累计已报金额（原币）',data.reimbursed_amount,'number','min="0" step="0.01"')}${field('reimbursement_date','最近报销日期',data.reimbursement_date,'date')}${area('reimbursement_note','报销备注',data.reimbursement_note)}</div><p class="hint">金额与支出使用同一币种。若只垫付了部分费用，应报金额填实际需要报销的部分；已报金额填累计金额。</p></fieldset>`;
}
function syncReimbursementFields() {
 const status=$('#editor [name=reimbursement_status]');if(!status)return;
 const direction=$('#editor [name=direction]');
 const income=!!direction && direction.value!=='支出';
 if(income)status.value='不需报销';
 status.disabled=income;
 const applies=['待报销','部分报销','已报销'].includes(status.value);
 for(const name of ['claim_amount','reimbursed_amount','reimbursement_date']) {
   const input=$('#editor [name='+name+']');input.disabled=!applies;
   input.required=applies && name!=='reimbursement_date';
 }
}
function reimbursementBody(form) {
 const body=Object.fromEntries(form);
 body.reimbursement_status=['收入','其他'].includes(body.direction)?'不需报销':body.reimbursement_status;
 if(['待确认','不需报销'].includes(body.reimbursement_status)) Object.assign(body,{claim_amount:null,reimbursed_amount:null,reimbursement_date:''});
 return body;
}
function reimbursementEditor(id) {
 if(!canEdit(id)){readOnlyRecord(id);return;}
 const t=find(id);
 openModal('更新报销',`<p>${esc(t.title)} · ${cash(t.amount,t.currency)}</p><p class="hint">${esc(financeProfileName(t.profile_id))} · ${esc(t.responsible||'未填写垫付人')}</p>`+formWrap(reimbursementFields(t),'保存报销'));
 syncReimbursementFields();
 bindSubmit(async form=>{await api('/records/transactions/'+id,'PATCH',{...reimbursementBody(form),version:t.version});closeModal();await load();toast('报销记录已更新，更新人：'+me.name);});
}
function taskRow(t) {const done=t.status==='已完成', overdue=t.due_date&&t.due_date<today()&&!done;return `<div class="row ${done?'completed':''}"><button class="task-check ${done?'checked':''}" data-toggle-task="${t.id}" aria-label="${done?'重新打开':'完成'}：${esc(t.title)}">${done?icon('check'):''}</button><button class="row-main" data-edit="${t.id}"><h3>${esc(t.title)}</h3><p>${esc(person(t.assignee_id))} · ${esc(t.ledger_task||t.profile_id?financeProfileName(t.profile_id):projectName(t.project_id))}${t.subsection_id?' / '+esc(sectionName(t.subsection_id)):''}</p><p class="${overdue?'overdue':''}">${t.due_date?esc(t.due_date)+(overdue?' 已逾期':' 截止'):'未设截止日'}</p></button>${badge(t.status)}</div>`;}
function tasksPage() {const items=byKind('tasks').filter(t=>(taskFilter==='全部'||taskFilter==='我的待办'&&t.assignee_id===me.id&&t.status!=='已完成'||taskFilter==='未完成'&&t.status!=='已完成'||taskFilter==='已完成'&&t.status==='已完成')&&t.title.toLowerCase().includes(search.toLowerCase())).sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999'));return `<nav class="detail-tabs" aria-label="待办筛选">${['未完成','我的待办','已完成','全部'].map(f=>`<button data-task-filter="${f}" class="${taskFilter===f?'active':''}">${f}</button>`).join('')}</nav><div class="toolbar"><input type="search" id="search" value="${esc(search)}" placeholder="搜索待办" aria-label="搜索待办"></div><div class="panel">${items.length?items.map(taskRow).join(''):empty('这里没有待办','新增要跟进的事项，或切换上面的筛选查看其他任务。','new-task','添加待办','tasks')}</div>`;}
function activityList(items) {return items.length?`<div class="timeline">${items.map(a=>`<div class="event"><button data-audit="${a.id}"><h3><strong>${esc(a.actor_name||'成员')}</strong> ${esc(a.action)}${a.kind==='resource_access'?'':'了'+esc(kindLabels[a.kind]||a.kind)}<br>${esc(a.after?.title||a.after?.name||a.before?.title||'')}</h3><p>${formatTime(a.at)} · 查看详情</p></button></div>`).join('')}</div>`:empty('还没有团队更新','成员创建或修改记录后，操作人和时间会显示在这里。',null,null,'activity');}
function activityPage() {return `<div class="panel">${activityList(events)}</div><p class="hint">显示最近 100 次操作。每条记录的编辑窗口可以查看该记录的修改历史。</p>`;}
function usersPage() {if(me.role!=='admin')return '';return `<div class="notice">已启用 ${allAccounts.filter(p=>p.active).length} / 6 个账号。可查看用户名和重置初始密码；成员当前密码不可查看。团队权限请在对应团队的“公司成员与权限”中设置。</div><div class="panel">${allAccounts.map(p=>`<div class="user-row"><span class="avatar">${initials(p.name)}</span><div class="row-main"><h3>${esc(p.name)} ${p.role==='admin'?badge('平台管理员'):''}</h3><p class="meta">${esc(p.username)} · ${p.active?'已启用':'已停用'}${p.must_change?' · 待修改初始密码':''}</p></div>${p.role!=='admin'?`<div class="account-actions"><button class="secondary" data-reset-user="${p.id}">重置初始密码</button><button class="secondary" data-user-active="${p.id}">${p.active?'停用':'启用'}</button>${p.active?`<button class="text-button" data-maintain-user="${p.id}">只读数据维护</button>`:''}</div>`:''}</div>`).join('')}</div>`;}
function field(name,label,value='',type='text',extra='') {return `<label class="field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;}
function area(name,label,value='',required=false) {return `<label class="field wide"><span>${label}</span><textarea name="${name}" ${required?'required':''}>${esc(value)}</textarea></label>`;}
function select(name,label,options,value='',required=false) {return `<label class="field"><span>${esc(label)}</span><select name="${name}" ${required?'required':''}>${options.map(o=>{const [v,t]=Array.isArray(o)?o:[o,o];return `<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(t)}</option>`;}).join('')}</select></label>`;}
const projectSelect = (value,required=false) => select('project_id','关联项目',[...(value&&!find(value)?[[value,projectName(value)+'（保留已有关联）']]:[]),['',required?'请选择项目':'不关联项目'],...byKind('projects').map(p=>[p.id,p.title])],value,required);
const peopleSelect = (name,label,value) => select(name,label,[['','未指定'],...people.filter(p=>p.active).map(p=>[p.id,p.name])],value);
function openModal(title,body) {clearFilePreview();const modal=$('#modal');modal.innerHTML=`<div class="dialog-head"><h2>${esc(title)}</h2><button data-action="close-modal" aria-label="关闭">${icon('close')}</button></div>${body}`;if(!modal.open)modal.showModal();}
function closeModal(force=false) {if(!force&&$('#modal').dataset.busy==='true')return;clearFilePreview();$('#modal').close();}
function formWrap(content,button='保存记录') {return `<form id="editor"><div class="form-grid">${content}</div><p class="error" role="alert"></p><div class="dialog-footer"><button type="button" class="secondary" data-action="close-modal">取消</button><button class="primary" type="submit">${button}</button></div></form>`;}
function bindSubmit(handler) {$('#editor').addEventListener('submit', async e=>{e.preventDefault();const form=e.currentTarget,button=$('button[type=submit]',form);button.disabled=true;$('.error',form).textContent='';try{await handler(new FormData(form));}catch(error){$('.error',form).textContent=error.message;}finally{button.disabled=false;}});}

function deleteProjectForm(id) {
 const project=find(id),targetWorkspace=workspaceId;
 if(!project||!config.full_edit){toast('只有公司管理员或个人空间主人可以删除项目');return;}
 openModal('删除项目',formWrap(`<div class="wide delete-project-summary"><p>确认删除 <strong>${esc(project.title)}</strong>？</p><p>项目及其分区、笔记、待办和附件将退出日常列表，不能继续添加或修改。</p><p>财务流水、报销记录、库存数量和操作历史会保留。附件原文件也会保留。</p><p class="hint">删除后不能在界面自行恢复。请输入完整项目名称确认。</p></div><div class="wide">${field('confirm_title','项目名称','','text','required maxlength="120" autocomplete="off"')}</div>`,'确认删除项目'));
 const input=$('[name=confirm_title]',$('#editor')),button=$('[type=submit]',$('#editor'));
 button.classList.add('danger');button.disabled=true;
 input.addEventListener('input',()=>{button.disabled=input.value!==project.title;});
 bindSubmit(async values=>{
  if(workspaceId!==targetWorkspace)throw new Error('当前空间已变化，请重新打开项目');
  if(values.get('confirm_title')!==project.title)throw new Error('请输入完整项目名称确认删除');
  await api('/records/projects/'+id,'DELETE',{version:project.version,confirm_title:values.get('confirm_title')});
  closeModal();projectId=null;subsectionId='';view='projects';search='';filter='';
  await load();draw();toast('项目已删除，财务与库存记录已保留');
 });
}

function sectionSelect(project, value='') {
 return `<div id="section-field">${select('subsection_id','所属分区',[...(value&&!find(value)?[[value,'未授权分区（保留已有关联）']]:[]),['','未分区'],...byKind('subsections').filter(r=>r.project_id===project).map(r=>[r.id,r.title])],value)}</div>`;
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

function projectAttachmentFields() {
 const message=config.upload_ready?'保存项目后自动上传。PDF、图片和文本可在平台内预览，每个最多 20 MB，一次最多 10 个。':'附件存储暂不可用。项目可以先保存，稍后再到项目的“附件”中添加。';
 return `<fieldset class="project-attachments wide"><legend>项目附件 <span>可选</span></legend><p class="hint">${message}</p><label class="attachment-picker"><span>${icon('file')} 选择附件</span><input type="file" name="project_files" multiple ${config.upload_ready?'':'disabled'} aria-label="选择项目附件" aria-describedby="attachment-help"></label><p class="hint" id="attachment-help">${config.upload_ready?'文件只会在保存项目时上传。':'附件存储恢复后即可选择文件。'}</p><ul class="attachment-list" aria-label="已选附件"></ul><p class="attachment-status" role="status" aria-live="polite"></p></fieldset>`;
}

function bindProjectCreation() {
 const targetWorkspace=workspaceId;
 const draft=new CmpProjectAttachments.ProjectAttachmentDraft((path,method,body)=>{
  if(workspaceId!==targetWorkspace)throw new Error('当前空间已变化，请返回原公司后重试');
  return api(path,method,body);
 });
 const form=$('#editor'),modal=$('#modal'),picker=$('[name=project_files]',form),list=$('.attachment-list',form),status=$('.attachment-status',form),submit=$('[type=submit]',form);
 const labels={pending:'等待保存',uploading:'正在上传…',done:'已保存到 Drive',failed:'上传未完成'};
 const update=()=>{
  modal.dataset.busy=String(draft.busy);
  for(const control of form.querySelectorAll('input:not([type=file]),select,textarea'))control.disabled=Boolean(draft.body);
  for(const button of modal.querySelectorAll('[data-action=close-modal]'))button.disabled=draft.busy;
  picker.disabled=draft.busy||!config.upload_ready;
  list.innerHTML=draft.files.map(x=>`<li><div><strong>${esc(x.file.name)}</strong><small>${x.file.size<1024*1024?Math.ceil(x.file.size/1024)+' KB':(x.file.size/1024/1024).toFixed(1)+' MB'} · <span class="attachment-${x.status}">${labels[x.status]}</span></small>${x.error?`<p class="attachment-error">${esc(x.error)}</p>`:''}</div>${x.status!=='done'?`<button type="button" class="text-button" data-remove-attachment="${x.key}" aria-label="移除 ${esc(x.file.name)}" ${draft.busy?'disabled':''}>移除</button>`:icon('check')}</li>`).join('');
  const completed=draft.files.filter(x=>x.status==='done').length;
  status.textContent=draft.project?(draft.busy?`项目已保存，附件 ${completed} / ${draft.files.length} 已完成。`:'项目已保存。关闭后，未完成的附件需要重新选择。'):draft.busy?'正在保存项目…':draft.body?'保存结果暂未确认，请重试以核对。':'';
  submit.textContent=draft.busy?(draft.project?'正在上传附件…':'正在保存项目…'):draft.project?(draft.files.some(x=>x.status!=='done')?'重试未完成附件':'打开项目'):draft.body?'重试保存项目':draft.files.length?'保存项目并上传附件':'保存项目';
 };
 picker.addEventListener('change',()=>{try{draft.add(picker.files);$('.error',form).textContent='';}catch(error){$('.error',form).textContent=error.message;}picker.value='';update();});
 list.addEventListener('click',event=>{const button=event.target.closest('[data-remove-attachment]');if(button){draft.remove(button.dataset.removeAttachment);update();}});
 const cancel=event=>{if(draft.busy)event.preventDefault();};
 modal.addEventListener('cancel',cancel);
 const leaving=event=>{if(draft.busy){event.preventDefault();event.returnValue='';}};
 window.addEventListener('beforeunload',leaving);
 modal.addEventListener('close',()=>{window.removeEventListener('beforeunload',leaving);modal.removeEventListener('cancel',cancel);delete modal.dataset.busy;},{once:true});
 bindSubmit(async values=>{
  const body=Object.fromEntries(values);delete body.project_files;
  if(config.can_manage_resources)body.member_access=values.getAll('resource_member').map(user_id=>({user_id,role:values.get('resource_role')}));
  const saved=await draft.save(body,update);
  view='projects';projectId=saved.id;projectTab=draft.files.length?'files':'notes';subsectionId='';search='';filter='';
  closeModal();await load();draw();toast(draft.files.length?'项目已保存，附件已上传附件':'项目已保存');
 });
 update();
}

function editor(kind,id,externalLink=false) {
 if(kind!=='users'&&!(id?canEdit(id):canCreate(kind))){readOnlyRecord(id);return;}
 if(['warehouses','products','stock_movements'].includes(kind)){inventoryEditor(kind,id);return;}
 if(kind==='users'){userEditor();return;}
 if(kind==='files'&&!id&&config.upload_ready&&!externalLink){uploadForm();return;}
 const record=id?find(id):null, data=record||{date:today(),project_id:projectId||'',subsection_id:subsectionId==='unassigned'?'':subsectionId,profile_id:kind==='transactions'||kind==='tasks'&&view==='finance'?financeProfileId:'',ledger_task:kind==='tasks'&&view==='finance',currency:'USD',direction:'支出'};
 let content='';
 if(kind==='finance_profiles')content=field('title','账本名称',data.title,'text','required maxlength="120" placeholder="例如：公司、商会、业务名称"')+area('description','账本说明',data.description);
 if(kind==='subsections')content=field('title','分区名称',data.title,'text','required maxlength="120" placeholder="例如：店家联系、工厂一联系"')+projectSelect(data.project_id,true)+area('description','分区说明',data.description);
 if(kind==='projects') content=field('title','项目名称',data.title,'text','required maxlength="120"')+select('status','状态',['进行中','待启动','暂停','已完成'],data.status||'进行中')+field('due_date','截止日期',data.due_date,'date')+area('description','项目说明',data.description);
 if(kind==='transactions')content=field('title','事项 / 项目名称',data.title,'text','required maxlength="200"')+field('date','日期（不确定可留空）',data.date,'date')+select('direction','收支',['支出','收入','其他'],data.direction)+select('currency','原币币种',['USD','CNY'],data.currency)+field('amount','原币金额',data.amount,'number','min="0" step="0.01" required')+field('usd_amount','折合美元（人民币流水填写）',data.usd_amount,'number','min="0" step="0.01"')+field('event','事件 / 活动',data.event)+projectSelect(data.project_id)+select('payment_status','支付状态',['未付','已付','部分支付','待确认'],data.payment_status||'未付')+field('payment_method','付款形式',data.payment_method,'text','placeholder="现金、支票、转账…"')+select('posting_status','入账状态',['未入账','平帐','部分入账','待入账','待支出'],data.posting_status||'未入账')+field('booked_amount','入账金额（原币，可为负数）',data.booked_amount,'number','step="0.01"')+field('responsible','负责人',data.responsible)+field('category','类别',data.category)+area('note','备注',data.note);
 if(kind==='notes')content=field('title','笔记标题',data.title,'text','required maxlength="200"')+field('date','沟通日期',data.date,'date','required')+field('contact','客户 / 联系人',data.contact)+projectSelect(data.project_id,true)+area('body','沟通内容与项目进展',data.body,true);
 if(kind==='tasks')content=select('task_scope','待办归属',[['project','项目待办'],...(config.finance_access?[['ledger','账本待办']]:[])],data.ledger_task||data.profile_id?'ledger':'project')+`<div id="task-ledger-scope">${financeProfileSelect(data.profile_id)}</div>`+field('title','要做什么',data.title,'text','required maxlength="200"')+select('status','状态',['待办','进行中','已完成'],data.status||'待办')+peopleSelect('assignee_id','负责人',data.assignee_id)+field('due_date','截止日期',data.due_date,'date')+projectSelect(data.project_id)+area('description','补充说明',data.description);
 if(kind==='files')content=field('title','文件名称',data.title,'text','required')+projectSelect(data.project_id,true)+(data.storage_provider==='gcs'?'<p class="hint wide">文件已保存，可修改名称、项目和分区。原文件内容保持不变。</p>':field('url','已有文件链接',data.url,'url','required placeholder="https://drive.google.com/…"'));
 if(['notes','tasks','files','transactions'].includes(kind)) content+=sectionSelect(data.project_id,data.subsection_id);
 if(kind==='transactions')content=financeProfileSelect(data.profile_id)+content+reimbursementFields(data)+(id?stockFinanceLinks(id):'');
 if(kind==='notes') content+=`<label class="timeline-toggle wide"><input type="checkbox" name="show_on_timeline" ${data.show_on_timeline!==false?'checked':''}><span>加入项目时间线<small>取消后仍会保存在沟通笔记中。</small></span></label>`+noteTaskControls(data);
 if(kind==='tasks'&&id) {const linked=byKind('notes').filter(n=>n.linked_task_id===id);if(linked.length)content+=`<section class="wide"><h3>关联笔记</h3>${linked.map(n=>`<button type="button" class="text-button" data-edit="${n.id}">${icon('note')} ${esc(n.title)}</button>`).join('<br>')}</section>`;}
 const uploads=kind==='files'&&!id?`<div class="notice">${config.upload_ready?'上传文件后，可直接在平台内预览 PDF、图片和文本。':'附件存储暂不可用，可以先添加已有文件链接。'}</div>${config.upload_ready?'<button class="secondary" data-action="upload-form">从手机 / 电脑上传</button><br><br>':''}`:'';
 if(!id&&['projects','finance_profiles'].includes(kind))content+=newResourceMembers();
 if(kind==='projects'&&!id)content+=projectAttachmentFields();
 openModal((id?'编辑':'新增')+kindLabels[kind],uploads+formWrap(content)+(id?`<p class="hint">创建：${esc(person(record.created_by))} · ${formatTime(record.created_at)}<br>最近修改：${esc(person(record.updated_by))} · ${formatTime(record.updated_at)}<br><button class="text-button" data-history="${id}">查看修改历史</button></p>`:''));
 if(kind==='projects'&&!id){bindProjectCreation();return;}
 if(kind==='notes')syncTaskControls();
 if(kind==='transactions') {syncReimbursementFields();if(data.source_import) $('#editor').insertAdjacentHTML('beforebegin',sourceLink(data.source_import));}
 if(kind==='tasks')syncTaskScope();
 if(kind==='subsections'&&id)$('#editor [name=project_id]').disabled=true;
 bindSubmit(async form=>{const body=kind==='transactions'?reimbursementBody(form):Object.fromEntries(form);if(kind==='notes')body.show_on_timeline=form.has('show_on_timeline');if(!id&&['projects','finance_profiles'].includes(kind)&&config.can_manage_resources)body.member_access=form.getAll('resource_member').map(user_id=>({user_id,role:form.get('resource_role')}));if(kind==='tasks'){body.ledger_task=form.get('task_scope')==='ledger';if(body.ledger_task){body.project_id='';body.subsection_id='';}else body.profile_id='';}if(kind==='subsections'&&id)body.project_id=record.project_id;if(id)body.version=record.version;const saved=await api('/records/'+kind+(id?'/'+id:''),id?'PATCH':'POST',body);if(kind==='finance_profiles'&&!id){financeProfileId=saved.id;search='';filter='';reimbursementFilter='';}closeModal();await load();toast('已保存，更新人：'+me.name);});
}
function userEditor(id) {
 const p=allAccounts.find(x=>x.id===id), teams=availableSpaces.filter(w=>w.kind==='team'&&['owner','admin'].includes(w.access));
 const teamFields=id?'':select('team_role','加入团队后的权限',[[ 'none','仅创建个人账号'],['member','普通成员 · 逐项分配权限'],['admin','Admin · 公司全部权限']],teams.length?'member':'none')+select('team_id','加入团队',teams.map(w=>[w.id,w.title]),teams[0]?.id);
 openModal(id?'重置 '+p.name+' 的初始密码':'添加账号',formWrap((id?'':field('name','姓名','','text','required maxlength="80"')+field('username','登录账号','','text','required pattern="[a-z0-9_.-]{3,40}" autocomplete="off"'))+field('password','初始密码','','password','required minlength="12" maxlength="128" autocomplete="new-password"')+teamFields,id?'重置初始密码':'创建账号'));
 bindSubmit(async form=>{await api('/users'+(id?'/'+id:''),id?'PATCH':'POST',Object.fromEntries(form));closeModal();await load();toast(id?'初始密码已重置，请交给该成员并由本人修改':'账号已创建，请将初始密码交给成员');});
}
function passwordForm() {openModal('修改密码',formWrap(field('current_password','当前密码','','password','required autocomplete="current-password"')+field('password','新密码（至少 12 位）','','password','required minlength="12" maxlength="128" autocomplete="new-password"')+field('confirm_password','再次输入新密码','','password','required minlength="12" autocomplete="new-password"'),'修改并重新登录'));bindSubmit(async form=>{if(form.get('password')!==form.get('confirm_password'))throw new Error('两次输入的新密码不一致');await api('/password','POST',Object.fromEntries(form));me=null;closeModal();loginPage();toast('密码已更新，请使用新密码登录');});}
function account() {openModal('账号与设置',`<div class="account"><span class="avatar">${initials(me.name)}</span><div>${esc(me.name)}<small>${esc(me.username)} · ${me.role==='admin'?'管理员':'团队成员'}</small></div></div><div class="panel"><button class="row clickable" data-action="password"><div class="row-main"><h3>修改密码</h3></div>${icon('arrow')}</button><button class="row clickable" data-view="activity"><div class="row-main"><h3>操作记录</h3></div>${icon('arrow')}</button>${me.role==='admin'?'<button class="row clickable" data-view="users"><div class="row-main"><h3>平台账号管理</h3></div>'+icon('arrow')+'</button>':''}<button class="row clickable" data-view="security"><div class="row-main"><h3>账号安全与维护记录</h3></div>${icon('arrow')}</button><button class="row clickable" data-action="refresh"><div class="row-main"><h3>刷新全部记录</h3></div>${icon('refresh')}</button><button class="row clickable" data-action="logout"><div class="row-main"><h3>退出登录</h3></div>${icon('logout')}</button></div><p class="hint">手机安装：iPhone Safari → 分享 → 添加到主屏幕；Android Chrome → 菜单 → 安装应用。使用时需要网络连接。</p>`);}
const fieldLabels={source_import:'导入来源',subsection_id:'分区',linked_task_id:'关联待办',title:'标题',name:'姓名',date:'日期',event:'事件',amount:'原币金额',usd_amount:'美元折算金额',currency:'币种',direction:'收支',payment_status:'支付状态',posting_status:'入账状态',booked_amount:'入账金额',payment_method:'付款形式',responsible:'负责人',category:'类别',note:'备注',description:'说明',body:'正文',contact:'联系人',status:'状态',progress:'进度',due_date:'截止日',project_id:'项目',owner_id:'负责人',assignee_id:'负责人',username:'账号',role:'角色',active:'启用',must_change:'需改密码',url:'链接'};
function auditText(data) {if(!data)return '无';return Object.entries(data).filter(([k])=>!['id','workspace_id','version','updated_at','upload_sha256','drive_file_id','storage_provider','storage_bucket','storage_object','storage_generation','mime_type','size','filename'].includes(k)).map(([k,v])=>`${fieldLabels[k]||k}：${k==='resource_kind'?(kindLabels[v]||v):k==='resource_id'?(data.resource_kind==='finance_profiles'?financeProfileName(v):projectName(v)):k==='user_id'?person(v):k==='role'?accessName(v):k==='ledger_task'?(v?'是':'否'):k==='source_import'?sourceLabel(v):k==='warehouse_id'?warehouseName(v):k==='product_id'?productName(v):k==='linked_transaction_id'?(find(v)?.title||'未关联'):k==='profile_id'?financeProfileName(v):k==='project_id'?projectName(v):k==='subsection_id'?sectionName(v):k==='linked_task_id'?(find(v)?.title||'未关联'):['owner_id','assignee_id','deleted_by'].includes(k)?person(v):v??'未填写'}`).join('\n');}
fieldLabels.show_on_timeline='加入时间线';
Object.assign(fieldLabels,{resource_kind:'权限类型',resource_id:'权限对象',user_id:'成员',ledger_task:'账本待办'});
Object.assign(fieldLabels,{deleted_at:'删除时间',deleted_by:'删除人',_project_deleted:'项目已删除',_project_title:'原项目名称'});
Object.assign(fieldLabels,{profile_id:'财务账本',reimbursement_status:'报销状态',claim_amount:'应报金额',reimbursed_amount:'累计已报金额',reimbursement_date:'最近报销日期',reimbursement_note:'报销备注'});
Object.assign(fieldLabels,{warehouse_id:'仓库',product_id:'商品',linked_transaction_id:'关联财务',movement_type:'出入库类型',quantity:'数量',sku:'SKU',unit:'计量单位',low_stock:'低库存提醒线'});
function showAudit(a) {openModal('修改详情',`<p class="meta">${esc(a.actor_name)} · ${formatTime(a.at)} · ${esc(a.action)}</p><div class="audit-columns"><section><h3>修改前</h3><div class="audit-data">${esc(auditText(a.before))}</div></section><section><h3>修改后</h3><div class="audit-data">${esc(auditText(a.after))}</div></section></div>`);}
async function history(id) {const data=await api('/activity?entity_id='+encodeURIComponent(id));openModal('这条记录的修改历史',activityList(data));$('#modal').querySelectorAll('[data-audit]').forEach(button=>button.addEventListener('click',e=>{e.stopPropagation();showAudit(data.find(a=>a.id===button.dataset.audit));}));}
let activeFilePreview=null;
function clearFilePreview(){if(activeFilePreview){activeFilePreview.controller.abort();activeFilePreview.viewer?.destroy();activeFilePreview=null;}$('#modal').classList.remove('file-preview-dialog');}
async function fetchFile(id,signal,download=false){
 const requested=workspaceId, headers={'X-Cmp-Workspace':requested};if(maintenanceId)headers['X-Cmp-Maintenance']=maintenanceId;
 const response=await fetch('/api/files/'+encodeURIComponent(id)+'/content'+(download?'?download=true':''),{credentials:'same-origin',headers,signal,cache:'no-store'});
 if(!response.ok){const result=await response.json().catch(()=>({}));if(response.status===401){me=null;closeModal(true);loginPage();}throw new Error(result.detail||'文件暂时无法打开，请重试');}
 const blob=await response.blob();if(workspaceId!==requested||!me)throw new Error('当前空间已变化，请重新打开附件');return blob;
}
async function previewFile(id){
 const f=find(id);if(!f||f.storage_provider!=='gcs')return;
 openModal(f.title,`<div class="preview-toolbar"><p>${esc(sectionName(f.subsection_id))} · ${fileSize(f.size||0)}</p><button class="secondary" data-download-file="${f.id}">下载原文件</button></div><div class="file-preview-body"><p class="preview-status" role="status">正在读取附件…</p></div>`);
 $('#modal').classList.add('file-preview-dialog');
 const state={controller:new AbortController(),viewer:null};activeFilePreview=state;
 try{const blob=await fetchFile(id,state.controller.signal);if(activeFilePreview!==state)return;state.viewer=CmpFilePreview.mount($('.file-preview-body'),blob,f.filename||f.title);await state.viewer.ready;}
 catch(error){if(activeFilePreview===state&&error.name!=='AbortError')$('.file-preview-body').textContent=error.message;}
}
async function downloadFile(id,button){
 const f=find(id);if(!f)return;button.disabled=true;
 try{const blob=await fetchFile(id,undefined,true),url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=f.filename||f.title;($('#modal').open?$('#modal'):document.body).append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
 finally{button.disabled=false;}
}
function uploadForm() {const uploadKey=crypto.randomUUID();if(!canCreate('files')){toast('没有此项目的编辑权限');return;}openModal('上传项目附件',formWrap(projectSelect(projectId||'',true)+sectionSelect(projectId||'',subsectionId==='unassigned'?'':subsectionId)+field('file','文件（最大 20 MB）','','file','required'),'上传附件')+'<p><button class="text-button" data-action="file-link-form">添加已有文件链接</button></p>');bindSubmit(async form=>{if(form.get('file').size>20*1024*1024)throw new Error('文件不能超过 20 MB');form.set('upload_key',uploadKey);await api('/upload','POST',form);closeModal();await load();toast('附件已上传，可以直接预览');});}
function loginPage() {loadEpoch++;workspaceId='';maintenanceId='';currentWorkspace=null;records=[];people=[];events=[];allAccounts=[];accountEvents=[];maintenanceItems=[];lastSnapshot=null;clearTimeout(maintenanceTimer);clearInterval(refreshTimer);$('#root').innerHTML=`<main class="login-shell"><section class="login-intro"><img src="/static/icon.svg" alt="Company Manager"><h1>公司的工作，<br>一起记录。</h1><p>从一笔收支到一个项目。<br>让进度、沟通和每次更新都有迹可循。</p></section><section class="login-form-wrap"><form id="login" class="login-form"><h2>登录公司工作台</h2><p class="subtle">使用管理员分配给你的账号。</p>${field('username','账号','','text','required autocomplete="username" autocapitalize="none"')}${field('password','密码','','password','required autocomplete="current-password"')}<p class="error" role="alert"></p><button class="primary" type="submit">登录</button><p class="hint">忘记密码？请联系管理员 Kevin 重置。</p></form></section></main>`;$('#login').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=$('button',f);b.disabled=true;try{me=await api('/login','POST',Object.fromEntries(new FormData(f)));view='home';projectId=null;search='';filter='';if(me.must_change){forcePassword();}else{await load();startRefresh();}}catch(error){$('.error',f).textContent=error.message;}finally{b.disabled=false;}});}
function forcePassword() {$('#root').innerHTML='<div class="loading">首次登录，请修改初始密码后开始使用。</div>';passwordForm();}
function startRefresh() {clearInterval(refreshTimer);refreshTimer=setInterval(()=>{if(me&&!document.hidden&&!$('#modal').open&&!search)(maintenanceId?checkMaintenanceAccess():load()).catch(()=>{});},30000);}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 try {
  if((b.dataset.toggleTask&&!canEdit(b.dataset.toggleTask))||((b.dataset.stockIn||b.dataset.stockOut)&&!canCreate('stock_movements'))){toast('当前空间为只读权限');return;}
  if(b.dataset.view){view=b.dataset.view;projectId=null;subsectionId='';search='';filter='';closeModal();draw();window.scrollTo(0,0);}
  else if(b.dataset.previewFile)await previewFile(b.dataset.previewFile);
  else if(b.dataset.downloadFile)await downloadFile(b.dataset.downloadFile,b);
  else if(b.dataset.deleteProject)deleteProjectForm(b.dataset.deleteProject);
  else if(b.dataset.new)editor(b.dataset.new);
  else if(b.dataset.edit){const r=find(b.dataset.edit);editor(r.kind,r.id);}
  else if(b.dataset.reimburse)reimbursementEditor(b.dataset.reimburse);
  else if(b.dataset.stockIn)inventoryEditor('stock_movements',null,{product_id:b.dataset.stockIn,movement_type:'采购入库'});
  else if(b.dataset.stockOut)inventoryEditor('stock_movements',null,{product_id:b.dataset.stockOut,movement_type:'销售出库'});
  else if(b.dataset.stockLedger){stockProductFilter=b.dataset.stockLedger;inventoryTab='movements';draw();}
  else if(b.dataset.inventoryTab){inventoryTab=b.dataset.inventoryTab;draw();}
  else if(b.dataset.project){view='projects';projectId=b.dataset.project;subsectionId='';projectTab='notes';draw();window.scrollTo(0,0);}
  else if(b.hasAttribute('data-section')){subsectionId=b.dataset.section;draw();}
  else if(b.dataset.tab){projectTab=b.dataset.tab;draw();}
  else if(b.dataset.taskFilter){taskFilter=b.dataset.taskFilter;draw();}
  else if(b.dataset.toggleTask){b.disabled=true;const t=find(b.dataset.toggleTask);await api('/records/tasks/'+t.id,'PATCH',{...t,status:t.status==='已完成'?'待办':'已完成'});await load();toast('待办状态已更新');}
  else if(b.dataset.history)await history(b.dataset.history);
  else if(b.dataset.audit){const a=events.find(x=>x.id===b.dataset.audit);if(a)showAudit(a);}
  else if(b.dataset.resetUser)userEditor(b.dataset.resetUser);
  else if(b.dataset.userActive){const u=allAccounts.find(p=>p.id===b.dataset.userActive);openModal((u.active?'停用':'启用')+' '+u.name,formWrap(`<p class="wide">${u.active?'停用后，该成员将立即退出登录，历史署名会保留。':'启用后，该成员可以再次登录。'}</p>`,'确认'+(u.active?'停用':'启用')));bindSubmit(async()=>{await api('/users/'+u.id,'PATCH',{active:!u.active});closeModal();await load();toast('账号已更新');});}
  else if(b.dataset.action){switch(b.dataset.action){
   case 'new-project':editor('projects');break;case 'new-task':editor('tasks');break;case 'new-transaction':editor('transactions');break;
   case 'new-product':inventoryEditor('products');break;case 'new-stock':inventoryEditor('stock_movements');break;
   case 'account':account();break;case 'close-modal':if(me?.must_change){me=null;loginPage();}closeModal();break;
   case 'back-projects':projectId=null;draw();break;case 'password':passwordForm();break;case 'upload-form':uploadForm();break;case 'file-link-form':editor('files',null,true);break;
   case 'refresh':await load();closeModal();toast('已刷新');break;
   case 'logout':await api('/logout','POST');me=null;closeModal();loginPage();break;
  }}
 }catch(error){toast(error.message);b.disabled=false;}
});
document.addEventListener('input',e=>{if(e.target.id==='search'){const pos=e.target.selectionStart;search=e.target.value;$('#page').innerHTML=renderPage();applyTimelineColors();applyWorkspaceAccess();const input=$('#search');input.focus();input.setSelectionRange(pos,pos);}});
document.addEventListener('change',e=>{
 if(e.target.name==='task_action')syncTaskControls();
 if(e.target.name==='project_id')refreshProjectFields();
 if(e.target.name==='project_id'&&$('#editor [name=movement_type]'))syncStockFinance(true);
 if(e.target.name==='movement_type')syncStockFinance(true);
 if(e.target.name==='finance_action')syncStockFinance();
 if(['product_id','warehouse_id'].includes(e.target.name))stockAvailability();
 if(e.target.name==='inventory_warehouse'){warehouseId=e.target.value;search='';draw();}
 if(e.target.name==='stock_product_filter'){stockProductFilter=e.target.value;draw();}
 if(['direction','reimbursement_status'].includes(e.target.name))syncReimbursementFields();
 if(e.target.name==='task_scope')syncTaskScope();
 if(e.target.name==='finance_profile'){financeProfileId=e.target.value;search='';filter='';reimbursementFilter='';draw();}
 if(e.target.id==='reimbursement-filter'){reimbursementFilter=e.target.value;draw();}
 if(e.target.id==='filter'){filter=e.target.value;$('#page').innerHTML=renderPage();applyTimelineColors();applyWorkspaceAccess();}
});
window.addEventListener('online',()=>{if(me&&!me.must_change)load().catch(e=>toast(e.message));});
window.addEventListener('offline',()=>toast('当前离线，请联网后保存记录'));
$('#modal').addEventListener('close',()=>{if(!$('#modal').open)clearFilePreview();});
$('#modal').addEventListener('cancel',()=>{if(me?.must_change){me=null;loginPage();}});
(async()=>{try{me=await api('/me');if(me.must_change)forcePassword();else{await load();startRefresh();}}catch{loginPage();}if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});})();
