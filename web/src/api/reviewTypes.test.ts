import { describe, expect, it } from "vitest";

import {
  parseCandidateResponse,
  parseReviewResponse,
  type DecisionPayload,
} from "./types";

const candidate = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  species: {
    code: "SF001",
    name_zh: "测试鱼",
    name_en: "Test fish",
    scientific_name: "Piscis probatio",
  },
  source_dataset: "INATURALIST",
  source_record_id: "obs:1/photo:10",
  preview_url: "https://images.example.test/preview.jpg",
  original_url: "https://images.example.test/original.jpg",
  source_url: "https://source.example.test/record/1",
  creator: "Ada",
  license: "CC-BY-NC",
  license_url: "https://creativecommons.org/licenses/by-nc/4.0/",
  attribution: "Ada / iNaturalist",
  location: "South China Sea",
  observed_on: "2026-08-13",
  metadata: { source_observation_quality: "research" },
};

const payload: DecisionPayload = {
  decision: "REJECTED",
  rejection_reason: "DUPLICATE",
  notes: null,
};

const review = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  candidate_id: candidate.id,
  reviewer_id: "8de1871b-677f-4ea8-8e11-1f4d49a88c86",
  decision: "REJECTED",
  rejection_reason: "DUPLICATE",
  notes: null,
  whole_fish: "REVIEW",
  exact_species_verified: "REVIEW",
  is_current: true,
  version: 1,
};

describe("review wire parsing", () => {
  it("accepts the complete real FastAPI candidate shape without rewriting safe values", () => {
    expect(parseCandidateResponse(candidate)).toEqual(candidate);
  });

  it.each([
    ["non-object", null],
    ["missing used field", { ...candidate, attribution: undefined }],
    ["malformed id", { ...candidate, id: "not-a-uuid" }],
    ["HTTP preview", { ...candidate, preview_url: "http://images.example.test/fish.jpg" }],
    ["credentialed source URL", { ...candidate, source_url: "https://user:pass@example.test/record" }],
    ["script URL", { ...candidate, original_url: "javascript:alert(1)" }],
    ["malformed date", { ...candidate, observed_on: "2026-02-31" }],
    ["array metadata", { ...candidate, metadata: [] }],
    ["oversized text", { ...candidate, attribution: "x".repeat(1_025) }],
  ])("rejects a candidate with %s before rendering it", (_label, value) => {
    expect(() => parseCandidateResponse(value)).toThrow("Invalid candidate response");
  });

  it("accepts a decision receipt only when IDs and submitted semantics match", () => {
    expect(
      parseReviewResponse(review, {
        candidateId: candidate.id,
        reviewerId: review.reviewer_id,
        payload,
      }),
    ).toEqual(review);
  });

  it.each([
    ["wrong candidate", { ...review, candidate_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" }],
    ["wrong reviewer", { ...review, reviewer_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" }],
    ["wrong decision", { ...review, decision: "UNSURE", rejection_reason: null }],
    ["wrong reason", { ...review, rejection_reason: "WRONG_SPECIES" }],
    ["not current", { ...review, is_current: false }],
    ["zero version", { ...review, version: 0 }],
    ["unknown fact code", { ...review, whole_fish: "MAYBE" }],
  ])("rejects a successful decision receipt with %s", (_label, value) => {
    expect(() =>
      parseReviewResponse(value, {
        candidateId: candidate.id,
        reviewerId: review.reviewer_id,
        payload,
      }),
    ).toThrow("Invalid review response");
  });
});
