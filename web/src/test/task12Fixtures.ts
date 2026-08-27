import { FIXED_NAMES } from "../api/types";

export const IDS = {
  mao: "10000000-0000-4000-8000-000000000001",
  hassan: "10000000-0000-4000-8000-000000000002",
  xinhui: "10000000-0000-4000-8000-000000000003",
  wahid: "10000000-0000-4000-8000-000000000004",
  sharmaa: "10000000-0000-4000-8000-000000000005",
  yiming: "10000000-0000-4000-8000-000000000006",
  species1: "20000000-0000-4000-8000-000000000001",
  species2: "20000000-0000-4000-8000-000000000002",
  candidate: "30000000-0000-4000-8000-000000000001",
  review: "40000000-0000-4000-8000-000000000001",
  batch: "50000000-0000-4000-8000-000000000001",
} as const;

const userIds = [IDS.hassan, IDS.mao, IDS.xinhui, IDS.wahid, IDS.sharmaa, IDS.yiming];

export const maoAuth = {
  id: IDS.mao,
  name: "Mao",
  role: "admin",
  must_change_password: false,
  csrf_token: "mao-csrf-token",
} as const;

export const usersFixture = {
  total: 6,
  items: FIXED_NAMES.map((display_name, index) => ({
    id: userIds[index],
    display_name,
    role: display_name === "Mao" ? "admin" : "reviewer",
    active: true,
  })),
};

export const speciesItems = [
  {
    id: IDS.species1,
    code: "SF001",
    name_zh: "测试鱼",
    name_en: "Test fish",
    scientific_name: "Piscis probatio",
    inat_taxon_id: null,
    gbif_taxon_key: null,
    commons_category: null,
    fish_vista_filter: null,
    active: true,
    sort_order: 10,
    candidate_count: 2,
  },
  {
    id: IDS.species2,
    code: "SF002",
    name_zh: "其他鱼",
    name_en: "Other fish",
    scientific_name: "Piscis alter",
    inat_taxon_id: null,
    gbif_taxon_key: null,
    commons_category: null,
    fish_vista_filter: null,
    active: true,
    sort_order: 20,
    candidate_count: 1,
  },
];

export const speciesFixture = { total: 2, items: speciesItems };
export const sourcesFixture = { sources: ["GBIF", "INATURALIST", "WIKIMEDIA_COMMONS"] };

function speciesSummary(item: typeof speciesItems[number]) {
  return { id: item.id, code: item.code, name_zh: item.name_zh, name_en: item.name_en, scientific_name: item.scientific_name, active: item.active };
}

export const progressFixture = {
  total: 3,
  reviewed: 1,
  pending: 1,
  currently_open: 1,
  completion_percent: 33.33,
  decision_counts: { APPROVED: 1, REJECTED: 0, UNSURE: 0 },
  today_count: 1,
  members: FIXED_NAMES.map((name) => ({
    name,
    completed: name === "Hassan" ? 1 : 0,
    approved: name === "Hassan" ? 1 : 0,
    rejected: 0,
    unsure: 0,
    today: name === "Hassan" ? 1 : 0,
  })),
};

export const candidateSummary = {
  id: IDS.candidate,
  source_dataset: "INATURALIST",
  source_record_id: "obs:1/photo:10",
  preview_url: "https://images.example.test/preview.jpg",
  original_url: "https://images.example.test/original.jpg",
  source_url: "https://source.example.test/record/1",
  active: true,
  version: 1,
};

export const currentFixture = {
  total: 1,
  items: [{
    candidate: candidateSummary,
    species: speciesSummary(speciesItems[0]),
    reviewer: { id: IDS.hassan, display_name: "Hassan", active: true },
    current_started_at: "2026-08-26T02:00:00Z",
  }],
};
export const candidateFixture = {
  ...candidateSummary,
  species: { ...currentFixture.items[0].species },
  creator: "Ada",
  license: "CC-BY-4.0",
  license_url: "https://creativecommons.org/licenses/by/4.0/",
  attribution: "Ada / CC-BY-4.0",
  location: "Ningbo",
  observed_on: "2026-08-20",
  metadata: { catalog_number: "one" },
  current_started_at: null,
  current_reviewer: null,
  current_review: {
    id: IDS.review,
    decision: "APPROVED",
    rejection_reason: null,
    notes: null,
    is_current: true,
    version: 1,
    reviewer: { id: IDS.hassan, display_name: "Hassan", active: true },
  },
};

export const candidatesFixture = { total: 1, items: [candidateFixture] };

export const reviewItem = {
  id: IDS.review,
  candidate_id: IDS.candidate,
  reviewer_id: IDS.hassan,
  decision: "APPROVED",
  rejection_reason: null,
  notes: null,
  whole_fish: "YES",
  exact_species_verified: "YES",
  is_current: true,
  read_only: false,
  version: 1,
  created_at: "2026-08-25T02:00:00Z",
  updated_at: "2026-08-25T02:00:00Z",
  candidate: candidateSummary,
  species: { ...currentFixture.items[0].species },
  reviewer: { id: IDS.hassan, display_name: "Hassan", active: true },
};

export const reviewsFixture = { total: 1, items: [reviewItem] };

export const importPreviewFixture = {
  total: 4,
  new_rows: 2,
  exact_duplicates: 1,
  url_duplicates: 1,
  invalid_species: 0,
  missing_urls: 0,
  invalid_licenses: 0,
  invalid_sources: 0,
  conflicting_identities: 0,
  parse_errors: 0,
  warnings: 2,
  source_counts: { INATURALIST: 2, GBIF: 2 },
  species_counts: { SF001: 3, SF002: 1 },
  blocking_errors: 0,
  can_commit: true,
  file_sha256: "a".repeat(64),
  issues: [
    { row: 3, related_row: 2, code: "EXACT_DUPLICATE", message: "duplicate", blocking: false, host: null },
    { row: 4, related_row: 2, code: "DUPLICATE_IMAGE_URL", message: "duplicate URL", blocking: false, host: null },
  ],
  issue_groups: [
    { code: "EXACT_DUPLICATE", message: "duplicate", blocking: false, host: null, count: 1, sample_rows: [3], sample_related_rows: [2], omitted_rows: 0 },
    { code: "DUPLICATE_IMAGE_URL", message: "duplicate URL", blocking: false, host: null, count: 1, sample_rows: [4], sample_related_rows: [2], omitted_rows: 0 },
  ],
  issues_truncated: false,
  omitted_issue_details: 0,
  preview_token: "p".repeat(43),
};

export const exportBatch = {
  id: IDS.batch,
  species_code: "SF001",
  status: "pending",
  created_at: "2026-08-26T02:00:00Z",
  expires_at: "2026-09-02T02:00:00Z",
  completed_at: null,
  expired_at: null,
  item_count: 2,
  pending_count: 2,
  created: false,
};

export const exportsFixture = { total: 1, items: [exportBatch] };

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function defaultAdminResponse(url: string): Response {
  if (url.endsWith("/auth/me")) return jsonResponse(maoAuth);
  if (url.endsWith("/progress")) return jsonResponse(progressFixture);
  if (url.includes("/admin/current")) return jsonResponse(currentFixture);
  if (url.includes("/admin/users")) return jsonResponse(usersFixture);
  if (url.includes("/admin/sources")) return jsonResponse(sourcesFixture);
  if (url.includes("/admin/species")) return jsonResponse(speciesFixture);
  if (url.includes("/admin/candidates")) return jsonResponse(candidatesFixture);
  if (url.includes("/admin/reviews")) return jsonResponse(reviewsFixture);
  if (url.endsWith("/admin/exports/pending-counts")) return jsonResponse({ SF001: 2, SF002: 0 });
  if (url.endsWith("/admin/exports") || url.includes("/admin/exports?")) return jsonResponse(exportsFixture);
  throw new Error(`Unexpected request: ${url}`);
}
