'use strict';
let workspaceId='', currentWorkspace=null, availableSpaces=[], allAccounts=[], accountEvents=[], maintenanceItems=[];
let maintenanceId='', loadEpoch=0, maintenanceTimer;
const accessName=role=>({owner:'Owner · 公司管理员',admin:'Admin · 公司管理员',member:'成员 · 按项目和账本授权',editor:'Editor · 可编辑',viewer:'Viewer · 只读',removed:'无访问权限'}[role]||role);
const canEdit=id=>id?find(id)?._access==='editor':config.can_edit===true;
const projectAccess=id=>config.full_edit?'editor':config.project_access?.[id]||'';
const ledgerAccess=id=>config.full_edit?'editor':config.ledger_access?.[id||'']||'';
function canCreate(kind, project=projectId) {
 if(kind==='users')return me.role==='admin';
 if(config.full_edit)return true;
 if(['projects','finance_profiles','warehouses','products'].includes(kind))return false;
 if(kind==='transactions')return ledgerAccess(financeProfileId)==='editor';
 if(kind==='tasks'&&view==='finance')return ledgerAccess(financeProfileId)==='editor';
 if(kind==='tasks'&&!project)return [...Object.values(config.project_access||{}),...Object.values(config.ledger_access||{})].includes('editor');
 return project?projectAccess(project)==='editor':Object.values(config.project_access||{}).includes('editor');
}
function resourceMembersButton(kind,id) {
 return config.can_manage_resources?`<button class="secondary" data-resource-kind="${kind}" data-resource-id="${esc(id||'default')}">${kind==='projects'?'项目成员与权限':'账本成员与权限'}</button>`:'';
}
function newResourceMembers() {
 if(!config.can_manage_resources)return '';
 const members=people.filter(p=>p.active&&p.team_role==='member');
 return `<fieldset class="resource-members wide"><legend>加入此${view==='finance'?'账本':'项目'}的成员</legend><p class="hint">公司 Admin 自动拥有全部权限。其他成员只有加入后才能看到此内容。</p>${members.map(p=>`<label class="member-check"><input type="checkbox" name="resource_member" value="${p.id}"><span>${esc(p.name)} · ${esc(p.username)}</span></label>`).join('')||'<p class="hint">暂无普通成员，可先到公司成员页添加账号。</p>'}${select('resource_role','选中成员的权限',[['editor','Editor · 可编辑'],['viewer','Viewer · 只读']],'editor')}</fieldset>`;
}
async function resourceMembersEditor(kind,id) {
 const data=await api('/resource-access/'+kind+'/'+id);
 openModal((kind==='projects'?'项目':'账本')+'成员与权限',formWrap('<p class="hint wide">此处权限仅适用于当前'+(kind==='projects'?'项目及其笔记、待办、附件、库存记录。财务需单独授予账本权限。':'账本及其财务记录、报销和账本待办。')+'</p><div class="resource-permission-list wide">'+data.members.map(p=>p.company_role==='member'?select('resource_'+p.id,p.name+' · '+p.username,[['removed','不加入 / 无访问权限'],['viewer','Viewer · 只读'],['editor','Editor · 可编辑']],p.access):`<p class="hint wide">${esc(p.name)} · 公司管理员（全部权限）</p>`).join('')+'</div>','保存权限'));
 bindSubmit(async form=>{const members=data.members.filter(p=>p.company_role==='member'&&form.get('resource_'+p.id)!==p.access).map(p=>({user_id:p.id,role:form.get('resource_'+p.id),version:p.version}));await api('/resource-access/'+kind+'/'+id,'PATCH',{members});closeModal();await load();toast('权限已更新，下次请求立即生效');});
}

function workspaceControls() {
 const choices=availableSpaces.map(w=>[w.id,w.kind==='personal'?me.name+' · 个人空间':w.title+' · '+accessName(w.access)]);
 if(currentWorkspace?.maintenance)choices.unshift([currentWorkspace.id,person(currentWorkspace.owner_id)+' · 个人空间 · 维护只读']);
 return `<section class="workspace-controls" aria-label="空间与权限">${select('active_workspace','当前空间',choices,currentWorkspace?.id)}
 <div class="workspace-actions">${currentWorkspace?.kind==='team'?'<button class="secondary" data-view="team">公司成员与权限</button>':''}${me.role==='admin'?'<button class="text-button" data-action="new-team">新建公司</button>':''}</div></section>
 ${currentWorkspace?.maintenance?`<div class="notice maintenance-banner">正在只读维护 ${esc(person(currentWorkspace.owner_id))} 的个人空间，${formatTime(new Date(currentWorkspace.maintenance_expires*1000).toISOString())} 到期。每次读取均有记录。<button class="text-button" data-action="end-maintenance">结束维护</button></div>`:
 currentWorkspace?.kind==='personal'?'<p class="privacy-note">个人空间日常仅你本人可访问。平台管理员进行数据维护时，须重新验证密码并填写原因，最多只读访问 15 分钟；你可查看记录并随时结束访问。<button class="text-button" data-view="security">查看账号安全与维护记录</button></p>':
 `<p class="privacy-note">${esc(accessName(currentWorkspace?.access))} · ${config.can_manage_team?'可以管理公司的所有项目、账本和成员权限。':'仅显示分配给你的项目、账本及关联待办和操作记录。'}个人空间保持独立。</p>`}`;
}
async function switchWorkspace(id, grant='') {
 workspaceId=id;maintenanceId=grant;loadEpoch++;
 records=[];people=[];events=[];currentWorkspace=null;config={};lastSnapshot=null;
 projectId=null;subsectionId='';financeProfileId='';warehouseId='';stockProductFilter='';search='';filter='';reimbursementFilter='';view='home';
 closeModal();$('#root').innerHTML='<div class="loading">正在切换空间…</div>';
 try {await load();} catch(error) {
  if(!me)return;
  workspaceId='personal';maintenanceId='';await load();toast(error.message);
 }
 window.scrollTo(0,0);
}
function applyWorkspaceAccess() {
 document.querySelectorAll('[data-new]').forEach(b=>{b.hidden=!canCreate(b.dataset.new);});
 const actions={'new-project':'projects','new-task':'tasks','new-transaction':'transactions','new-product':'products','new-stock':'stock_movements'};
 document.querySelectorAll('[data-action]').forEach(b=>{if(actions[b.dataset.action])b.hidden=!canCreate(actions[b.dataset.action]);if(b.dataset.action==='upload-form')b.hidden=!canCreate('files');});
 document.querySelectorAll('[data-toggle-task]').forEach(b=>{b.hidden=!canEdit(b.dataset.toggleTask);});
 document.querySelectorAll('[data-stock-in],[data-stock-out]').forEach(b=>{b.hidden=!canCreate('stock_movements');});
 document.querySelectorAll('[data-edit]').forEach(b=>{if(!canEdit(b.dataset.edit)&&b.textContent.trim().startsWith('编辑'))b.textContent=b.textContent.replace('编辑','查看');});
 document.querySelectorAll('[data-reimburse]').forEach(b=>{if(!canEdit(b.dataset.reimburse))b.setAttribute('aria-label','查看报销');});
}
function readOnlyRecord(id) {
 const r=find(id);if(!r){toast('只读空间不能新增记录');return;}
 const data=Object.fromEntries(Object.entries(r).filter(([k])=>!['id','kind','version','workspace_id','created_at','updated_at','created_by','updated_by'].includes(k)));
 openModal('查看'+kindLabels[r.kind],`<p class="hint">你对此记录只有查看权限。</p><div class="audit-data">${esc(auditText(data))}</div>${r.kind==='notes'?noteAttachments(r):''}<p class="hint">${esc(person(r.updated_by))} · ${formatTime(r.updated_at)}</p><button class="text-button" data-history="${r.id}">查看修改记录</button>`);
}
function teamPage() {
 if(currentWorkspace?.kind!=='team')return empty('请先选择公司空间','个人空间没有团队成员。');
 return `<div class="section-title"><h2>${esc(currentWorkspace.title)}</h2>${config.can_manage_team?'<button class="primary" data-action="add-team-member">添加已有账号</button>':''}</div><p class="hint">Owner / Admin 管理公司的全部项目、账本和成员。普通成员仅能访问分配给自己的项目和账本；请进入具体项目或账本管理访问权限。移出公司会撤销其所有分配。</p><div class="panel">${people.map(p=>`<div class="user-row"><span class="avatar">${initials(p.name)}</span><div class="row-main"><h3>${esc(p.name)}</h3><p class="meta">${esc(p.username)} · ${esc(accessName(p.team_role))}${p.active?'':' · 账号已停用'}</p></div>${config.can_manage_team&&p.team_role!=='owner'?`<button class="secondary" data-member-role="${p.id}">调整权限</button>`:''}</div>`).join('')}</div>`;
}
function teamEditor() {
 openModal('新建公司',formWrap(field('title','公司名称','','text','required maxlength="120"')));
 bindSubmit(async form=>{const result=await api('/teams','POST',Object.fromEntries(form));await switchWorkspace(result.id);toast('公司已创建，你是 Owner');});
}
function membershipEditor(id) {
 const member=id?people.find(p=>p.id===id):null, team=currentWorkspace.id;
 openModal(member?'调整 '+member.name+' 的权限':'添加公司成员',formWrap((member?'':field('username','已有账号的用户名','','text','required autocapitalize="none"'))+select('role','团队权限',[[ 'member','普通成员 · 逐项分配权限'],['admin','Admin · 公司全部权限'],...(member?[['removed','移出公司']]:[])],member?.team_role||'member')));
 bindSubmit(async form=>{const body=Object.fromEntries(form);if(member)body.version=member.membership_version;
  await api('/teams/'+team+'/members'+(id?'/'+id:''),id?'PATCH':'POST',body);closeModal();await load();toast('公司权限已更新');});
}
function maintenanceForm(userId) {
 const user=allAccounts.find(u=>u.id===userId);
 openModal('限时只读维护：'+user.name,formWrap('<p class="notice wide">访问会记录在该成员的个人空间中，对方可以看到并随时结束。此入口不能修改个人记录。</p>'+area('reason','本次维护原因','',true)+field('password','重新验证你的管理员密码','','password','required autocomplete="current-password"'),'开始 15 分钟只读维护'));
 bindSubmit(async form=>{const grant=await api('/maintenance','POST',{...Object.fromEntries(form),user_id:userId});await switchWorkspace(grant.workspace_id,grant.id);});
}
function securityPage() {
 return `<h2>账号安全与数据维护</h2><p class="hint">密码仅保存为不可逆哈希。管理员能查看用户名并重置初始密码，不能查看你的当前密码。</p><h3>维护访问记录</h3><div class="panel">${maintenanceItems.length?maintenanceItems.map(g=>`<div class="row"><div class="row-main"><h3>${esc(g.actor_name)} · ${g.revoked?'已结束':g.expires*1000>Date.now()?'访问中':'已到期'}</h3><p>${esc(g.reason)}</p><p>${formatTime(g.created_at)} · 最晚 ${formatTime(new Date(g.expires*1000).toISOString())} 结束</p></div>${!g.revoked&&g.expires*1000>Date.now()?`<button class="secondary" data-revoke-maintenance="${g.id}">立即结束</button>`:''}</div>`).join(''):empty('暂无维护访问','所有维护访问都会在这里留下记录。')}</div><h3>账号操作记录</h3><div class="panel">${accountEvents.length?accountEvents.map(a=>`<button class="row clickable" data-security-audit="${a.id}"><span class="row-main"><strong>${esc(a.actor_name)} · ${esc(a.action)}</strong><p>${formatTime(a.at)}</p></span></button>`).join(''):empty('暂无账号操作','修改密码、重置密码和账号状态会留下记录。')}</div>`;
}
function syncMaintenanceTimer() {
 clearTimeout(maintenanceTimer);
 if(currentWorkspace?.maintenance)maintenanceTimer=setTimeout(()=>{switchWorkspace('personal').then(()=>toast('维护访问已到期')).catch(()=>{});},Math.max(0,currentWorkspace.maintenance_expires*1000-Date.now()));
}
async function checkMaintenanceAccess() {
 const requested=maintenanceId, grants=await api('/maintenance');
 if(requested!==maintenanceId)return;
 const active=grants.find(g=>g.id===requested&&!g.revoked&&g.expires*1000>Date.now());
 if(!active){await switchWorkspace('personal');toast('维护访问已结束');}
}
document.addEventListener('change',e=>{if(e.target.name==='active_workspace')switchWorkspace(e.target.value).catch(error=>toast(error.message));});
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 try {
  if(b.dataset.action==='new-team')teamEditor();
  if(b.dataset.action==='add-team-member')membershipEditor();
  if(b.dataset.memberRole)membershipEditor(b.dataset.memberRole);
  if(b.dataset.resourceKind)await resourceMembersEditor(b.dataset.resourceKind,b.dataset.resourceId);
  if(b.dataset.maintainUser)maintenanceForm(b.dataset.maintainUser);
  if(b.dataset.action==='end-maintenance'){await api('/maintenance/'+maintenanceId+'/revoke','POST');await switchWorkspace('personal');}
  if(b.dataset.revokeMaintenance){await api('/maintenance/'+b.dataset.revokeMaintenance+'/revoke','POST');await load();}
  if(b.dataset.securityAudit)showAudit(accountEvents.find(a=>a.id===b.dataset.securityAudit));
 }catch(error){toast(error.message);}
});
function syncTaskScope() {
 const field=$('#editor [name=task_scope]');if(!field)return;
 const ledger=field.value==='ledger', holder=$('#task-ledger-scope');
 holder.hidden=!ledger;
 holder.querySelectorAll('select').forEach(el=>{el.disabled=!ledger;});
 const project=$('#editor [name=project_id]');
 if(project){project.closest('.field').hidden=ledger;project.disabled=ledger;project.required=!ledger&&!config.full_edit;}
 const section=$('#section-field');if(section){section.hidden=ledger;section.querySelectorAll('select').forEach(el=>{el.disabled=ledger;});}
}

function ledgerTasksSection() {
 const tasks=byKind('tasks').filter(t=>(t.ledger_task||t.profile_id)&&(t.profile_id||'')===financeProfileId);
 return `<section class="section-space"><div class="section-title"><h2>账本待办</h2>${ledgerAccess(financeProfileId)==='editor'?'<button class="secondary" data-new="tasks">添加账本待办</button>':''}</div><div class="panel">${tasks.length?tasks.map(taskRow).join(''):empty('暂无账本待办','此处待办跟随当前账本权限。')}</div></section>`;
}
