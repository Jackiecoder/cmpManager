(function(root) {
 'use strict';
 const types={'期初入库':1,'采购入库':1,'销售出库':-1,'领用出库':-1,'销售退货入库':1,'采购退货出库':-1,'盘盈入库':1,'盘亏出库':-1};
 const finance={'采购入库':'支出','销售出库':'收入','销售退货入库':'支出','采购退货出库':'收入'};
 const milli=value=>Math.round(Number(value||0)*1000);
 const format=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:3}).format(value);
 function balances(records,warehouseId) {
   const totals={};
   for(const m of records) if(m.kind==='stock_movements'&&m.warehouse_id===warehouseId) {
     totals[m.product_id]=(totals[m.product_id]||0)+milli(m.quantity)*types[m.movement_type];
   }
   return totals;
 }
 root.CmpInventory={types,finance,milli,format,balances};
})(typeof window==='undefined'?globalThis:window);
