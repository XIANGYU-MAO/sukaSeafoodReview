import {
  DECISION_CODES,
  FIXED_NAMES,
  REJECTION_REASON_CODES,
  type DecisionCode,
  type FixedName,
  type ProgressResponse,
  type RejectionReasonCode,
  parseProgressResponse,
} from "../api/types";

export interface AdminUser {
  id: string;
  display_name: FixedName;
  role: "reviewer" | "admin";
  active: boolean;
}

export interface AdminUserList { total: number; items: AdminUser[] }
export interface AdminSourceList { sources: string[] }

export interface AdminSpeciesSummary {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  scientific_name: string;
  active: boolean;
}

export interface AdminSpecies extends AdminSpeciesSummary {
  inat_taxon_id: number | null;
  gbif_taxon_key: number | null;
  commons_category: string | null;
  fish_vista_filter: string | null;
  sort_order?: number;
  candidate_count?: number;
  source_counts?: Record<string, number>;
}

export interface AdminSpeciesList { total: number; items: Required<AdminSpecies>[] }

export interface AdminUserSummary { id: string; display_name: FixedName; active: boolean }
export interface AdminCandidateSummary {
  id: string;
  source_dataset: string;
  source_record_id: string;
  preview_url: string;
  original_url: string;
  source_url: string;
  active: boolean;
  version: number;
}

export interface AdminReviewSummary {
  id: string;
  decision: DecisionCode;
  rejection_reason: RejectionReasonCode | null;
  notes: string | null;
  is_current: boolean;
  version: number;
  reviewer: AdminUserSummary;
}

export interface AdminCandidate extends AdminCandidateSummary {
  species: AdminSpeciesSummary;
  creator: string | null;
  license: string;
  license_url: string | null;
  attribution: string;
  location: string | null;
  observed_on: string | null;
  metadata: Record<string, unknown>;
  current_started_at: string | null;
  current_reviewer: AdminUserSummary | null;
  current_review: AdminReviewSummary | null;
}

export interface AdminCandidateList { total: number; items: AdminCandidate[] }
export interface CurrentItem {
  candidate: AdminCandidateSummary;
  species: AdminSpeciesSummary;
  reviewer: AdminUserSummary;
  current_started_at: string;
}
export interface CurrentList { total: number; items: CurrentItem[] }

export interface AdminReviewItem {
  id: string;
  candidate_id: string;
  reviewer_id: string;
  decision: DecisionCode;
  rejection_reason: RejectionReasonCode | null;
  notes: string | null;
  whole_fish: "YES" | "NO" | "REVIEW";
  exact_species_verified: "YES" | "NO" | "REVIEW";
  is_current: boolean;
  read_only: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  candidate: AdminCandidateSummary;
  species: AdminSpeciesSummary;
  reviewer: AdminUserSummary;
}
export interface AdminReviewList { total: number; items: AdminReviewItem[] }

export interface ImportIssue { row: number | null; related_row: number | null; code: string; message: string; blocking: boolean; host: string | null }
export interface ImportIssueGroup { code: string; message: string; blocking: boolean; host: string | null; count: number; sample_rows: number[]; sample_related_rows: (number | null)[]; omitted_rows: number }
export interface ImportPreview {
  total: number; new_rows: number; exact_duplicates: number; url_duplicates: number;
  invalid_species: number; missing_urls: number; invalid_licenses: number; invalid_sources: number;
  conflicting_identities: number; parse_errors: number; warnings: number;
  source_counts: Record<string, number>; species_counts: Record<string, number>;
  blocking_errors: number; can_commit: boolean; file_sha256: string; issues: ImportIssue[]; issue_groups: ImportIssueGroup[];
  issues_truncated: boolean; omitted_issue_details: number; preview_token: string | null;
}
export interface ImportResult {
  total: number; inserted: number; skipped_exact: number; skipped_url_duplicates: number; skipped_blocking: number; file_sha256: string;
}

export interface ExportBatch {
  id: string; species_code: string | null; status: "pending" | "completed" | "expired";
  created_at: string; expires_at: string; completed_at: string | null; expired_at: string | null;
  item_count: number; pending_count: number; created: boolean;
}
export interface ExportBatchList { total: number; items: ExportBatch[] }
export type ExportCreateResult =
  | { kind: "no-work"; scope: string | null }
  | { kind: "batch"; scope: string | null; batch: ExportBatch };

export interface ReceiptUploadItem {
  candidate_id: string; review_id: string; review_version: number;
  status: "SUCCEEDED" | "FAILED"; sha256?: string | null; relative_path?: string | null; error?: string | null;
}
export interface ReceiptUpload { batch_id: string; items: ReceiptUploadItem[] }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SAFE_CODE = /^[A-Z][A-Z0-9_-]{0,31}$/;
const RESERVED = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/;
const DECISIONS = new Set<string>(DECISION_CODES);
const REASONS = new Set<string>(REJECTION_REASON_CODES);
const NAMES = new Set<string>(FIXED_NAMES);
const FACTS = new Set(["YES", "NO", "REVIEW"]);

export function isSafeSpeciesCode(value: string): boolean {
  return SAFE_CODE.test(value) && !RESERVED.test(value);
}

export function parseAdminUsers(value: unknown): AdminUserList {
  try {
    const root = object(value);
    exact(root, ["total", "items"]);
    const items = array(root.items, 6).map((entry, index) => {
      const item = object(entry); exact(item, ["id", "display_name", "active", "role"]);
      const name = fixedName(item.display_name);
      if (name !== FIXED_NAMES[index]) fail();
      const role = item.role as "admin" | "reviewer";
      if (role !== "admin" && role !== "reviewer") fail();
      if (role !== (name === "Mao" ? "admin" : "reviewer")) fail();
      return { id: uuid(item.id), display_name: name, active: bool(item.active), role };
    });
    if (integer(root.total) !== 6 || items.length !== 6 || new Set(items.map((item) => item.id)).size !== 6) fail();
    return { total: 6, items };
  } catch { throw new Error("管理员账号响应无效"); }
}

export function parseAdminSources(value: unknown): AdminSourceList {
  try {
    const root = object(value); exact(root, ["sources"]);
    const sources = array(root.sources, 1_000).map((value) => text(value, 128));
    if (new Set(sources).size !== sources.length || sources.some((value, index) => index > 0 && sources[index - 1].localeCompare(value, undefined, { sensitivity: "base" }) > 0)) fail();
    return { sources };
  } catch { throw new Error("来源目录响应无效"); }
}

export function parseSpeciesList(value: unknown): AdminSpeciesList {
  try {
    const root = listRoot(value);
    return { total: root.total, items: root.items.map(parseSpeciesFull) };
  } catch { throw new Error("鱼种响应无效"); }
}

export function parseSpeciesReceipt(value: unknown, expected: { id?: string; code?: string; submitted: Partial<Pick<Required<AdminSpecies>, "name_zh" | "name_en" | "scientific_name" | "inat_taxon_id" | "gbif_taxon_key" | "commons_category" | "fish_vista_filter" | "sort_order" | "active">>; create?: boolean }): Required<AdminSpecies> {
  try {
    const item = parseSpeciesFull(value);
    if ((expected.id && item.id !== expected.id) || (expected.code && item.code !== expected.code) || (expected.create && item.candidate_count !== 0)) fail();
    for (const key of ["name_zh", "name_en", "scientific_name", "inat_taxon_id", "gbif_taxon_key", "commons_category", "fish_vista_filter", "sort_order", "active"] as const) if (key in expected.submitted && item[key] !== expected.submitted[key]) fail();
    return item;
  } catch { throw new Error("鱼种操作结果无效"); }
}

export function parseCandidateList(value: unknown): AdminCandidateList {
  try { const root = listRoot(value); return { total: root.total, items: root.items.map(parseCandidate) }; }
  catch { throw new Error("候选图片响应无效"); }
}

export function parseCurrentList(value: unknown): CurrentList {
  try {
    const root = listRoot(value);
    return { total: root.total, items: root.items.map((entry) => {
      const item = object(entry); exact(item, ["candidate", "species", "reviewer", "current_started_at"]);
      return { candidate: parseCandidateSummary(item.candidate), species: parseSpeciesSummary(item.species), reviewer: parseUserSummary(item.reviewer), current_started_at: timestamp(item.current_started_at) };
    }) };
  } catch { throw new Error("当前图片响应无效"); }
}

export function parseCandidateReceipt(
  value: unknown,
  expected: {
    id: string; previousVersion: number;
    operation: "release" | "transfer" | "reopen" | "invalidation" | "patch";
    targetReviewerId?: string; speciesId?: string;
    submitted?: Partial<Pick<AdminCandidate, "preview_url" | "original_url" | "active">> & { species_id?: string };
    previous?: AdminCandidate;
  },
): AdminCandidate {
  try {
    const item = parseCandidate(value);
    if (item.id !== expected.id || expected.previousVersion >= Number.MAX_SAFE_INTEGER || item.version !== expected.previousVersion + 1) fail();
    if (expected.operation === "release" && (item.current_reviewer !== null || item.current_started_at !== null || item.current_review !== null)) fail();
    if (["transfer", "reopen", "invalidation"].includes(expected.operation) && (item.current_reviewer?.id !== expected.targetReviewerId || item.current_started_at === null || item.current_review !== null)) fail();
    if (expected.operation === "invalidation" && item.species.id !== expected.speciesId) fail();
    if (expected.submitted) for (const key of ["preview_url", "original_url", "active"] as const) if (key in expected.submitted && item[key] !== expected.submitted[key]) fail();
    if (expected.submitted?.species_id !== undefined && item.species.id !== expected.submitted.species_id) fail();
    if (expected.operation === "patch" && expected.previous) {
      if (expected.submitted?.species_id === undefined && item.species.id !== expected.previous.species.id) fail();
      if (item.current_reviewer?.id !== expected.previous.current_reviewer?.id || item.current_started_at !== expected.previous.current_started_at || item.current_review?.id !== expected.previous.current_review?.id || item.current_review?.version !== expected.previous.current_review?.version) fail();
    }
    return item;
  } catch { throw new Error("候选操作结果无效"); }
}

export function parseAdminReviewList(value: unknown): AdminReviewList {
  try {
    const root = listRoot(value);
    return { total: root.total, items: root.items.map((entry) => {
      const item = object(entry);
      exact(item, ["id", "candidate_id", "reviewer_id", "decision", "rejection_reason", "notes", "whole_fish", "exact_species_verified", "is_current", "read_only", "version", "created_at", "updated_at", "candidate", "species", "reviewer"]);
      const isCurrent = bool(item.is_current); const readOnly = bool(item.read_only); if (readOnly === isCurrent) fail();
      const reviewer = parseUserSummary(item.reviewer); const reviewerId = uuid(item.reviewer_id); if (reviewer.id !== reviewerId) fail();
      return {
        id: uuid(item.id), candidate_id: uuid(item.candidate_id), reviewer_id: reviewerId,
        decision: decision(item.decision), rejection_reason: reason(item.rejection_reason), notes: optionalText(item.notes, 2_000),
        whole_fish: fact(item.whole_fish), exact_species_verified: fact(item.exact_species_verified), is_current: isCurrent, read_only: readOnly,
        version: positive(item.version), created_at: timestamp(item.created_at), updated_at: timestamp(item.updated_at),
        candidate: parseCandidateSummary(item.candidate), species: parseSpeciesSummary(item.species), reviewer,
      };
    }) };
  } catch { throw new Error("审核历史响应无效"); }
}

export function parseReviewReceipt(value: unknown, expected: { id: string; candidateId: string; reviewerId: string; previousVersion: number; decision: DecisionCode; rejectionReason: RejectionReasonCode | null; notes: string | null; wholeFish?: "YES" | "NO" | "REVIEW"; exactSpeciesVerified?: "YES" | "NO" | "REVIEW" }): void {
  try {
    const item = object(value);
    exact(item, ["id", "candidate_id", "reviewer_id", "decision", "rejection_reason", "notes", "whole_fish", "exact_species_verified", "is_current", "version"]);
    const parsed = {
      id: uuid(item.id), candidateId: uuid(item.candidate_id), reviewerId: uuid(item.reviewer_id),
      decision: decision(item.decision), rejectionReason: reason(item.rejection_reason), notes: optionalText(item.notes, 2_000),
      whole: fact(item.whole_fish), exactSpecies: fact(item.exact_species_verified), current: bool(item.is_current), version: positive(item.version),
    };
    const facts = canonicalFacts(expected.decision, expected.rejectionReason);
    if (!parsed.current || parsed.id !== expected.id || parsed.candidateId !== expected.candidateId || parsed.reviewerId !== expected.reviewerId || parsed.version !== expected.previousVersion + 1 || parsed.decision !== expected.decision || parsed.rejectionReason !== expected.rejectionReason || parsed.notes !== expected.notes || parsed.whole !== (expected.wholeFish ?? facts.whole) || parsed.exactSpecies !== (expected.exactSpeciesVerified ?? facts.exact)) fail();
  } catch { throw new Error("审核操作结果无效"); }
}

export function parseProgress(value: unknown): ProgressResponse { return parseProgressResponse(value); }

export function parseImportPreview(value: unknown): ImportPreview {
  try {
    const root = object(value);
    const countKeys = ["total", "new_rows", "exact_duplicates", "url_duplicates", "invalid_species", "missing_urls", "invalid_licenses", "invalid_sources", "conflicting_identities", "parse_errors", "warnings", "blocking_errors", "omitted_issue_details"] as const;
    const counts = Object.fromEntries(countKeys.map((key) => [key, integer(root[key])])) as unknown as Pick<ImportPreview, typeof countKeys[number]>;
    const issues = array(root.issues, 100).map((entry) => { const item = object(entry); exact(item, ["row", "related_row", "code", "message", "blocking", "host"]); return { row: item.row === null ? null : positive(item.row), related_row: item.related_row === null ? null : positive(item.related_row), code: enumCode(item.code), message: text(item.message, 500), blocking: bool(item.blocking), host: item.host === null ? null : text(item.host, 253) }; });
    const issueGroups = array(root.issue_groups, 100).map((entry) => {
      const item = object(entry); exact(item, ["code", "message", "blocking", "host", "count", "sample_rows", "sample_related_rows", "omitted_rows"]);
      const sampleRows = array(item.sample_rows, 10).map(positive);
      const sampleRelatedRows = array(item.sample_related_rows, 10).map((row) => row === null ? null : positive(row));
      if (sampleRelatedRows.length !== sampleRows.length) fail();
      return { code: enumCode(item.code), message: text(item.message, 500), blocking: bool(item.blocking), host: item.host === null ? null : text(item.host, 253), count: positive(item.count), sample_rows: sampleRows, sample_related_rows: sampleRelatedRows, omitted_rows: integer(item.omitted_rows) };
    });
    const token = root.preview_token === null ? null : text(root.preview_token, 512, 32);
    const preview = {
      ...counts, source_counts: countMap(root.source_counts), species_counts: countMap(root.species_counts), can_commit: bool(root.can_commit),
      file_sha256: sha(root.file_sha256), issues, issue_groups: issueGroups, issues_truncated: bool(root.issues_truncated), preview_token: token,
    } as ImportPreview;
    if (preview.can_commit && !preview.preview_token) fail();
    if (preview.issues_truncated !== (preview.omitted_issue_details > 0)) fail();
    return preview;
  } catch { throw new Error("导入预检查响应无效"); }
}

export function parseImportResult(value: unknown, expected: ImportPreview, skipBlockingRows = false): ImportResult {
  try {
    const root = object(value); exact(root, ["total", "inserted", "skipped_exact", "skipped_url_duplicates", "skipped_blocking", "file_sha256"]);
    const result = { total: integer(root.total), inserted: integer(root.inserted), skipped_exact: integer(root.skipped_exact), skipped_url_duplicates: integer(root.skipped_url_duplicates), skipped_blocking: integer(root.skipped_blocking), file_sha256: sha(root.file_sha256) };
    if (result.total !== expected.total || result.inserted !== expected.new_rows || result.skipped_exact !== expected.exact_duplicates || result.skipped_url_duplicates !== expected.url_duplicates || result.skipped_blocking !== (skipBlockingRows ? expected.blocking_errors : 0) || result.file_sha256 !== expected.file_sha256) fail();
    return result;
  } catch { throw new Error("导入结果无效"); }
}

export function parsePendingCounts(value: unknown, species: Required<AdminSpecies>[]): Record<string, number> {
  try {
    const root = object(value); const expected = species.filter((item) => item.active).map((item) => item.code).sort();
    if (Object.keys(root).sort().join("|") !== expected.join("|")) fail();
    return Object.fromEntries(expected.map((code) => [code, integer(root[code])]));
  } catch { throw new Error("待同步数量响应无效"); }
}

export function parseExportBatches(value: unknown): ExportBatchList {
  try { const root = listRoot(value); return { total: root.total, items: root.items.map(parseExportBatch) }; }
  catch { throw new Error("同步批次响应无效"); }
}

export function parseExportCreate(value: unknown, expectedScope: string | null): ExportCreateResult {
  try {
    const scope = expectedScope === null ? null : safeCode(expectedScope);
    const root = object(value);
    if (root.code === "NO_WORK") {
      exact(root, ["code", "created", "batch"]); if (root.created !== false || root.batch !== null) fail(); return { kind: "no-work", scope };
    }
    const batch = parseExportBatch(root); if (batch.species_code !== scope) fail();
    return { kind: "batch", scope, batch };
  } catch { throw new Error("创建同步批次结果无效"); }
}

export function parseReceiptFile(value: unknown, expectedBatchId: string, allowedCandidates?: Set<string>): ReceiptUpload {
  try {
    const root = object(value); exact(root, ["batch_id", "items"]); const batchId = uuid(root.batch_id); if (batchId !== expectedBatchId) fail();
    const items = array(root.items, 10_000, 1).map((entry): ReceiptUploadItem => {
      const item = object(entry); const keys = Object.keys(item); const allowed = new Set(["candidate_id", "review_id", "review_version", "status", "sha256", "relative_path", "error"]);
      if (keys.some((key) => !allowed.has(key)) || !["candidate_id", "review_id", "review_version", "status"].every((key) => key in item)) fail();
      const candidateId = uuid(item.candidate_id); if (allowedCandidates && !allowedCandidates.has(candidateId)) fail();
      const status: "SUCCEEDED" | "FAILED" = item.status === "SUCCEEDED" ? "SUCCEEDED" : item.status === "FAILED" ? "FAILED" : fail();
      const base = { candidate_id: candidateId, review_id: uuid(item.review_id), review_version: positive(item.review_version), status };
      if (status === "SUCCEEDED") {
        if (item.error !== undefined && item.error !== null) fail();
        return { ...base, sha256: sha(item.sha256), relative_path: safePath(item.relative_path), ...(item.error === null ? { error: null } : {}) };
      }
      if ((item.sha256 !== undefined && item.sha256 !== null) || (item.relative_path !== undefined && item.relative_path !== null)) fail();
      return { ...base, ...(item.sha256 === null ? { sha256: null } : {}), ...(item.relative_path === null ? { relative_path: null } : {}), error: text(item.error, 2_000) };
    });
    return { batch_id: batchId, items };
  } catch { throw new Error("回执 JSON 无效或批次不匹配"); }
}

export function parseReceiptResponse(value: unknown, batchId: string, submitted: Map<string, "SUCCEEDED" | "FAILED">): { accepted: number; pending: number } {
  try {
    const root = object(value); exact(root, ["batch_id", "status", "accepted_candidate_ids", "pending_candidate_ids"]);
    if (uuid(root.batch_id) !== batchId || !["pending", "completed"].includes(String(root.status))) fail();
    const accepted = array(root.accepted_candidate_ids, 10_000).map(uuid); const pending = array(root.pending_candidate_ids, 10_000).map(uuid);
    const acceptedSet = new Set(accepted); const pendingSet = new Set(pending);
    const succeeded = [...submitted].filter(([, status]) => status === "SUCCEEDED").map(([id]) => id);
    const failed = [...submitted].filter(([, status]) => status === "FAILED").map(([id]) => id);
    if (accepted.some((id) => submitted.get(id) !== "SUCCEEDED") || succeeded.some((id) => !acceptedSet.has(id)) || accepted.length !== succeeded.length) fail();
    if (failed.some((id) => !pendingSet.has(id)) || new Set([...accepted, ...pending]).size !== accepted.length + pending.length) fail();
    if ((root.status === "completed" && pending.length !== 0) || (root.status === "pending" && pending.length === 0)) fail();
    return { accepted: accepted.length, pending: pending.length };
  } catch { throw new Error("回执处理结果无效"); }
}

export function parseTemporaryPassword(value: unknown): string {
  try { const root = object(value); exact(root, ["temporary_password"]); return text(root.temporary_password, 1_024, 20); }
  catch { throw new Error("临时密码响应无效"); }
}

export function conflictCode(errorBody: unknown): string | null {
  try { const root = object(errorBody); const detail = object(root.detail); return enumCode(detail.code); } catch { return null; }
}

export function parseExportConflict(errorBody: unknown): { code: "EXPORT_SCOPE_OVERLAP" | "EXPORT_BATCH_EXPIRED" | "UNSAFE_SPECIES_CODE"; overlapCount: number } | null {
  try {
    const root = object(errorBody); const detail = object(root.detail); const code = enumCode(detail.code);
    if (code !== "EXPORT_SCOPE_OVERLAP" && code !== "EXPORT_BATCH_EXPIRED" && code !== "UNSAFE_SPECIES_CODE") return null;
    const ids = detail.batch_ids === undefined ? [] : array(detail.batch_ids, 100).map(uuid);
    if (new Set(ids).size !== ids.length || (code === "EXPORT_SCOPE_OVERLAP" && ids.length === 0)) fail();
    return { code, overlapCount: ids.length };
  } catch { return null; }
}

function parseSpeciesFull(value: unknown): Required<AdminSpecies> {
  const item = object(value); exact(item, ["id", "code", "name_zh", "name_en", "scientific_name", "inat_taxon_id", "gbif_taxon_key", "commons_category", "fish_vista_filter", "active", "sort_order", "candidate_count", "source_counts"]);
  const summary = Object.fromEntries(["id", "code", "name_zh", "name_en", "scientific_name", "active"].map((key) => [key, item[key]]));
  return { ...parseSpeciesSummary(summary), inat_taxon_id: item.inat_taxon_id === null ? null : positive(item.inat_taxon_id), gbif_taxon_key: item.gbif_taxon_key === null ? null : positive(item.gbif_taxon_key), commons_category: optionalText(item.commons_category, 512), fish_vista_filter: optionalText(item.fish_vista_filter, 255), sort_order: signedInteger(item.sort_order), candidate_count: integer(item.candidate_count), source_counts: countMap(item.source_counts) };
}
function parseSpeciesSummary(value: unknown): AdminSpeciesSummary {
  const item = object(value); exact(item, ["id", "code", "name_zh", "name_en", "scientific_name", "active"]);
  const code = text(item.code, 32); if (!isSafeSpeciesCode(code)) fail();
  return { id: uuid(item.id), code, name_zh: text(item.name_zh, 255), name_en: text(item.name_en, 255), scientific_name: text(item.scientific_name, 255), active: bool(item.active) };
}
function parseUserSummary(value: unknown): AdminUserSummary { const item = object(value); exact(item, ["id", "display_name", "active"]); return { id: uuid(item.id), display_name: fixedName(item.display_name), active: bool(item.active) }; }
function parseCandidateSummary(value: unknown): AdminCandidateSummary {
  const item = object(value); exact(item, ["id", "source_dataset", "source_record_id", "preview_url", "original_url", "source_url", "active", "version"]);
  return { id: uuid(item.id), source_dataset: text(item.source_dataset, 128), source_record_id: text(item.source_record_id, 255), preview_url: https(item.preview_url), original_url: https(item.original_url), source_url: https(item.source_url), active: bool(item.active), version: positive(item.version) };
}
function parseCandidate(value: unknown): AdminCandidate {
  const item = object(value); exact(item, ["id", "species", "source_dataset", "source_record_id", "preview_url", "original_url", "source_url", "creator", "license", "license_url", "attribution", "location", "observed_on", "metadata", "active", "version", "current_started_at", "current_reviewer", "current_review"]);
  const base = parseCandidateSummary(Object.fromEntries(["id", "source_dataset", "source_record_id", "preview_url", "original_url", "source_url", "active", "version"].map((key) => [key, item[key]])));
  const currentReviewer = item.current_reviewer === null ? null : parseUserSummary(item.current_reviewer);
  let currentReview: AdminReviewSummary | null = null;
  if (item.current_review !== null) {
    const review = object(item.current_review); exact(review, ["id", "decision", "rejection_reason", "notes", "is_current", "version", "reviewer"]);
    currentReview = { id: uuid(review.id), decision: decision(review.decision), rejection_reason: reason(review.rejection_reason), notes: optionalText(review.notes, 2_000), is_current: bool(review.is_current), version: positive(review.version), reviewer: parseUserSummary(review.reviewer) };
  }
  return { ...base, species: parseSpeciesSummary(item.species), creator: optionalText(item.creator, 512), license: text(item.license, 255), license_url: item.license_url === null ? null : https(item.license_url), attribution: text(item.attribution, 1_024), location: optionalText(item.location, 512), observed_on: item.observed_on === null ? null : date(item.observed_on), metadata: object(item.metadata), current_started_at: item.current_started_at === null ? null : timestamp(item.current_started_at), current_reviewer: currentReviewer, current_review: currentReview };
}
function parseExportBatch(value: unknown): ExportBatch {
  const root = object(value); exact(root, ["id", "species_code", "status", "created_at", "expires_at", "completed_at", "expired_at", "item_count", "pending_count", "created"]);
  const status = root.status; if (status !== "pending" && status !== "completed" && status !== "expired") fail();
  const itemCount = integer(root.item_count); const pendingCount = integer(root.pending_count); if (pendingCount > itemCount) fail();
  return { id: uuid(root.id), species_code: root.species_code === null ? null : safeCode(root.species_code), status, created_at: timestamp(root.created_at), expires_at: timestamp(root.expires_at), completed_at: root.completed_at === null ? null : timestamp(root.completed_at), expired_at: root.expired_at === null ? null : timestamp(root.expired_at), item_count: itemCount, pending_count: pendingCount, created: bool(root.created) };
}
function listRoot(value: unknown): { total: number; items: unknown[] } { const root = object(value); exact(root, ["total", "items"]); const total = integer(root.total); const items = array(root.items, 100); if (items.length > total) fail(); return { total, items }; }
function object(value: unknown): Record<string, unknown> { if (typeof value !== "object" || value === null || Array.isArray(value)) fail(); return value as Record<string, unknown>; }
function exact(value: Record<string, unknown>, keys: readonly string[]): void { const actual = Object.keys(value).sort(); const expected = [...keys].sort(); if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(); }
function array(value: unknown, max: number, min = 0): unknown[] { if (!Array.isArray(value) || value.length < min || value.length > max) fail(); return value; }
function text(value: unknown, max: number, min = 1): string { if (typeof value !== "string" || value.length < min || value.length > max || !value.trim() || /[\u0000-\u001f\u007f]/.test(value)) fail(); return value; }
function optionalText(value: unknown, max: number): string | null { return value === null ? null : text(value, max); }
function uuid(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) fail(); return value; }
function integer(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 0) fail(); return Number(value); }
function signedInteger(value: unknown): number { if (!Number.isSafeInteger(value)) fail(); return Number(value); }
function positive(value: unknown): number { const parsed = integer(value); if (parsed < 1) fail(); return parsed; }
function bool(value: unknown): boolean { if (typeof value !== "boolean") fail(); return value; }
function fixedName(value: unknown): FixedName { if (typeof value !== "string" || !NAMES.has(value)) fail(); return value as FixedName; }
function decision(value: unknown): DecisionCode { if (typeof value !== "string" || !DECISIONS.has(value)) fail(); return value as DecisionCode; }
function reason(value: unknown): RejectionReasonCode | null { if (value === null) return null; if (typeof value !== "string" || !REASONS.has(value)) fail(); return value as RejectionReasonCode; }
function fact(value: unknown): "YES" | "NO" | "REVIEW" { if (typeof value !== "string" || !FACTS.has(value)) fail(); return value as "YES" | "NO" | "REVIEW"; }
function https(value: unknown): string { const raw = text(value, 2_048); const parsed = new URL(raw); if (raw !== raw.trim() || /\s/.test(raw) || parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) fail(); return raw; }
function timestamp(value: unknown): string { const raw = text(value, 64); const year = Number(raw.slice(0, 4)); if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(raw) || year < 1 || year > 9998 || !Number.isFinite(Date.parse(raw))) fail(); return raw; }
function date(value: unknown): string { const raw = text(value, 10); const parsed = new Date(`${raw}T00:00:00Z`); if (!/^\d{4}-\d{2}-\d{2}$/.test(raw) || parsed.toISOString().slice(0, 10) !== raw) fail(); return raw; }
function sha(value: unknown): string { if (typeof value !== "string" || !/^[0-9a-f]{64}$/i.test(value)) fail(); return value.toLowerCase(); }
function safeCode(value: unknown): string { const raw = text(value, 32); if (!isSafeSpeciesCode(raw)) fail(); return raw; }
function enumCode(value: unknown): string { if (typeof value !== "string" || !/^[A-Z][A-Z0-9_]{0,63}$/.test(value)) fail(); return value; }
function countMap(value: unknown): Record<string, number> { const root = object(value); if (Object.keys(root).length > 1_000) fail(); return Object.fromEntries(Object.entries(root).map(([key, count]) => [text(key, 128), integer(count)])); }
function safePath(value: unknown): string { const raw = text(value, 1_024); if (raw.includes("\\") || raw.startsWith("/") || raw.split("/").some((part) => !part || part === "." || part === "..")) fail(); return raw; }
function fail(): never { throw new Error("invalid wire response"); }

function canonicalFacts(decisionValue: DecisionCode, rejection: RejectionReasonCode | null): { whole: "YES" | "NO" | "REVIEW"; exact: "YES" | "NO" | "REVIEW" } {
  if (decisionValue === "APPROVED") return { whole: "YES", exact: "YES" };
  if (decisionValue === "REJECTED" && rejection === "WRONG_SPECIES") return { whole: "REVIEW", exact: "NO" };
  if (decisionValue === "REJECTED" && rejection === "NOT_WHOLE_FISH") return { whole: "NO", exact: "REVIEW" };
  if (decisionValue === "REJECTED" && rejection === "NOT_A_FISH") return { whole: "NO", exact: "NO" };
  return { whole: "REVIEW", exact: "REVIEW" };
}
