import { computeResize, rankPredictions, toNchw } from "./cv-core.mjs";

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const FALLBACK_MAX_BYTES = 10 * 1024 * 1024;
const STORAGE_KEY = "sukaseafood:cv-locale";

const messages = {
  zh: {
    documentTitle: "SukaSeafood · 鱼类识别演示", brandTagline: "五分类视觉识别 · I1", navTechnology: "技术", navConfiguration: "配置",
    brandAria: "SukaSeafood 项目主页", navAria: "页面导航", workspaceAria: "鱼类识别工作区", photoInputAria: "上传或拍摄鱼类照片", previewAlt: "待识别的鱼类照片",
    eyebrow: "计算机视觉概念验证", pageTitle: "鱼类识别演示", intro: "上传照片或使用手机后置相机拍摄。模型会给出五个已审核鱼种中的前三个候选。",
    privacy: "照片只在当前浏览器中处理，不会上传或保存。", photoHeading: "选择鱼类照片", metadataLoading: "读取模型配置",
    modelReady: "模型配置就绪", modelFailed: "模型不可用", pickLabel: "上传或拍摄照片", pickHelp: "JPEG、PNG 或 WebP，最大 10 MB",
    previewEmpty: "尽量让整条鱼清晰可见，并减少遮挡。", noFile: "尚未选择照片", identify: "识别鱼种", analysing: "识别中…", clear: "清除",
    resultHeading: "模型结论", waiting: "等待图片", resultEmptyTitle: "还没有识别结果", resultEmptyCopy: "选择照片后点击“识别鱼种”，这里会显示 Top-3 候选。",
    loadingTitle: "正在加载模型", loadingCopy: "首次使用需要下载约 6 MB 的模型，请稍候。", analysingTitle: "正在分析照片", analysingCopy: "预处理和推理都在你的设备上完成。",
    candidates: "候选结果", lowConfidence: "低置信度", candidatesTitle: "最可能的候选", lowTitle: "无法可靠判断", candidatesCopy: "请比较前三个候选并人工确认。",
    lowCopy: "最高分低于阈值 0.30，五个类别都可能不正确。", confirmation: "这是模型建议，不是最终鉴定。请人工确认，也可能五个候选都不正确。",
    rank: "排名", confidence: "置信度", classCode: "类别代码", seafoodId: "seafood_item_id", invalidType: "只支持 JPEG、PNG 或 WebP 图片。",
    tooLarge: "图片超过 10 MB，请压缩后重试。", decodeFailed: "无法读取这张图片，请换一张 JPEG、PNG 或 WebP。", inferenceFailed: "模型运行失败，请刷新页面后重试。",
    scopeEyebrow: "模型范围", scopeTitle: "本次只识别 5 个鱼种", technologyEyebrow: "技术", technologyTitle: "技术方案",
    modelTitle: "轻量分类模型", modelCopy: "MobileNetV3 Small 使用 ImageNet 预训练权重，在审核后的五分类数据上迁移学习。",
    runtimeTitle: "浏览器本地推理", runtimeCopy: "ONNX Runtime Web 通过 WebAssembly 在设备上运行；服务器只提供静态文件。",
    decisionTitle: "Top-3 + 人工确认", decisionCopy: "阈值为 0.30。即使超过阈值也只是候选，不能自动写入业务数据。",
    evaluationEyebrow: "清洗测试集 · N=82", evaluationTitle: "独立测试结果", accuracy: "准确率", top3: "Top-3 命中率",
    metricNote: "这些数字来自清洗后的内部测试集，不代表真实零售场景的生产准确率。", configurationEyebrow: "模型契约", configurationTitle: "运行配置",
    version: "模型版本", input: "模型输入", resize: "图像处理", resizeValue: "短边缩放至 256，中心裁剪至 224", normalize: "标准化", threshold: "候选阈值", runtime: "运行环境",
    limitationsEyebrow: "使用前须知", limitationsTitle: "已知限制", limitScope: "模型只能在五个已知类别中选择，遇到其他鱼也会尝试给出候选。",
    limitData: "训练数据量较小，且主要不是零售冰台现场照片。", limitConfusion: "Kembung 与 Cencaru 是当前最主要的混淆组合。",
    limitLicence: "部分训练图片仍需完成商业许可核查，本版本仅用于 I1 概念验证。", footer: "CV I1 演示 · 结果必须人工确认",
  },
  en: {
    documentTitle: "SukaSeafood · Fish identification demo", brandTagline: "Five-class visual identification · I1", navTechnology: "Technology", navConfiguration: "Configuration",
    brandAria: "SukaSeafood project home", navAria: "Page navigation", workspaceAria: "Fish identification workspace", photoInputAria: "Upload or take a fish photo", previewAlt: "Fish photo to identify",
    eyebrow: "Computer vision proof of concept", pageTitle: "Fish identification demo", intro: "Upload a photo or use your phone's rear camera. The model returns the top three candidates from five reviewed fish classes.",
    privacy: "The photo is processed only in this browser. It is never uploaded or saved.", photoHeading: "Choose a fish photo", metadataLoading: "Reading model config",
    modelReady: "Model config ready", modelFailed: "Model unavailable", pickLabel: "Upload or take a photo", pickHelp: "JPEG, PNG or WebP · 10 MB maximum",
    previewEmpty: "Keep the whole fish clear and reduce occlusion where possible.", noFile: "No photo selected", identify: "Identify fish", analysing: "Identifying…", clear: "Clear",
    resultHeading: "Model conclusion", waiting: "Waiting for photo", resultEmptyTitle: "No result yet", resultEmptyCopy: "Choose a photo and select “Identify fish” to see the Top-3 candidates.",
    loadingTitle: "Loading the model", loadingCopy: "The first run downloads an approximately 6 MB model.", analysingTitle: "Analysing the photo", analysingCopy: "Preprocessing and inference both run on your device.",
    candidates: "Candidates", lowConfidence: "Low confidence", candidatesTitle: "Most likely candidates", lowTitle: "No reliable match", candidatesCopy: "Compare the three suggestions and confirm manually.",
    lowCopy: "The leading score is below the 0.30 threshold; all five classes may be wrong.", confirmation: "This is a model suggestion, not a final identification. Confirm it manually; none of the five may be correct.",
    rank: "Rank", confidence: "Confidence", classCode: "Class code", seafoodId: "seafood_item_id", invalidType: "Only JPEG, PNG and WebP images are supported.",
    tooLarge: "The photo is larger than 10 MB. Compress it and try again.", decodeFailed: "This image could not be decoded. Try another JPEG, PNG or WebP.", inferenceFailed: "The model could not run. Refresh the page and try again.",
    scopeEyebrow: "Model scope", scopeTitle: "This version recognizes 5 fish classes", technologyEyebrow: "Technology", technologyTitle: "Technical approach",
    modelTitle: "Lightweight classifier", modelCopy: "MobileNetV3 Small starts from ImageNet weights and is transfer-learned on the reviewed five-class dataset.",
    runtimeTitle: "On-device browser inference", runtimeCopy: "ONNX Runtime Web runs through WebAssembly on the device; the server only serves static files.",
    decisionTitle: "Top-3 + human confirmation", decisionCopy: "The threshold is 0.30. A score above it is still only a candidate and must not update business data automatically.",
    evaluationEyebrow: "Clean test set · N=82", evaluationTitle: "Independent test results", accuracy: "Accuracy", top3: "Top-3 hit rate",
    metricNote: "These figures come from a cleaned internal test set; they are not proof of production accuracy in retail conditions.", configurationEyebrow: "Model contract", configurationTitle: "Runtime configuration",
    version: "Model version", input: "Model input", resize: "Image transform", resizeValue: "Resize short side to 256, then centre-crop to 224", normalize: "Normalization", threshold: "Candidate threshold", runtime: "Runtime",
    limitationsEyebrow: "Read before use", limitationsTitle: "Known limitations", limitScope: "The model must choose among five known classes and will still suggest candidates for an unknown fish.",
    limitData: "The training set is small and is not mainly made of real retail ice-counter photos.", limitConfusion: "Kembung and Cencaru are the main confusion pair in the current test set.",
    limitLicence: "Some training-image licences still require commercial clearance. This release is an I1 proof of concept only.", footer: "CV I1 demo · Every result requires human confirmation",
  },
};

let locale = "zh";
let selectedFile = null;
let previewUrl = null;
let metadataPromise = null;
let metadata = null;
let sessionPromise = null;
let currentResult = null;

const text = (key) => messages[locale][key] ?? key;

export function applyLocale(nextLocale, root = document) {
  const active = nextLocale === "en" ? "en" : "zh";
  const catalog = messages[active];
  root.documentElement.lang = active === "en" ? "en" : "zh-CN";
  root.title = catalog.documentTitle;
  root.querySelectorAll("[data-i18n]").forEach((element) => {
    const translated = catalog[element.dataset.i18n];
    if (translated) element.textContent = translated;
  });
  root.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", catalog[element.dataset.i18nAriaLabel]);
  });
  root.querySelectorAll("[data-i18n-alt]").forEach((element) => {
    element.setAttribute("alt", catalog[element.dataset.i18nAlt]);
  });
  const toggle = root.querySelector("[data-locale-toggle]");
  if (toggle) {
    toggle.textContent = active === "zh" ? "English" : "中文";
    toggle.dataset.locale = active;
    toggle.setAttribute("aria-label", active === "zh" ? "Switch to English" : "切换为中文");
  }
  return active;
}

export function validateFile(file, maxBytes = FALLBACK_MAX_BYTES) {
  if (!ALLOWED_TYPES.has(file?.type)) return "invalidType";
  if (!Number.isFinite(file.size) || file.size > maxBytes) return "tooLarge";
  return null;
}

export function errorKeyForStage(stage) {
  return stage === "decode" ? "decodeFailed" : "inferenceFailed";
}

export function runtimeAssetBase(moduleUrl = import.meta.url) {
  return new URL("./vendor/", moduleUrl).href;
}

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function setPanel(statusKey, titleKey, copyKey, tone = "") {
  currentResult = null;
  const status = document.querySelector("#result-status");
  status.textContent = text(statusKey);
  status.className = `result-status ${tone}`.trim();
  document.querySelector("#result-message").textContent = text(titleKey);
  document.querySelector("#result-copy").textContent = text(copyKey);
  document.querySelector("#result-empty").hidden = false;
  document.querySelector("#prediction-list").hidden = true;
  document.querySelector("#confirmation-note").hidden = true;
}

function renderSpecies() {
  if (!metadata) return;
  const list = document.querySelector("#species-list");
  list.replaceChildren(...metadata.classes.map((item) => {
    const chip = document.createElement("span");
    chip.textContent = `${item.class_code} · ${locale === "en" ? item.display_name_en : item.label}`;
    return chip;
  }));
}

function renderResult(result) {
  currentResult = result;
  const low = result.status === "LOW_CONFIDENCE";
  const status = document.querySelector("#result-status");
  status.textContent = text(low ? "lowConfidence" : "candidates");
  status.className = `result-status ${low ? "is-warning" : "is-ready"}`;
  document.querySelector("#result-empty").hidden = true;
  const list = document.querySelector("#prediction-list");
  list.replaceChildren(...result.predictions.map((prediction) => {
    const article = document.createElement("article");
    article.className = "prediction";
    const primaryName = locale === "en" ? prediction.display_name_en : prediction.label;
    article.innerHTML = `<div class="prediction-head"><div><span class="prediction-rank">${text("rank")} ${prediction.rank}</span><h3></h3><em></em></div><strong class="confidence"></strong></div><progress max="1"></progress><dl class="prediction-meta"><dt>${text("classCode")}</dt><dd></dd><dt>${text("seafoodId")}</dt><dd></dd></dl>`;
    article.querySelector("h3").textContent = primaryName;
    article.querySelector("em").textContent = prediction.scientific_name;
    article.querySelector(".confidence").textContent = `${(prediction.confidence * 100).toFixed(1)}%`;
    article.querySelector("progress").value = prediction.confidence;
    const values = article.querySelectorAll("dd");
    values[0].textContent = prediction.class_code;
    values[1].textContent = prediction.seafood_item_id;
    return article;
  }));
  list.hidden = false;
  document.querySelector("#confirmation-note").hidden = false;
}

async function loadMetadata() {
  const [classMap, preprocessing, modelCard] = await Promise.all([
    fetchJson("./class_map.json"), fetchJson("./preprocessing.json"), fetchJson("./model_card.json"),
  ]);
  if (classMap.model_version !== modelCard.model_version || classMap.classes.length !== 5) {
    throw new Error("Model metadata mismatch");
  }
  metadata = { classes: classMap.classes, preprocessing, modelCard };
  document.querySelector("#config-version").textContent = modelCard.model_version;
  document.querySelector("#config-threshold").textContent = modelCard.confidence_threshold.toFixed(2);
  const testMetrics = modelCard.metrics.test;
  document.querySelector("#metric-accuracy").textContent = `${(testMetrics.accuracy * 100).toFixed(2)}%`;
  document.querySelector("#metric-f1").textContent = `${(testMetrics.macro_f1 * 100).toFixed(2)}%`;
  document.querySelector("#metric-top3").textContent = `${(testMetrics.top3_hit_rate * 100).toFixed(2)}%`;
  const state = document.querySelector("#model-state");
  state.textContent = text("modelReady");
  state.classList.add("is-ready");
  renderSpecies();
  return metadata;
}

async function ensureSession() {
  await metadataPromise;
  if (!globalThis.ort) throw new Error("ONNX Runtime did not load");
  if (!sessionPromise) {
    globalThis.ort.env.wasm.wasmPaths = runtimeAssetBase();
    globalThis.ort.env.wasm.numThreads = 1;
    globalThis.ort.env.wasm.proxy = false;
    sessionPromise = globalThis.ort.InferenceSession.create("./model.onnx", {
      executionProviders: ["wasm"], graphOptimizationLevel: "all",
    });
  }
  return sessionPromise;
}

async function decodeImage(file) {
  if (globalThis.createImageBitmap) {
    try { return await createImageBitmap(file, { imageOrientation: "from-image" }); }
    catch { return createImageBitmap(file); }
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } finally { URL.revokeObjectURL(url); }
}

function prepareTensor(source, config) {
  const width = source.width || source.naturalWidth;
  const height = source.height || source.naturalHeight;
  const resizedSize = computeResize(width, height, config.resize_shorter_side);
  const resized = document.createElement("canvas");
  resized.width = resizedSize.width;
  resized.height = resizedSize.height;
  const resizedContext = resized.getContext("2d", { alpha: false });
  resizedContext.imageSmoothingEnabled = true;
  resizedContext.imageSmoothingQuality = "high";
  resizedContext.drawImage(source, 0, 0, resized.width, resized.height);

  const size = config.image_size;
  const crop = document.createElement("canvas");
  crop.width = size;
  crop.height = size;
  const cropContext = crop.getContext("2d", { alpha: false, willReadFrequently: true });
  cropContext.drawImage(resized, -Math.round((resized.width - size) / 2), -Math.round((resized.height - size) / 2));
  return toNchw(cropContext.getImageData(0, 0, size, size), config.mean, config.std);
}

async function identify() {
  if (!selectedFile) return;
  const button = document.querySelector("#identify-button");
  button.disabled = true;
  button.textContent = text("analysing");
  document.querySelector("#error-message").hidden = true;
  setPanel("waiting", "loadingTitle", "loadingCopy");
  let source;
  let stage = "model";
  try {
    const session = await ensureSession();
    setPanel("waiting", "analysingTitle", "analysingCopy");
    stage = "decode";
    source = await decodeImage(selectedFile);
    stage = "inference";
    const tensorData = prepareTensor(source, metadata.preprocessing);
    const inputName = session.inputNames[0];
    const outputName = session.outputNames[0];
    const feeds = { [inputName]: new globalThis.ort.Tensor("float32", tensorData, [1, 3, 224, 224]) };
    const outputs = await session.run(feeds);
    renderResult(rankPredictions(outputs[outputName].data, metadata.classes, metadata.modelCard.confidence_threshold, 3));
  } catch (error) {
    console.error(error);
    const errorKey = errorKeyForStage(stage);
    const message = document.querySelector("#error-message");
    message.textContent = text(errorKey);
    message.hidden = false;
    setPanel("modelFailed", "lowTitle", errorKey, "is-error");
  } finally {
    source?.close?.();
    button.disabled = false;
    button.textContent = text("identify");
  }
}

function clearPhoto() {
  selectedFile = null;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  const input = document.querySelector("#photo-input");
  input.value = "";
  const preview = document.querySelector("#photo-preview");
  preview.removeAttribute("src");
  preview.hidden = true;
  document.querySelector("#preview-empty").hidden = false;
  document.querySelector("#preview-frame").classList.remove("has-image");
  document.querySelector("#file-name").textContent = text("noFile");
  document.querySelector("#identify-button").disabled = true;
  document.querySelector("#clear-button").hidden = true;
  document.querySelector("#error-message").hidden = true;
  setPanel("waiting", "resultEmptyTitle", "resultEmptyCopy");
}

function choosePhoto(file) {
  const errorKey = validateFile(file, metadata?.preprocessing.max_upload_bytes ?? FALLBACK_MAX_BYTES);
  if (errorKey) {
    clearPhoto();
    const message = document.querySelector("#error-message");
    message.textContent = text(errorKey);
    message.hidden = false;
    return;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  selectedFile = file;
  previewUrl = URL.createObjectURL(file);
  const preview = document.querySelector("#photo-preview");
  preview.src = previewUrl;
  preview.hidden = false;
  document.querySelector("#preview-empty").hidden = true;
  document.querySelector("#preview-frame").classList.add("has-image");
  document.querySelector("#file-name").textContent = file.name;
  document.querySelector("#identify-button").disabled = false;
  document.querySelector("#clear-button").hidden = false;
  document.querySelector("#error-message").hidden = true;
  setPanel("waiting", "resultEmptyTitle", "resultEmptyCopy");
}

function initialize() {
  try { locale = localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh"; } catch { locale = "zh"; }
  locale = applyLocale(locale);
  setPanel("waiting", "resultEmptyTitle", "resultEmptyCopy");
  metadataPromise = loadMetadata().catch((error) => {
    console.error(error);
    const state = document.querySelector("#model-state");
    state.textContent = text("modelFailed");
    state.classList.add("is-error");
    throw error;
  });
  document.querySelector("#photo-input").addEventListener("change", (event) => choosePhoto(event.target.files?.[0]));
  document.querySelector("#identify-button").addEventListener("click", identify);
  document.querySelector("#clear-button").addEventListener("click", clearPhoto);
  document.querySelector("[data-locale-toggle]").addEventListener("click", (event) => {
    locale = applyLocale(event.currentTarget.dataset.locale === "zh" ? "en" : "zh");
    try { localStorage.setItem(STORAGE_KEY, locale); } catch { /* Preference remains session-only. */ }
    renderSpecies();
    if (currentResult) renderResult(currentResult);
  });
  window.addEventListener("beforeunload", () => { if (previewUrl) URL.revokeObjectURL(previewUrl); });
}

if (typeof document !== "undefined" && document.querySelector("#cv-app")) initialize();
