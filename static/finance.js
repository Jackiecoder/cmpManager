(function(root) {
 'use strict';
 const statuses = ['待确认','不需报销','待报销','部分报销','已报销'];
 const status = t => t.reimbursement_status || (t.direction === '支出' ? '待确认' : '不需报销');
 const cents = value => Math.round(Number(value || 0) * 100);
 function ledger(records, profileId='', search='', filter='', reimbursement='') {
   const rows = records.filter(t => t.kind === 'transactions' && (t.profile_id || '') === profileId
     && (!filter || t.direction === filter || t.payment_status === filter)
     && (!reimbursement || status(t) === reimbursement)
     && [t.title,t.event,t.responsible,t.note,t.category,t.payment_method].join(' ').toLowerCase().includes(search.toLowerCase()))
     .sort((a,b) => (a.date||'9999').localeCompare(b.date||'9999') || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
   const totals = {}, balances = {};
   for (const t of rows) {
     const total = totals[t.currency] ||= {income:0,expense:0,pending:0,unknown:0};
     const bucket = t.direction === '收入' ? 'income' : t.direction === '支出' ? 'expense' : 'other';
     total[bucket] = (total[bucket] || 0) + cents(t.amount);
     if (t.direction === '支出') {
       if (status(t) === '待确认') total.unknown++;
       if (['待报销','部分报销'].includes(status(t))) total.pending += cents(t.claim_amount) - cents(t.reimbursed_amount);
     }
     balances[t.id] = total.income - total.expense;
   }
   return {rows,totals,balances};
 }
 function profitSummary(records, profileId='') {
   // Always use the whole profile. Search and reimbursement filters must not
   // turn an expense-only view into a misleading profile loss.
   const rows = records.filter(t => t.kind === 'transactions' && (t.profile_id || '') === profileId
     && ['收入','支出'].includes(t.direction));
   const currencies = new Set(rows.map(t => t.currency));
   const converted = currencies.size > 1;
   const currency = converted ? 'USD' : (rows[0]?.currency || 'USD');
   let income = 0, expense = 0, missingConversions = 0;
   for (const t of rows) {
     const value = t.currency === currency ? t.amount : t.usd_amount;
     if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) {
       missingConversions++;
       continue;
     }
     if (t.direction === '收入') income += cents(value);
     else expense += cents(value);
   }
   const net = missingConversions ? null : income - expense;
   const state = !rows.length ? 'empty' : missingConversions ? 'incomplete'
     : net > 0 ? 'profit' : net < 0 ? 'loss' : 'balanced';
   return {currency,converted,count:rows.length,income,expense,net,missingConversions,state};
 }
 root.CmpFinance = {statuses,status,cents,ledger,profitSummary};
})(typeof window === 'undefined' ? globalThis : window);
