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
    if (!isRecord(value)) throw new Error();
    const decision = knownDecision(value.decision);
    const rejectionReason = knownReasonOrNull(value.rejection_reason);
    const notes = optionalText(value.notes, 2_000);
    const response: ReviewResponse = {
      id: requiredUuid(value.id),
      candidate_id: requiredUuid(value.candidate_id),
      reviewer_id: requiredUuid(value.reviewer_id),
      decision,
      rejection_reason: rejectionReason,
      notes,
      whole_fish: reviewFact(value.whole_fish),
      exact_species_verified: reviewFact(value.exact_species_verified),
      is_current: value.is_current === true ? true : (() => { throw new Error(); })(),
      version:
        Number.isSafeInteger(value.version) && Number(value.version) > 0
          ? Number(value.version)
          : (() => { throw new Error(); })(),
    };
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
  if (typeof value !== "string" || !value.trim() || value.length > maximum) throw new Error();
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
