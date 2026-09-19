#!/usr/bin/env node
/** Evidence-gated PPTX create/edit pipeline with PNG, PDF and HTML export. */

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";
import { FileBlob, Presentation, PresentationFile } from "@oai/artifact-tool";

function parseArgs(argv) {
  const result = { mode: "create", formats: "pptx,pdf,html" };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--help") result.help = true;
    else if (key.startsWith("--")) result[key.slice(2)] = argv[++index];
  }
  return result;
}

function usage() {
  return "presentation_pipeline.mjs --spec <spec.json> --output <final.pptx> [--mode create|edit] [--source source.pptx] [--formats pptx,pdf,html]";
}

function requireAbsolute(value, label) {
  if (!value || !path.isAbsolute(value)) throw new Error(`${label} 必須是絕對路徑`);
  return value;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[character]);
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} 結束碼 ${code}`)));
  });
}

function getSlides(presentation) {
  if (Array.isArray(presentation.slides?.items)) return presentation.slides.items;
  if (Number.isInteger(presentation.slides?.count)) {
    return Array.from({ length: presentation.slides.count }, (_, index) => presentation.slides.getItem(index));
  }
  throw new Error("無法讀取投影片集合");
}

function addTextbox(slide, position, text, style) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = style;
  return shape;
}

async function createPresentation(spec, family) {
  if (!Array.isArray(spec.slides) || spec.slides.length < 1) throw new Error("spec.slides 至少需要一頁");
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  for (let index = 0; index < spec.slides.length; index += 1) {
    const item = spec.slides[index] || {};
    const slide = presentation.slides.add();
    slide.background.fill = index === 0 ? "#0B1F33" : "#F7F3E8";
    const dark = index === 0;
    addTextbox(slide, { left: 72, top: 62, width: 1136, height: 92 }, String(item.title || spec.title || "未命名簡報"), {
      typeface: family, fontSize: index === 0 ? 46 : 38, bold: true,
      color: dark ? "#FFFFFF" : "#12372A", autoFit: "shrinkText", verticalAlignment: "middle",
    });
    const body = Array.isArray(item.body) ? item.body.map(String) : [String(item.body || spec.subtitle || "")];
    const paragraphs = body.filter(Boolean).map((value) => ({
      bulletCharacter: index === 0 ? undefined : "•",
      marginLeft: index === 0 ? 0 : 24 * 12700,
      indent: index === 0 ? 0 : -10 * 12700,
      spaceAfter: 900,
      runs: [value],
    }));
    const content = addTextbox(slide, { left: 92, top: 190, width: 1060, height: 400 }, paragraphs, {
      typeface: family, fontSize: index === 0 ? 26 : 24, color: dark ? "#D7E9E2" : "#20342E",
      autoFit: "shrinkText", wrap: "square", verticalAlignment: "top",
    });
    content.text.paragraphFormat = { spaceAfter: 9 };
    const sourceLabel = Array.isArray(spec.sources) && spec.sources.length
      ? `來源：${spec.sources.map((value) => path.basename(String(value))).join("、")}`
      : "來源：使用者提供內容";
    addTextbox(slide, { left: 72, top: 665, width: 1030, height: 28 }, sourceLabel, {
      typeface: family, fontSize: 12, color: dark ? "#AFC7BE" : "#68766F", autoFit: "shrinkText",
    });
    addTextbox(slide, { left: 1120, top: 665, width: 88, height: 28 }, `${index + 1} / ${spec.slides.length}`, {
      typeface: family, fontSize: 12, color: dark ? "#AFC7BE" : "#68766F", alignment: "right", autoFit: "none",
    });
  }
  return presentation;
}

async function editPresentation(spec, sourcePath) {
  const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));
  const replacements = Array.isArray(spec.replacements) ? spec.replacements : [];
  for (const replacement of replacements) {
    const oldText = String(replacement.old || "");
    const newText = String(replacement.new || "");
    if (!oldText) continue;
    const snapshot = await presentation.inspect({ kind: "textbox,shape", search: oldText, maxChars: 20000 });
    for (const line of String(snapshot.ndjson || "").split("\n").filter(Boolean)) {
      const record = JSON.parse(line);
      const anchor = record.id || record.anchorId || record.anchor_id;
      if (!anchor) continue;
      const target = presentation.resolve(anchor);
      if (target?.text?.replace) target.text.replace(oldText, newText);
    }
  }
  return presentation;
}

async function renderAll(finalPath, renderDir) {
  await fs.mkdir(renderDir, { recursive: true });
  const imported = await PresentationFile.importPptx(await FileBlob.load(finalPath));
  const slides = getSlides(imported);
  const paths = [];
  for (let index = 0; index < slides.length; index += 1) {
    const output = path.join(renderDir, `slide-${index + 1}.png`);
    const blob = await imported.export({ slide: slides[index], format: "png", scale: 1 });
    await fs.writeFile(output, new Uint8Array(await blob.arrayBuffer()));
    paths.push(output);
  }
  return paths;
}

async function exportHtml(spec, images, htmlPath) {
  const sections = images.map((image, index) => `<section><img src="${path.basename(path.dirname(image))}/${path.basename(image)}" alt="投影片 ${index + 1}"></section>`).join("\n");
  const html = `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${escapeHtml(spec.title || "簡報")}</title><style>body{margin:0;background:#17212b;font-family:system-ui}section{max-width:1280px;margin:24px auto}img{display:block;width:100%;height:auto;box-shadow:0 8px 30px #0006}</style><body>${sections}</body></html>`;
  await fs.writeFile(htmlPath, html, "utf8");
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(usage());
  process.exit(0);
}
const specPath = requireAbsolute(args.spec, "spec");
const finalPath = requireAbsolute(args.output, "output");
const skillDir = requireAbsolute(process.env.SKILL_DIR, "SKILL_DIR");
const runtimePython = requireAbsolute(process.env.RUNTIME_PYTHON, "RUNTIME_PYTHON");
const soffice = requireAbsolute(process.env.SOFFICE, "SOFFICE");
const outputDir = path.dirname(finalPath);
const workspaceDir = path.dirname(outputDir);
await fs.mkdir(outputDir, { recursive: true });
const spec = JSON.parse(await fs.readFile(specPath, "utf8"));
for (const source of spec.sources || []) await fs.access(path.resolve(path.dirname(specPath), String(source)));
const { resolvePresentationFont, finalizePresentation } = await import(pathToFileURL(path.join(skillDir, "container_tools/artifact_tool_utils.mjs")).href);
const family = String(spec.fontFamily || resolvePresentationFont());
const presentation = args.mode === "edit"
  ? await editPresentation(spec, requireAbsolute(args.source, "source"))
  : await createPresentation(spec, family);
const slides = getSlides(presentation);
const stagingDir = path.join(workspaceDir, `.presentation-staging-${path.basename(outputDir)}`);
await fs.mkdir(stagingDir, { recursive: true });
const candidatePath = path.join(stagingDir, `${path.basename(finalPath, ".pptx")}.candidate.pptx`);
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);
const result = await finalizePresentation({
  workspaceDir,
  candidatePath,
  finalPath,
  explicitTotalSlideCount: slides.length,
  pythonExecutable: runtimePython,
  integrityValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: ["--expected-slide-size-emu", String(spec.slideSizeEmu || "12192000,6858000"), "--validate-bullet-geometry", "--validate-heading-fit"],
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
  fontPolicy: { basis: "design", families: [family] },
  verifyArtifactToolImport: true,
  receiptPath: path.join(stagingDir, `${path.basename(finalPath)}.validation.json`),
});
const renderDir = path.join(outputDir, `${path.basename(finalPath, ".pptx")}-slides`);
const images = await renderAll(finalPath, renderDir);
const requested = new Set(String(args.formats || "pptx").split(",").map((value) => value.trim().toLowerCase()));
let pdfPath = null;
let htmlPath = null;
if (requested.has("pdf")) {
  await run(soffice, ["--headless", "--convert-to", "pdf", "--outdir", outputDir, finalPath]);
  pdfPath = path.join(outputDir, `${path.basename(finalPath, ".pptx")}.pdf`);
  await fs.access(pdfPath);
}
if (requested.has("html")) {
  htmlPath = path.join(outputDir, `${path.basename(finalPath, ".pptx")}.html`);
  await exportHtml(spec, images, htmlPath);
}
console.log(JSON.stringify({ status: "passed", mode: args.mode, finalPath, pdfPath, htmlPath, renderedSlides: images, finalizer: result }, null, 2));
