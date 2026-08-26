export const FIXED_NAMES = [
  "Hassan",
  "Mao",
  "Xinhui",
  "Wahid",
  "Sharmaa",
  "Yiming",
] as const;

export type FixedName = (typeof FIXED_NAMES)[number];
export type UserRole = "reviewer" | "admin";

export interface LoginName {
  name: FixedName;
}

export interface AuthState {
  id: string;
  name: FixedName;
  role: UserRole;
  must_change_password: boolean;
  csrf_token: string;
}

export interface LoginPayload {
  name: string;
  password: string;
}

export interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}

export const DECISION_CODES = ["APPROVED", "REJECTED", "UNSURE"] as const;
export type DecisionCode = (typeof DECISION_CODES)[number];

export const REJECTION_REASON_CODES = [
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
] as const;
export type RejectionReasonCode = (typeof REJECTION_REASON_CODES)[number];

export interface DecisionPayload {
  decision: DecisionCode;
  rejection_reason: RejectionReasonCode | null;
  notes: string | null;
}

export interface SpeciesSummary {
  code: string;
  name_zh: string;
  name_en: string;
  scientific_name: string;
}

export interface CandidateResponse {
  id: string;
  species: SpeciesSummary;
  source_dataset: string;
  source_record_id: string;
  preview_url: string;
  original_url: string;
  source_url: string;
  creator: string | null;
  license: string;
  license_url: string | null;
  attribution: string;
  location: string | null;
  observed_on: string | null;
  metadata: Record<string, unknown>;
}

export interface ReviewResponse {
  id: string;
  candidate_id: string;
  reviewer_id: string;
  decision: DecisionCode;
  rejection_reason: RejectionReasonCode | null;
  notes: string | null;
  whole_fish: "YES" | "NO" | "REVIEW";
  exact_species_verified: "YES" | "NO" | "REVIEW";
  is_current: true;
  version: number;
}

export interface DecisionCounts {
  APPROVED: number;
  REJECTED: number;
  UNSURE: number;
}

export interface MemberProgress {
  name: FixedName;
  completed: number;
  approved: number;
  rejected: number;
  unsure: number;
  today: number;
}

export interface ProgressResponse {
  total: number;
  reviewed: number;
  pending: number;
  currently_open: number;
  completion_percent: number;
  decision_counts: DecisionCounts;
  today_count: number;
  members: MemberProgress[];
}

export interface HistoryItem {
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
  species: SpeciesSummary;
  source_dataset: string;
  source_record_id: string;
  preview_url: string;
  original_url: string;
  source_url: string;
}

export interface HistoryFacets {
  species: SpeciesSummary[];
  sources: string[];
}

export interface HistoryResponse {
  total: number;
  items: HistoryItem[];
  filters: HistoryFacets;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIXED_NAME_SET = new Set<string>(FIXED_NAMES);
const DECISION_SET = new Set<string>(DECISION_CODES);
const REJECTION_REASON_SET = new Set<string>(REJECTION_REASON_CODES);
const REVIEW_FACT_SET = new Set(["YES", "NO", "REVIEW"]);

export function parseAuthState(value: unknown): AuthState {
  if (!isRecord(value)) {
    throw new Error("Invalid authentication response");
  }
  const { id, name, role, must_change_password: mustChangePassword, csrf_token: csrfToken } = value;
  if (
    typeof id !== "string" ||
    !UUID_PATTERN.test(id) ||
    !isFixedName(name) ||
    (role !== "reviewer" && role !== "admin") ||
    role !== expectedRole(name) ||
    typeof mustChangePassword !== "boolean" ||
    typeof csrfToken !== "string" ||
    !csrfToken.trim()
  ) {
    throw new Error("Invalid authentication response");
  }
  return {
    id,
    name,
    role,
    must_change_password: mustChangePassword,
    csrf_token: csrfToken,
  };
}

export function parseLoginNames(value: unknown): readonly FixedName[] {
  if (!Array.isArray(value) || value.length !== FIXED_NAMES.length) {
    throw new Error("Invalid fixed-name response");
  }
  const names = value.map((entry) => {
    if (!isRecord(entry) || !isFixedName(entry.name)) {
      throw new Error("Invalid fixed-name response");
    }
    return entry.name;
  });
  if (new Set(names).size !== FIXED_NAMES.length) {
    throw new Error("Invalid fixed-name response");
  }
  return FIXED_NAMES;
}

export function parseCandidateResponse(value: unknown): CandidateResponse {
  try {
    if (!isRecord(value) || !isRecord(value.species)) throw new Error();
    const species = value.species;
    const candidate: CandidateResponse = {
      id: requiredUuid(value.id),
      species: {
        code: requiredText(species.code, 32),
        name_zh: requiredText(species.name_zh, 255),
        name_en: requiredText(species.name_en, 255),
        scientific_name: requiredText(species.scientific_name, 255),
      },
      source_dataset: requiredText(value.source_dataset, 128),
      source_record_id: requiredText(value.source_record_id, 255),
      preview_url: requiredHttpsUrl(value.preview_url),
      original_url: requiredHttpsUrl(value.original_url),
      source_url: requiredHttpsUrl(value.source_url),
      creator: optionalText(value.creator, 512),
      license: requiredText(value.license, 255),
      license_url: optionalHttpsUrl(value.license_url),
      attribution: requiredText(value.attribution, 1_024),
      location: optionalText(value.location, 512),
      observed_on: optionalDate(value.observed_on),
      metadata: plainRecord(value.metadata),
    };
    return candidate;
  } catch {
    throw new Error("Invalid candidate response");
  }
}

interface ExpectedReview {
  candidateId: string;
  reviewerId: string;
  payload: DecisionPayload;
}

export function parseReviewResponse(value: unknown, expected: ExpectedReview): ReviewResponse {
  try {
    const response = parseReviewWire(value);
    if (
      response.candidate_id !== expected.candidateId ||
      response.reviewer_id !== expected.reviewerId ||
      response.decision !== expected.payload.decision ||
      response.rejection_reason !== expected.payload.rejection_reason ||
      response.notes !== expected.payload.notes
    ) {
      throw new Error();
    }
    return response;
  } catch {
    throw new Error("Invalid review response");
  }
}

export function parseLatestReviewResponse(
  value: unknown,
  expected: { reviewId: string; candidateId: string; reviewerId: string },
): ReviewResponse {
  try {
    const response = parseReviewWire(value);
    if (
      response.id !== expected.reviewId ||
      response.candidate_id !== expected.candidateId ||
      response.reviewer_id !== expected.reviewerId
    ) {
      throw new Error();
    }
    return response;
  } catch {
    throw new Error("Invalid review response");
  }
}

export function parseProgressResponse(value: unknown): ProgressResponse {
  try {
    if (!isRecord(value) || !hasExactKeys(value, [
      "total", "reviewed", "pending", "currently_open", "completion_percent",
      "decision_counts", "today_count", "members",
    ])) throw new Error();
    if (!isRecord(value.decision_counts) || !hasExactKeys(value.decision_counts, DECISION_CODES)) {
      throw new Error();
    }
    if (!Array.isArray(value.members) || value.members.length !== FIXED_NAMES.length) throw new Error();
    const members = value.members.map((entry, index): MemberProgress => {
      if (!isRecord(entry) || !hasExactKeys(entry, ["name", "completed", "approved", "rejected", "unsure", "today"])) {
        throw new Error();
      }
      if (entry.name !== FIXED_NAMES[index]) throw new Error();
      const member = {
        name: entry.name,
        completed: nonnegativeInteger(entry.completed),
        approved: nonnegativeInteger(entry.approved),
        rejected: nonnegativeInteger(entry.rejected),
        unsure: nonnegativeInteger(entry.unsure),
        today: nonnegativeInteger(entry.today),
      } as MemberProgress;
      if (
        member.completed !== member.approved + member.rejected + member.unsure ||
        member.today > member.completed
      ) throw new Error();
      return member;
    });
    const total = nonnegativeInteger(value.total);
    const reviewed = nonnegativeInteger(value.reviewed);
    const pending = nonnegativeInteger(value.pending);
    const currentlyOpen = nonnegativeInteger(value.currently_open);
    const decisionCounts = {
      APPROVED: nonnegativeInteger(value.decision_counts.APPROVED),
      REJECTED: nonnegativeInteger(value.decision_counts.REJECTED),
      UNSURE: nonnegativeInteger(value.decision_counts.UNSURE),
    };
    const percent = finiteRange(value.completion_percent, 0, 100);
    if (
      total !== reviewed + pending + currentlyOpen ||
      reviewed !== decisionCounts.APPROVED + decisionCounts.REJECTED + decisionCounts.UNSURE
    ) throw new Error();
    return {
      total,
      reviewed,
      pending,
      currently_open: currentlyOpen,
      completion_percent: percent,
      decision_counts: decisionCounts,
      today_count: nonnegativeInteger(value.today_count),
      members,
    };
  } catch {
    throw new Error("Invalid progress response");
  }
}

export function parseHistoryResponse(value: unknown, reviewerId: string): HistoryResponse {
  try {
    requiredUuid(reviewerId);
    if (!isRecord(value) || !hasExactKeys(value, ["total", "items", "filters"])) throw new Error();
    const total = nonnegativeInteger(value.total);
    if (!Array.isArray(value.items) || value.items.length > 100 || value.items.length > total) throw new Error();
    const items = value.items.map((entry) => parseHistoryItem(entry, reviewerId));
    if (new Set(items.map((item) => item.id)).size !== items.length) throw new Error();
    if (!isRecord(value.filters) || !hasExactKeys(value.filters, ["species", "sources"])) throw new Error();
    if (!Array.isArray(value.filters.species) || value.filters.species.length > 5_000) throw new Error();
    const species = value.filters.species.map(parseSpeciesSummary);
    if (new Set(species.map((item) => item.code)).size !== species.length) throw new Error();
    if (!Array.isArray(value.filters.sources) || value.filters.sources.length > 256) throw new Error();
    const sources = value.filters.sources.map((source) => requiredText(source, 128));
    if (new Set(sources).size !== sources.length) throw new Error();
    return { total, items, filters: { species, sources } };
  } catch {
    throw new Error("Invalid history response");
  }
}

function parseHistoryItem(value: unknown, reviewerId: string): HistoryItem {
  if (!isRecord(value) || !hasExactKeys(value, [
    "id", "candidate_id", "reviewer_id", "decision", "rejection_reason", "notes",
    "whole_fish", "exact_species_verified", "is_current", "read_only", "version",
    "created_at", "updated_at", "species", "source_dataset", "source_record_id",
    "preview_url", "original_url", "source_url",
  ])) throw new Error();
  const decision = knownDecision(value.decision);
  const rejectionReason = knownReasonOrNull(value.rejection_reason);
  const notes = optionalText(value.notes, 2_000);
  if (decision === "REJECTED") {
    if (rejectionReason === null || (rejectionReason === "OTHER" && notes === null)) throw new Error();
  } else if (rejectionReason !== null) {
    throw new Error();
  }
  if (typeof value.is_current !== "boolean" || typeof value.read_only !== "boolean") throw new Error();
  if (value.read_only !== !value.is_current) throw new Error();
  const ownedReviewer = requiredUuid(value.reviewer_id);
  if (ownedReviewer !== reviewerId) throw new Error();
  return {
    id: requiredUuid(value.id),
    candidate_id: requiredUuid(value.candidate_id),
    reviewer_id: ownedReviewer,
    decision,
    rejection_reason: rejectionReason,
    notes,
    whole_fish: reviewFact(value.whole_fish),
    exact_species_verified: reviewFact(value.exact_species_verified),
    is_current: value.is_current,
    read_only: value.read_only,
    version: positiveInteger(value.version),
    created_at: requiredTimestamp(value.created_at),
    updated_at: requiredTimestamp(value.updated_at),
    species: parseSpeciesSummary(value.species),
    source_dataset: requiredText(value.source_dataset, 128),
    source_record_id: requiredText(value.source_record_id, 255),
    preview_url: requiredHttpsUrl(value.preview_url),
    original_url: requiredHttpsUrl(value.original_url),
    source_url: requiredHttpsUrl(value.source_url),
  };
}

function parseReviewWire(value: unknown): ReviewResponse {
  if (!isRecord(value)) throw new Error();
  const decision = knownDecision(value.decision);
  const rejectionReason = knownReasonOrNull(value.rejection_reason);
  const notes = optionalText(value.notes, 2_000);
  if (decision === "REJECTED") {
    if (rejectionReason === null || (rejectionReason === "OTHER" && notes === null)) throw new Error();
  } else if (rejectionReason !== null) {
    throw new Error();
  }
  return {
    id: requiredUuid(value.id),
    candidate_id: requiredUuid(value.candidate_id),
    reviewer_id: requiredUuid(value.reviewer_id),
    decision,
    rejection_reason: rejectionReason,
    notes,
    whole_fish: reviewFact(value.whole_fish),
    exact_species_verified: reviewFact(value.exact_species_verified),
    is_current: value.is_current === true ? true : (() => { throw new Error(); })(),
    version: positiveInteger(value.version),
  };
}

function parseSpeciesSummary(value: unknown): SpeciesSummary {
  if (!isRecord(value) || !hasExactKeys(value, ["code", "name_zh", "name_en", "scientific_name"])) {
    throw new Error();
  }
  return {
    code: requiredText(value.code, 32),
    name_zh: requiredText(value.name_zh, 255),
    name_en: requiredText(value.name_en, 255),
    scientific_name: requiredText(value.scientific_name, 255),
  };
}

function isFixedName(value: unknown): value is FixedName {
  return typeof value === "string" && FIXED_NAME_SET.has(value);
}

function expectedRole(name: FixedName): UserRole {
  return name === "Mao" ? "admin" : "reviewer";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredUuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) throw new Error();
  return value;
}

function requiredText(value: unknown, maximum: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > maximum ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) throw new Error();
  return value;
}

function optionalText(value: unknown, maximum: number): string | null {
  if (value === null) return null;
  return requiredText(value, maximum);
}

function requiredHttpsUrl(value: unknown): string {
  const text = requiredText(value, 2_048);
  const parsed = new URL(text);
  if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) {
    throw new Error();
  }
  return text;
}

function optionalHttpsUrl(value: unknown): string | null {
  return value === null ? null : requiredHttpsUrl(value);
}

function optionalDate(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error();
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    throw new Error();
  }
  return value;
}

function plainRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error();
  return value;
}

function knownDecision(value: unknown): DecisionCode {
  if (typeof value !== "string" || !DECISION_SET.has(value)) throw new Error();
  return value as DecisionCode;
}

function knownReasonOrNull(value: unknown): RejectionReasonCode | null {
  if (value === null) return null;
  if (typeof value !== "string" || !REJECTION_REASON_SET.has(value)) throw new Error();
  return value as RejectionReasonCode;
}

function reviewFact(value: unknown): "YES" | "NO" | "REVIEW" {
  if (typeof value !== "string" || !REVIEW_FACT_SET.has(value)) throw new Error();
  return value as "YES" | "NO" | "REVIEW";
}

function nonnegativeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) throw new Error();
  return Number(value);
}

function positiveInteger(value: unknown): number {
  const parsed = nonnegativeInteger(value);
  if (parsed < 1) throw new Error();
  return parsed;
}

function finiteRange(value: unknown, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error();
  }
  return value;
}

function requiredTimestamp(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 64 ||
    !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) throw new Error();
  optionalDate(value.slice(0, 10));
  return value;
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
