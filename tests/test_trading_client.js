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

const candidates = [
  {symbol:'2330.TW',name:'台積電',market:'TW'},
  {symbol:'2317.TW',name:'鴻海',market:'TW'},
  {symbol:'NVDA',name:'NVIDIA',market:'US'}
];
assert.deepStrictEqual(trading.pickerRows(candidates, '台積', []).map(row => row.symbol), ['2330.TW']);
assert.deepStrictEqual(trading.pickerRows(candidates, 'NVDA', []).map(row => row.symbol), ['NVDA']);
assert.strictEqual(trading.pickerRows(candidates, '', [{symbol:'NVDA'}])[0].symbol, 'NVDA');
console.log('trading_client: all tests passed');
