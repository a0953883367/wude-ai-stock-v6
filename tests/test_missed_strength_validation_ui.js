const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('index.html','utf8');
[
  '強勢股漏選驗證 V1',
  'reports/missed_strength_validation.json',
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
  '不修改 V6、ALL、TOP10、60日閘門',
].forEach(text=>{if(!html.includes(text))throw new Error('missing missed-strength UI: '+text);});
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match=>match[1]).filter(Boolean);
scripts.forEach((source,index)=>new vm.Script(source,{filename:'missed-strength-inline-'+index+'.js'}));
console.log('missed strength validation UI: all tests passed');
