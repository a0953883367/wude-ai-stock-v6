const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const client = require('../live_client.js');
const analysis = require('../live_analysis.js');
const old = {market:'US',price:100,score:60,usLiveAvailable:true,usLivePrice:110,
  usLiveBid:109,usLiveAsk:111,usQuoteImbalance:10,usVwapDistance:1,
  usOptionSafetyScore:90};
const delayed = client.mergeStock(old,{ok:true,quote_status:'delayed',quote:{
  us_live_data_available:false,us_delayed_data_available:true,us_delayed_price:105,
  us_delayed_asof:'2026-10-16T13:30:00Z',us_delayed_minimum_seconds:960,
  us_delayed_interval:'1Min',us_delayed_source:'Alpaca SIP historical (>=16min delayed)'}});
assert.equal(delayed.usLiveAvailable,false);
assert.equal(delayed.usLivePrice,undefined);
assert.equal(delayed.usLiveBid,undefined);
assert.equal(delayed.usOptionSafetyScore,undefined);
assert.equal(delayed.price,100);
assert.equal(delayed.score,60);
assert.equal(delayed.usDelayedPrice,105);
const inputs = analysis.buildInputs(delayed);
assert.equal(inputs.price,100);
assert.equal(inputs.quoteImbalance,null);
assert.equal(inputs.vwapDistance,null);
const paid=client.mergeStock(delayed,{ok:true,quote:{us_live_data_available:true,us_live_price:107}});
assert.equal(paid.usLiveAvailable,true);
assert.equal(paid.usDelayedAvailable,false);
assert.equal(paid.usLivePrice,107);
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
const helper=html.match(/function delayedUsNote\(stock\)\{[\s\S]*?\n\}/)[0];
const ctx={number:(v)=>String(v)};vm.createContext(ctx);vm.runInContext(helper,ctx);
assert.match(ctx.delayedUsNote(delayed),/至少延遲16分鐘；非即時/);
assert.match(ctx.delayedUsNote(delayed),/2026-10-16T13:30:00Z/);
assert.match(ctx.delayedUsNote({...delayed,usDelayedInterval:'1Day'}),/日線收盤備援/);
assert.equal(ctx.delayedUsNote({...delayed,market:'TW'}),'');
for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
console.log('Delayed display/client tests passed; inline scripts parse.');
