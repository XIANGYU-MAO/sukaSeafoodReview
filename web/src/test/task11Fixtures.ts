import type { HistoryResponse, ProgressResponse } from "../api/types";

export const reviewerId = "8de1871b-677f-4ea8-8e11-1f4d49a88c86";

export const progressFixture: ProgressResponse = {
  total: 12,
  reviewed: 7,
  pending: 4,
  currently_open: 1,
  completion_percent: 58.33,
  decision_counts: { APPROVED: 4, REJECTED: 2, UNSURE: 1 },
  today_count: 3,
  members: [
    { name: "Hassan", completed: 2, approved: 1, rejected: 1, unsure: 0, today: 1 },
    { name: "Mao", completed: 1, approved: 1, rejected: 0, unsure: 0, today: 0 },
    { name: "Xinhui", completed: 1, approved: 0, rejected: 0, unsure: 1, today: 1 },
    { name: "Wahid", completed: 1, approved: 1, rejected: 0, unsure: 0, today: 0 },
    { name: "Sharmaa", completed: 1, approved: 0, rejected: 1, unsure: 0, today: 1 },
    { name: "Yiming", completed: 1, approved: 1, rejected: 0, unsure: 0, today: 0 },
  ],
};

export const historyItem = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  candidate_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  reviewer_id: reviewerId,
  decision: "REJECTED",
  rejection_reason: "WRONG_SPECIES",
  notes: null,
  whole_fish: "REVIEW",
  exact_species_verified: "NO",
  is_current: true,
  read_only: false,
  version: 3,
  created_at: "2026-08-25T10:00:00Z",
  updated_at: "2026-08-26T11:30:00Z",
  species: {
    code: "SF001",
    name_zh: "测试鱼",
    name_en: "Test fish",
    scientific_name: "Piscis probatio",
  },
  source_dataset: "WIKIMEDIA_COMMONS",
  source_record_id: "page:1:File:Fish.jpg",
  preview_url: "https://upload.example.test/thumb/fish.jpg",
  original_url: "https://upload.example.test/fish.jpg",
  source_url: "https://commons.example.test/wiki/Fish",
} as const;

export const historyFixture: HistoryResponse = {
  total: 1,
  items: [historyItem],
  filters: {
    species: [historyItem.species],
    sources: ["WIKIMEDIA_COMMONS"],
  },
};
