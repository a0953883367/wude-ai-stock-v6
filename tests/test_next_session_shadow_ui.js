const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('next-session-shadow.html','utf8');
const script=fs.readFileSync('next_session_shadow.js','utf8');
[
  '下一交易日預判','明日上漲機率','明日可買性','不先挑今天上漲股',
  '不讀取未來結果','不改正式V6','不改5日與60日紀錄',
  'reports/next_session_shadow_ranking.json','目前強勢排名只作輔助',
  '不因上漲直接加分','獨立續漲證據','只扣可買性','等待收盤'
].forEach(text=>{if(!(html+script).includes(text))throw new Error('missing next-session UI: '+text);});
if(!fs.readFileSync('index.html','utf8').includes('next-session-shadow.html')){
  throw new Error('homepage does not link isolated next-session page');
}
new vm.Script(script,{filename:'next_session_shadow.js'});
console.log('next-session shadow UI: all tests passed');
