const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../static/finance.js');
const base={kind:'transactions',date:'2026-09-07',created_at:'2026-09-07T00:00:00Z',direction:'支出',currency:'USD',payment_status:'已付',posting_status:'平帐'};
test('profiles, legacy rows, currency totals and reimbursement do not double count',()=>{
 const rows=[
  {...base,id:'a',amount:'100.10'},
  {...base,id:'b',direction:'收入',amount:'200.20'},
  {...base,id:'c',amount:'60',claim_amount:'50',reimbursed_amount:'10.10',reimbursement_status:'部分报销'},
  {...base,id:'d',currency:'CNY',amount:'500',claim_amount:'500',reimbursed_amount:'500',reimbursement_status:'已报销'},
  {...base,id:'e',profile_id:'other',amount:'999'},
 ];
 const data=CmpFinance.ledger(rows);
 assert.equal(data.rows.length,4);
 assert.deepEqual(data.totals.USD,{income:20020,expense:16010,pending:3990,unknown:1});
 assert.deepEqual(data.totals.CNY,{income:0,expense:50000,pending:0,unknown:0});
 assert.equal(data.balances.c,4010);
 assert.equal(CmpFinance.status(rows[0]),'待确认');
 assert.equal(CmpFinance.ledger(rows,'other').rows.length,1);
 assert.deepEqual(CmpFinance.ledger(rows,'','','','部分报销').rows.map(r=>r.id),['c']);
});
test('search and date order define the displayed running balance',()=>{
 const rows=[{...base,id:'b',date:'2026-09-08',title:'train',responsible:'Kevin',amount:'20'},
 {...base,id:'a',title:'taxi',responsible:'Kevin',amount:'10.25'},
 {...base,id:'c',title:'other',amount:'9'}];
 const data=CmpFinance.ledger(rows,'','kevin','支出');
 assert.deepEqual(data.rows.map(r=>r.id),['a','b']);
 assert.equal(data.balances.b,-3025);
});
test('unknown dates sort last and other dealings never count as expenses',()=>{
 const data=CmpFinance.ledger([
  {...base,id:'loan',date:'',direction:'其他',amount:'500'},
  {...base,id:'expense',amount:'30'},
  {...base,id:'income',direction:'收入',amount:'100'},
 ]);
 assert.deepEqual(data.rows.map(r=>r.id),['expense','income','loan']);
 assert.deepEqual(data.totals.USD,{income:10000,expense:3000,pending:0,unknown:1,other:50000});
 assert.equal(data.balances.loan,7000);
 assert.equal(CmpFinance.status(data.rows[2]),'不需报销');
});
