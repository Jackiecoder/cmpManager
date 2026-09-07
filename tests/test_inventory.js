const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../static/inventory.js');
test('warehouse balances use precise fractional quantities and all movement directions',()=>{
 const base={kind:'stock_movements',warehouse_id:'',product_id:'a'};
 const records=[
  {...base,movement_type:'期初入库',quantity:'10.125'},
  {...base,movement_type:'销售出库',quantity:'2.100'},
  {...base,movement_type:'领用出库',quantity:'0.025'},
  {...base,movement_type:'销售退货入库',quantity:'1.000'},
  {...base,movement_type:'采购退货出库',quantity:'2.000'},
  {...base,movement_type:'盘盈入库',quantity:'0.100'},
  {...base,movement_type:'盘亏出库',quantity:'0.050'},
  {...base,warehouse_id:'other',movement_type:'采购入库',quantity:'99'},
  {...base,product_id:'b',movement_type:'采购入库',quantity:'1'},
 ];
 assert.deepEqual(CmpInventory.balances(records,''),{a:7050,b:1000});
 assert.deepEqual(CmpInventory.balances(records,'other'),{a:99000});
 assert.equal(CmpInventory.format(7.05),'7.05');
});
