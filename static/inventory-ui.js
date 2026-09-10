'use strict';
let warehouseId='', inventoryTab='balances', stockProductFilter='';
const warehouseName=id=>find(id)?.title||'默认仓库';
const productName=id=>find(id)?.title||'未知商品';
function warehouseSelect(value,name='warehouse_id') {
 return select(name,'仓库',[['','默认仓库'],...byKind('warehouses').slice().sort((a,b)=>a.created_at.localeCompare(b.created_at)).map(w=>[w.id,w.title])],value);
}
function stockProductSelect(value,name='product_id',required=true) {
 return select(name,'商品 / SKU',[[ '',required?'请选择商品':'全部商品'],...byKind('products').map(p=>[p.id,`${p.title} · ${p.sku}（${p.unit}）`])],value,required);
}
function inventoryPage() {
 const products=byKind('products'), balances=CmpInventory.balances(records,warehouseId);
 const visible=products.filter(p=>(!stockProductFilter||p.id===stockProductFilter)&&[p.title,p.sku,p.description].join(' ').toLowerCase().includes(search.toLowerCase()));
 const moves=byKind('stock_movements').filter(m=>m.warehouse_id===warehouseId&&(!stockProductFilter||m.product_id===stockProductFilter)&&[m.title,m.note,productName(m.product_id),find(m.product_id)?.sku,projectName(m.project_id)].join(' ').toLowerCase().includes(search.toLowerCase()));
 const low=products.filter(p=>(balances[p.id]||0)<=CmpInventory.milli(p.low_stock));
 return `<div class="inventory-tools">${warehouseSelect(warehouseId,'inventory_warehouse')}<button class="secondary" data-new="warehouses">新建仓库</button>${warehouseId?`<button class="text-button" data-edit="${warehouseId}">编辑仓库</button>`:''}<button class="secondary" data-new="products">${icon('plus')} 新建商品</button></div>
 ${find(warehouseId)?.description?`<p class="hint">${esc(find(warehouseId).description)}</p>`:''}
 <div class="inventory-totals"><span><strong>${products.filter(p=>(balances[p.id]||0)>0).length}</strong>种商品有库存</span><span><strong>${low.length}</strong>种达到库存提醒线</span><span><strong>${byKind('stock_movements').filter(m=>m.warehouse_id===warehouseId).length}</strong>笔出入库</span></div>
 <nav class="detail-tabs" aria-label="库存内容"><button data-inventory-tab="balances" class="${inventoryTab==='balances'?'active':''}">库存余额</button><button data-inventory-tab="movements" class="${inventoryTab==='movements'?'active':''}">出入库流水</button></nav>
 <div class="finance-filters inventory-filters"><input id="search" type="search" placeholder="搜索商品、SKU 或流水" aria-label="搜索商品、SKU 或流水" value="${esc(search)}">${stockProductSelect(stockProductFilter,'stock_product_filter',false)}</div>
 <p class="hint">${config.full_access?'余额由当前仓库的全部出入库计算。':'这里只汇总已授权项目的出入库，不代表整个仓库的可用库存。'}商品按各自单位计量；期初库存通过“期初入库”录入，盘点差异通过盘盈或盘亏记录。</p>
 ${!products.length?`<div class="panel">${empty('先建立商品档案','填写商品名称、SKU 和计量单位，再录入期初库存或采购入库。','new-product','新建商品','inventory')}</div>`:inventoryTab==='movements'?`<div class="panel">${moves.length?stockMovementList(moves):empty('暂无出入库记录','记录每次采购、销售、领用或盘点调整。','new-stock','记录出入库','inventory')}</div>`:
 visible.length?`<div class="ledger-scroll" role="region" aria-label="库存余额表" tabindex="0"><table class="ledger-table"><thead><tr>${['商品','库存数量','SKU','提醒线','状态','操作'].map(x=>`<th scope="col">${x}</th>`).join('')}</tr></thead><tbody>${visible.map(p=>`<tr><td><button class="text-button" data-edit="${p.id}">${esc(p.title)}</button></td><td class="numeric"><span class="stock-quantity">${CmpInventory.format((balances[p.id]||0)/1000)}</span> ${esc(p.unit)}</td><td>${esc(p.sku)}</td><td class="numeric">${CmpInventory.format(p.low_stock)} ${esc(p.unit)}</td><td>${(balances[p.id]||0)<=CmpInventory.milli(p.low_stock)?'<span class="stock-low">库存偏低</span>':'正常'}</td><td><div class="stock-links"><button class="text-button" data-stock-in="${p.id}">入库</button><button class="text-button" data-stock-out="${p.id}">出库</button><button class="text-button" data-stock-ledger="${p.id}">流水</button></div></td></tr>`).join('')}</tbody></table></div>`:`<div class="panel">${empty('没有匹配的商品','调整搜索或商品筛选后重试。')}</div>`}`;
}
function stockMovementList(items) {
 return items.slice().sort((a,b)=>b.date.localeCompare(a.date)||b.created_at.localeCompare(a.created_at)).map(stockMovementRow).join('');
}
function stockMovementRow(m) {
 const product=find(m.product_id), transaction=find(m.linked_transaction_id);
 return `<article class="stock-row"><div class="spread"><div><h3><button class="text-button" data-edit="${m.id}">${esc(m.title)}</button></h3><p>${esc(m.date)} · ${esc(m.movement_type)}</p></div><span class="stock-quantity">${CmpInventory.types[m.movement_type]>0?'+':'−'}${CmpInventory.format(m.quantity)} ${esc(product?.unit||'')}</span></div><p>${esc(productName(m.product_id))} · ${esc(product?.sku)} · ${esc(warehouseName(m.warehouse_id))}</p><p>${esc(sectionName(m.subsection_id))}${m.note?' · '+esc(m.note):''}</p><div class="stock-links">${m.project_id?`<button class="text-button" data-project="${m.project_id}">项目：${esc(projectName(m.project_id))}</button>`:'<span class="meta">未关联项目</span>'}${transaction&&config.finance_access?`<button class="text-button" data-edit="${transaction.id}">财务：${esc(transaction.title)} · ${cash(transaction.amount,transaction.currency)}</button>${reimbursementBadge(transaction)}`:'<span class="meta">未显示关联财务</span>'}<button class="text-button" data-history="${m.id}">修改记录</button></div><p>${esc(person(m.updated_by))} · ${formatTime(m.updated_at)}</p></article>`;
}
function stockFinanceLinks(transactionId) {
 const linked=byKind('stock_movements').filter(m=>m.linked_transaction_id===transactionId);
 return linked.length?`<section class="wide"><h3>关联出入库</h3><div class="stock-linked-list">${linked.map(m=>`<button type="button" class="text-button" data-edit="${m.id}">${esc(m.movement_type)}：${esc(productName(m.product_id))} · ${CmpInventory.format(m.quantity)} ${esc(find(m.product_id)?.unit)} · ${esc(warehouseName(m.warehouse_id))}</button>`).join('')}</div><p class="hint">这笔收支可关联多条商品流水；这里只记一笔财务金额。</p></section>`:'';
}
function stockTransactionOptions(project,type) {
 const direction=CmpInventory.finance[type];
 return [['','请选择财务记录'],...byKind('transactions').filter(t=>(t.project_id||'')===project&&t.direction===direction).map(t=>[t.id,`${t.title} · ${cash(t.amount,t.currency)} · ${financeProfileName(t.profile_id)}`])];
}
function stockFinanceControls(data) {
 if(!config.finance_access)return '';
 return `<fieldset class="stock-finance wide"><legend>关联财务</legend>${select('finance_action','保存出入库时',[['none','只记录库存，不关联财务'],['link','关联已有财务记录'],...(!data.linked_transaction_id?[['create','同时创建一笔收支']]:[])],data.linked_transaction_id?'link':'none')}
 <div id="stock-finance-link">${select('linked_transaction_id','已有财务记录',stockTransactionOptions(data.project_id||'',data.movement_type),data.linked_transaction_id)}</div>
 <div id="stock-finance-create">${financeProfileSelect(financeProfileId,'finance_profile_id','记入财务账本')}${select('finance_currency','财务币种',['USD','CNY'],'USD')}${field('finance_amount','本次收支总金额','','number','min="0" step="0.01"')}${select('finance_payment_status','支付状态',['未付','已付','部分支付'],'未付')}${field('finance_responsible','负责人 / 垫付人',me.name)}<p class="hint">采购入库、销售退货记支出；销售出库、采购退货记收入。只创建一笔总金额，报销可在财务里继续跟进。</p></div>
 <p class="hint">库存与财务同时保存。关联后修改库存数量不会自动改写财务金额；一张单据包含多个商品时，其余商品请选择关联已有记录。解除关联会保留原财务记录。</p></fieldset>`;
}
function syncStockFinance(refresh=false) {
 const action=$('#editor [name=finance_action]');if(!action)return;
 const type=$('#editor [name=movement_type]').value, project=$('#editor [name=project_id]').value;
 const supported=!!CmpInventory.finance[type];
 if(!supported)action.value='none';action.disabled=!supported;
 if(refresh)$('#stock-finance-link').innerHTML=select('linked_transaction_id','已有财务记录',stockTransactionOptions(project,type));
 $('#stock-finance-link').hidden=action.value!=='link';$('#stock-finance-create').hidden=action.value!=='create';
 for(const input of $('#stock-finance-link').querySelectorAll('input,select')) input.disabled=action.value!=='link';
 for(const input of $('#stock-finance-create').querySelectorAll('input,select')) input.disabled=action.value!=='create';
 $('#editor [name=linked_transaction_id]').required=action.value==='link';
 $('#editor [name=finance_amount]').required=action.value==='create';
}
function stockAvailability() {
 const product=$('#editor [name=product_id]')?.value, warehouse=$('#editor [name=warehouse_id]')?.value;
 if(!$('#stock-availability'))return;
 const amount=CmpInventory.balances(records,warehouse)[product]||0;
 $('#stock-availability').textContent=product?`${config.full_access?'当前库存':'已授权项目净变动'}：${CmpInventory.format(amount/1000)} ${find(product)?.unit||''}；修改流水会重新计算余额。`:'请选择商品和仓库。';
}
function inventoryEditor(kind,id,preset={}) {
 if(!(id?canEdit(id):canCreate(kind))){readOnlyRecord(id);return;}
 const record=id?find(id):null;
 const data=record||{warehouse_id:warehouseId,product_id:stockProductFilter,project_id:projectId||'',subsection_id:subsectionId==='unassigned'?'':subsectionId,date:today(),movement_type:'采购入库',unit:'件',low_stock:'0',...preset};
 let content='';
 if(kind==='warehouses')content=field('title','仓库名称',data.title,'text','required maxlength="120"')+area('description','位置 / 仓库说明',data.description);
 if(kind==='products')content=field('title','商品名称',data.title,'text','required maxlength="120"')+field('sku','商品编号 / SKU',data.sku,'text','required maxlength="80"')+field('unit','计量单位',data.unit,'text','required maxlength="20" placeholder="件、箱、kg…"')+field('low_stock','低库存提醒线',data.low_stock,'number','min="0" step="0.001" required')+area('description','规格 / 商品说明',data.description);
 if(kind==='stock_movements') {
   if(!byKind('products').length){toast('请先新建商品');inventoryEditor('products');return;}
   content=field('title','流水标题',data.title,'text','required maxlength="200" placeholder="例如：样品到货、客户订单出库"')+stockProductSelect(data.product_id)+warehouseSelect(data.warehouse_id)+select('movement_type','出入库类型',Object.keys(CmpInventory.types),data.movement_type)+field('date','出入库日期',data.date,'date','required')+field('quantity','数量（按商品单位）',data.quantity,'number','min="0.001" step="0.001" required')+'<p class="stock-availability wide" id="stock-availability"></p>'+projectSelect(data.project_id,!config.full_edit)+sectionSelect(data.project_id,data.subsection_id)+area('note','备注 / 单据号',data.note)+stockFinanceControls(data);
 }
 openModal((id?'编辑':'新增')+kindLabels[kind],formWrap(content)+(record?`<p class="hint">${esc(person(record.updated_by))} · ${formatTime(record.updated_at)} <button class="text-button" data-history="${id}">修改记录</button></p>`:''));
 if(kind==='stock_movements'){syncStockFinance();stockAvailability();}
 bindSubmit(async form=>{
   const body=Object.fromEntries(form);if(id)body.version=record.version;
   if(kind==='stock_movements'&&config.finance_access)body.finance_action=form.get('finance_action')||'none';
   const saved=await api('/records/'+kind+(id?'/'+id:''),id?'PATCH':'POST',body);
   if(kind==='warehouses'&&!id){warehouseId=saved.id;stockProductFilter='';}
   closeModal();await load();toast('已保存，更新人：'+me.name);
 });
}
