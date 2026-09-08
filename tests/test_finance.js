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
test('profile profit combines recorded conversions and excludes loans and reimbursements',()=>{
 const records=[
  {...base,id:'income',profile_id:'club',direction:'收入',amount:'150.10',usd_amount:'999'},
  {...base,id:'usd-expense',profile_id:'club',amount:'20.20',payment_status:'未付'},
  {...base,id:'cny-expense',profile_id:'club',currency:'CNY',amount:'100.00',usd_amount:'14.75',
    reimbursement_status:'已报销',claim_amount:'100.00',reimbursed_amount:'100.00'},
  {...base,id:'loan',profile_id:'club',direction:'其他',amount:'500'},
  {...base,id:'other-profile',amount:'9999'},
 ];
 assert.deepEqual(CmpFinance.profitSummary(records,'club'),{
  currency:'USD',converted:true,count:3,income:15010,expense:3495,net:11515,missingConversions:0,state:'profit',
 });
 // A filtered detail list must not mutate the whole-profile result.
 CmpFinance.ledger(records,'club','','支出','已报销');
 assert.equal(CmpFinance.profitSummary(records,'club').net,11515);
});
test('missing conversions prevent an overall profit/loss claim, but zero conversion is valid',()=>{
 const rows=[{...base,id:'a',direction:'收入',amount:'10'},
  {...base,id:'b',currency:'CNY',amount:'100',usd_amount:null}];
 for(const value of [null,undefined,'','invalid']) {
  rows[1].usd_amount=value;
  const result=CmpFinance.profitSummary(rows);
  assert.equal(result.state,'incomplete');assert.equal(result.net,null);assert.equal(result.missingConversions,1);
 }
 rows[1].usd_amount='0.00';
 assert.equal(CmpFinance.profitSummary(rows).net,1000);
 rows[1].usd_amount='15.00';
 assert.equal(CmpFinance.profitSummary(rows).state,'loss');
 assert.equal(CmpFinance.profitSummary(rows).net,-500);
});
test('single currency, balanced and empty profiles keep distinct meanings',()=>{
 const rows=[{...base,id:'a',currency:'CNY',direction:'收入',amount:'0.30'},
  {...base,id:'b',currency:'CNY',amount:'0.10'},
  {...base,id:'c',currency:'CNY',amount:'0.20'}];
 const result=CmpFinance.profitSummary(rows);
 assert.equal(result.currency,'CNY');assert.equal(result.state,'balanced');assert.equal(result.net,0);
 assert.equal(result.missingConversions,0);
 assert.equal(CmpFinance.profitSummary(rows,'empty').state,'empty');
 assert.equal(CmpFinance.profitSummary([{...base,direction:'其他',amount:'500'}]).state,'empty');
});
