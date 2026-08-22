const assert = require('assert');
const trading = require('../trading_client.js');

function storage() {
  const values = {};
  return {getItem:key => values[key] || null,setItem:(key,value)=>{values[key]=value;}};
}

assert.deepStrictEqual(trading.allocation(20000, 2), {cash:20000,reserve:2000,perSymbol:9000});
assert.deepStrictEqual(trading.allocation(10000, 2), {cash:10000,reserve:1000,perSymbol:4500});
assert.strictEqual(trading.normalize('2330'), '2330.TW');
assert.strictEqual(trading.normalize('nvda'), 'NVDA');

const store = storage();
trading.toggle({symbol:'2330',name:'台積電',market:'TW'}, store);
trading.toggle({symbol:'NVDA',name:'NVIDIA',market:'US'}, store);
trading.toggle({symbol:'2317',name:'鴻海',market:'TW'}, store);
assert.strictEqual(trading.load(store).length, 3);
assert.throws(() => trading.toggle({symbol:'2308',name:'台達電',market:'TW'}, store), /最多只能勾選3檔/);
trading.toggle({symbol:'NVDA'}, store);
assert.strictEqual(trading.load(store).length, 2);
console.log('trading_client: all tests passed');
