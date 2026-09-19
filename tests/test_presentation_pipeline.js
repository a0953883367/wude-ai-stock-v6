const fs = require('fs');
const {execFileSync} = require('child_process');

execFileSync(process.execPath, ['--check', 'presentation_pipeline.mjs'], {stdio: 'inherit'});
const source = fs.readFileSync('presentation_pipeline.mjs', 'utf8');
for (const required of ['PresentationFile.exportPptx', 'finalizePresentation', 'verifyArtifactToolImport', 'renderAll', '--convert-to', 'exportHtml', 'editPresentation']) {
  if (!source.includes(required)) throw new Error(`缺少簡報流程：${required}`);
}
const template = JSON.parse(fs.readFileSync('agent_workspaces/PPT自動產生規格範本.json', 'utf8'));
if (template.schema !== 'wude.presentation_spec.v1' || template.slides.length < 2) throw new Error('簡報規格範本無效');
