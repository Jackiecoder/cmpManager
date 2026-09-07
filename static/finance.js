(function(root) {
 'use strict';
 const statuses = ['待确认','不需报销','待报销','部分报销','已报销'];
 const status = t => t.reimbursement_status || (t.direction === '收入' ? '不需报销' : '待确认');
 const cents = value => Math.round(Number(value || 0) * 100);
 function ledger(records, profileId='', search='', filter='', reimbursement='') {
   const rows = records.filter(t => t.kind === 'transactions' && (t.profile_id || '') === profileId
     && (!filter || t.direction === filter || t.payment_status === filter)
     && (!reimbursement || status(t) === reimbursement)
     && [t.title,t.event,t.responsible,t.note,t.category,t.payment_method].join(' ').toLowerCase().includes(search.toLowerCase()))
     .sort((a,b) => a.date.localeCompare(b.date) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
   const totals = {}, balances = {};
   for (const t of rows) {
     const total = totals[t.currency] ||= {income:0,expense:0,pending:0,unknown:0};
     total[t.direction === '收入' ? 'income' : 'expense'] += cents(t.amount);
     if (t.direction === '支出') {
       if (status(t) === '待确认') total.unknown++;
       if (['待报销','部分报销'].includes(status(t))) total.pending += cents(t.claim_amount) - cents(t.reimbursed_amount);
     }
     balances[t.id] = total.income - total.expense;
   }
   return {rows,totals,balances};
 }
 root.CmpFinance = {statuses,status,cents,ledger};
})(typeof window === 'undefined' ? globalThis : window);
