import type { DecisionCode, RejectionReasonCode } from "../api/types";

export type Locale = "zh" | "en";

export const DECISIONS = ["APPROVED", "REJECTED", "UNSURE"] as const;
export const STATUSES = [
  "CANDIDATE",
  "CURRENT",
  "APPROVED",
  "REJECTED",
  "UNSURE",
  "ACTIVE",
  "INACTIVE",
  "PENDING",
  "COMPLETED",
] as const;
export const SOURCES = ["FISH_VISTA", "GBIF", "INATURALIST", "WIKIMEDIA_COMMONS"] as const;
export const REJECTION_REASONS = [
  "WRONG_SPECIES",
  "NOT_WHOLE_FISH",
  "COOKED_OR_PROCESSED",
  "TOO_OCCLUDED",
  "TOO_SMALL_OR_BLURRY",
  "DUPLICATE",
  "ARTWORK_OR_DIAGRAM",
  "LICENSE_OR_SOURCE_CONCERN",
  "IMAGE_URL_UNAVAILABLE",
  "OTHER",
] as const satisfies readonly RejectionReasonCode[];

const decisions = {
  zh: { APPROVED: "已保留", REJECTED: "已拒绝", UNSURE: "不确定" },
  en: { APPROVED: "Kept", REJECTED: "Rejected", UNSURE: "Unsure" },
} satisfies Record<Locale, Record<DecisionCode, string>>;

const statuses: Record<Locale, Record<(typeof STATUSES)[number], string>> = {
  zh: {
    CANDIDATE: "待审核",
    CURRENT: "正在查看",
    APPROVED: "已保留",
    REJECTED: "已拒绝",
    UNSURE: "不确定",
    ACTIVE: "启用",
    INACTIVE: "停用",
    PENDING: "处理中",
    COMPLETED: "已完成",
  },
  en: {
    CANDIDATE: "Awaiting review",
    CURRENT: "In review",
    APPROVED: "Kept",
    REJECTED: "Rejected",
    UNSURE: "Unsure",
    ACTIVE: "Active",
    INACTIVE: "Inactive",
    PENDING: "Pending",
    COMPLETED: "Completed",
  },
};

const sources: Record<Locale, Record<(typeof SOURCES)[number], string>> = {
  zh: {
    FISH_VISTA: "Fish-Vista 鱼类数据集",
    GBIF: "GBIF 全球生物多样性信息平台",
    INATURALIST: "iNaturalist",
    WIKIMEDIA_COMMONS: "维基共享资源",
  },
  en: {
    FISH_VISTA: "Fish-Vista dataset",
    GBIF: "GBIF occurrence",
    INATURALIST: "iNaturalist observation",
    WIKIMEDIA_COMMONS: "Wikimedia Commons",
  },
};

const reasons: Record<Locale, Record<RejectionReasonCode, string>> = {
  zh: {
    WRONG_SPECIES: "鱼种错误",
    NOT_WHOLE_FISH: "不是完整鱼体",
    COOKED_OR_PROCESSED: "已烹饪或加工",
    TOO_OCCLUDED: "遮挡过多",
    TOO_SMALL_OR_BLURRY: "目标太小或模糊",
    DUPLICATE: "重复图片",
    ARTWORK_OR_DIAGRAM: "插画或示意图",
    LICENSE_OR_SOURCE_CONCERN: "授权或来源存疑",
    IMAGE_URL_UNAVAILABLE: "图片链接失效",
    OTHER: "其他",
  },
  en: {
    WRONG_SPECIES: "Wrong species",
    NOT_WHOLE_FISH: "Not a whole fish",
    COOKED_OR_PROCESSED: "Cooked or processed",
    TOO_OCCLUDED: "Too occluded",
    TOO_SMALL_OR_BLURRY: "Too small or blurry",
    DUPLICATE: "Duplicate image",
    ARTWORK_OR_DIAGRAM: "Artwork or diagram",
    LICENSE_OR_SOURCE_CONCERN: "License or source concern",
    IMAGE_URL_UNAVAILABLE: "Image URL unavailable",
    OTHER: "Other",
  },
};

export const messages = {
  zh: {
    reviewTitle: "图片审核",
    loadingCurrent: "正在取得待审核图片…",
    emptyPool: "暂时没有待审核图片。稍后重试即可。",
    currentError: "无法载入当前图片，请检查网络后重试。",
    retryLoad: "重试载入",
    loadingImage: "正在加载图片",
    imageError: "图片未能加载。请选择下一步操作。",
    retryImage: "重新加载图片",
    openSource: "打开来源页面",
    openOriginal: "打开原图",
    imageUnavailable: "图片链接失效",
    keep: "保留 (K)",
    reject: "拒绝 (R)",
    unsure: "不确定 (U)",
    rejectionReason: "拒绝原因",
    confirmReject: "确认拒绝",
    cancelReject: "取消拒绝",
    chooseReason: "请选择拒绝原因。",
    otherNotes: "其他原因备注",
    otherNotesRequired: "请填写其他原因。",
    saving: "正在保存…",
    decisionAmbiguous: "保存结果无法确认。请重试；系统会使用同一个幂等键。",
    decisionRejected: "保存请求被明确拒绝。选择已保留；重试将使用新的幂等键。",
    retrySave: "重试保存",
    cancelRetry: "取消重试",
    assignmentConflict: "当前图片的分配已变化，已为你刷新当前项目。",
    sessionCompleted: "本次会话已完成",
    itemUnit: "张",
    source: "来源",
    creator: "创建者",
    license: "授权",
    attribution: "署名",
    location: "地点",
    observedOn: "观察日期",
    sourceRecord: "来源记录",
    filters: "筛选",
    speciesFilter: "鱼种筛选",
    sourceFilter: "来源筛选",
    decisionFilter: "结果筛选",
    dateFilter: "日期筛选",
    progress: "进度",
    history: "历史记录",
    completed: "已完成",
    remaining: "待审核",
  },
  en: {
    reviewTitle: "Image review",
    loadingCurrent: "Loading the next review item…",
    emptyPool: "No images are waiting right now. Try again later.",
    currentError: "Could not load the current image. Check your connection and retry.",
    retryLoad: "Retry loading",
    loadingImage: "Loading image",
    imageError: "The image could not be loaded. Choose a next action.",
    retryImage: "Reload image",
    openSource: "Open source page",
    openOriginal: "Open original",
    imageUnavailable: "Image URL unavailable",
    keep: "Keep (K)",
    reject: "Reject (R)",
    unsure: "Unsure (U)",
    rejectionReason: "Rejection reason",
    confirmReject: "Confirm rejection",
    cancelReject: "Cancel rejection",
    chooseReason: "Choose a rejection reason.",
    otherNotes: "Other reason notes",
    otherNotesRequired: "Describe the other reason.",
    saving: "Saving…",
    decisionAmbiguous: "The save could not be confirmed. Retry will use the same idempotency key.",
    decisionRejected: "The save was rejected. Your choice is preserved; retry will use a new idempotency key.",
    retrySave: "Retry save",
    cancelRetry: "Cancel retry",
    assignmentConflict: "This assignment changed, so the current item was refreshed.",
    sessionCompleted: "Completed this session",
    itemUnit: "items",
    source: "Source",
    creator: "Creator",
    license: "License",
    attribution: "Attribution",
    location: "Location",
    observedOn: "Observed",
    sourceRecord: "Source record",
    filters: "Filters",
    speciesFilter: "Species filter",
    sourceFilter: "Source filter",
    decisionFilter: "Decision filter",
    dateFilter: "Date filter",
    progress: "Progress",
    history: "History",
    completed: "Completed",
    remaining: "Remaining",
  },
} as const;

export type MessageKey = keyof (typeof messages)["zh"];

export function decisionLabel(locale: Locale, code: string): string {
  return knownLabel(decisions[locale], code, locale === "zh" ? "未知结果" : "Unknown decision");
}

export function statusLabel(locale: Locale, code: string): string {
  return knownLabel(statuses[locale], code, locale === "zh" ? "未知状态" : "Unknown status");
}

export function sourceLabel(locale: Locale, code: string): string {
  return knownLabel(sources[locale], code, locale === "zh" ? "未知来源" : "Unknown source");
}

export function rejectionReasonLabel(locale: Locale, code: string): string {
  return knownLabel(reasons[locale], code, locale === "zh" ? "未知拒绝原因" : "Unknown rejection reason");
}

function knownLabel(labels: Record<string, string>, code: string, fallback: string): string {
  if (Object.prototype.hasOwnProperty.call(labels, code)) return labels[code];
  const bounded = code.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 24);
  return bounded ? `${fallback} (${bounded})` : fallback;
}
