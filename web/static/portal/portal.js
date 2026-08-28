(() => {
  const storageKey = "sukaseafood:portal-locale";
  const messages = {
    zh: {
      pageTitle: "SukaSeafood · 项目入口",
      brandTagline: "可信数据 · 更好选择",
      eyebrow: "可持续海鲜数据项目",
      heroTitle: "让海鲜数据真正可用",
      heroIntro: "把分散的可持续性、价格、供应、食谱与图片资料整理成一致、可验证的数据，帮助团队构建更清晰的海鲜选择体验。",
      openValidator: "打开 CSV Validator",
      openReview: "进入 Image Review",
      trustedCore: "可信数据核心",
      chipSustainability: "可持续性",
      chipPrice: "价格趋势",
      chipSupply: "供应地点",
      chipVision: "鱼类图像",
      toolsEyebrow: "项目工具",
      toolsTitle: "从这里开始",
      toolsIntro: "两个独立工具分别保护结构化数据质量和计算机视觉训练图片质量。",
      validatorDescription: "检查字段、类型、允许值和重复记录；下载模板并生成规范化 CSV。",
      reviewDescription: "协作审核候选鱼类图片，记录决定并维护计算机视觉训练数据质量。",
      launchTool: "打开工具",
      enterReview: "进入审核",
      workflowEyebrow: "数据工作流",
      workflowTitle: "让来源可追溯，让结果可复核",
      flowCollect: "收集公开来源",
      flowCollectDetail: "保留来源与快照信息",
      flowNormalize: "统一数据结构",
      flowNormalizeDetail: "对齐物种、地点与时间",
      flowValidate: "验证数据质量",
      flowValidateDetail: "发现缺失、格式与重复问题",
      flowReview: "人工复核图片",
      flowReviewDetail: "保留明确的审核决定",
      privacy: "CSV 文件只在你的浏览器中处理，不会上传。",
    },
    en: {
      pageTitle: "SukaSeafood · Project portal",
      brandTagline: "Trusted data · Better choices",
      eyebrow: "Sustainable seafood data project",
      heroTitle: "Make seafood data useful",
      heroIntro: "We bring sustainability, price, supply, recipe and image sources into consistent, verifiable data for clearer seafood choices.",
      openValidator: "Open CSV Validator",
      openReview: "Enter Image Review",
      trustedCore: "Trusted data core",
      chipSustainability: "Sustainability",
      chipPrice: "Price trends",
      chipSupply: "Landing points",
      chipVision: "Fish imagery",
      toolsEyebrow: "Project tools",
      toolsTitle: "Start here",
      toolsIntro: "Two focused tools protect structured-data quality and computer-vision training image quality.",
      validatorDescription: "Check columns, types, allowed values and duplicates; download templates and export normalized CSV files.",
      reviewDescription: "Review candidate fish images together, record decisions and maintain the quality of computer-vision training data.",
      launchTool: "Launch tool",
      enterReview: "Enter review",
      workflowEyebrow: "Data workflow",
      workflowTitle: "Traceable sources, reviewable results",
      flowCollect: "Collect public sources",
      flowCollectDetail: "Retain source and snapshot details",
      flowNormalize: "Normalize structures",
      flowNormalizeDetail: "Align species, place and time",
      flowValidate: "Validate data quality",
      flowValidateDetail: "Find missing, format and duplicate issues",
      flowReview: "Review imagery",
      flowReviewDetail: "Keep explicit human decisions",
      privacy: "CSV files are processed only in your browser and are never uploaded.",
    },
  };

  function storedLocale() {
    try {
      return localStorage.getItem(storageKey) === "en" ? "en" : "zh";
    } catch {
      return "zh";
    }
  }

  function applyLocale(locale, persist = false) {
    const activeLocale = locale === "en" ? "en" : "zh";
    const catalog = messages[activeLocale];
    document.documentElement.lang = activeLocale === "en" ? "en" : "zh-CN";
    document.title = catalog.pageTitle;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      const translation = catalog[element.dataset.i18n];
      if (translation) element.textContent = translation;
    });
    const toggle = document.querySelector("[data-locale-toggle]");
    if (toggle) {
      toggle.textContent = activeLocale === "zh" ? "English" : "中文";
      toggle.setAttribute(
        "aria-label",
        activeLocale === "zh" ? "Switch to English" : "切换为中文",
      );
      toggle.dataset.locale = activeLocale;
    }
    if (persist) {
      try {
        localStorage.setItem(storageKey, activeLocale);
      } catch {
        // The page remains usable when storage is disabled.
      }
    }
  }

  function initialize() {
    const toggle = document.querySelector("[data-locale-toggle]");
    applyLocale(storedLocale());
    toggle?.addEventListener("click", () => {
      applyLocale(toggle.dataset.locale === "zh" ? "en" : "zh", true);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
