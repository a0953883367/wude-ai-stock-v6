const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('index.html','utf8');
[
  '強勢股漏選＋多期間答案 V2',
  'reports/missed_strength_validation.json',
  'reports/forward_outcome_ledger.json',
  "target:'MISSED_STRENGTH'",
  '實際TOP10捕捉',
  '實際TOP20捕捉',
  '資料完整但判斷失誤',
  '資料不完整',
  '事前排名',
  '事前分數',
  '壓低因子',
  '量能／量價',
  '未滿20個有效交易日，禁止提前歸納',
  '5日內最大漲幅',
  '5／45／60／126個交易日',
  '答案只供影子學習',
].forEach(text=>{if(!html.includes(text))throw new Error('missing missed-strength UI: '+text);});
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match=>match[1]).filter(Boolean);
scripts.forEach((source,index)=>new vm.Script(source,{filename:'missed-strength-inline-'+index+'.js'}));
console.log('missed strength validation UI: all tests passed');
