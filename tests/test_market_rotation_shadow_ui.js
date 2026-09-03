'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="ROTATION"',
  'reports/market_rotation_shadow.json',
  'function showRotationShadow()',
  'function rotationMarketHtml(market)',
  '市場規則＋族群輪動影子模型',
  'A｜正式V6基準',
  'B｜市場規則＋輪動15%',
  '點火期',
  '擴散期',
  '高潮期',
  '退潮期',
  '5日只查程式，20日初步比較，60日後仍只標升級候選',
  '不改排名、不下單',
  '台股五條件研究資格｜不等於可買',
  '嚴格組5／5全過',
  '至少3檔共振',
  '缺法人資料列資料不足',
  '嚴格組跳空失效',
  '日結資金流',
  'function rotationFlowCell(item)',
  '成分股 ',
  '等待成分股',
  '收盤資金流已連動',
  '等待完整收盤資金流'
].forEach((text) => assert.ok(html.includes(text), `missing rotation UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('market rotation shadow UI: all tests passed');
