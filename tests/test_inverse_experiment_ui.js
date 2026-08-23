const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('index.html','utf8');
[
  '🔴空頭反向影子實驗',
  'reports/inverse_experiment.json',
  "target:'INVERSE'",
  'A｜原 TOP10 做多',
  'B｜100% 現金',
  'C｜-1x 反向 ETF',
  '相對大盤超額',
  '缺少任一必要價格即隔離',
  '不修改 V6 或 60 日閘門',
].forEach(text=>{if(!html.includes(text))throw new Error('missing inverse experiment UI: '+text);});
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match=>match[1]).filter(Boolean);
scripts.forEach((source,index)=>new vm.Script(source,{filename:'inverse-inline-'+index+'.js'}));
console.log('inverse experiment UI: all tests passed');
