import { describe, expect, it } from "vitest";

import { IDS, candidateFixture, currentFixture, speciesFixture } from "../test/task12Fixtures";
import { parseCandidateReceipt, parseReceiptResponse, parseReviewReceipt, parseSpeciesReceipt } from "./types";

describe("Task 12 review runtime mutation contracts", () => {
  it("accepts pending batch IDs outside a partial upload but only accepts submitted successes", () => {
    const result = parseReceiptResponse({
      batch_id: IDS.batch,
      status: "pending",
      accepted_candidate_ids: [IDS.candidate],
      pending_candidate_ids: [IDS.species2],
    }, IDS.batch, new Map([[IDS.candidate, "SUCCEEDED"]]) as never);

    expect(result).toEqual({ accepted: 1, pending: 1 });
    expect(() => parseReceiptResponse({
      batch_id: IDS.batch,
      status: "pending",
      accepted_candidate_ids: [IDS.candidate],
      pending_candidate_ids: [IDS.species2],
    }, IDS.batch, new Map([[IDS.candidate, "FAILED"]]) as never)).toThrow();
  });

  it("enforces receipt response status coherence", () => {
    expect(() => parseReceiptResponse({
      batch_id: IDS.batch,
      status: "completed",
      accepted_candidate_ids: [IDS.candidate],
      pending_candidate_ids: [IDS.species2],
    }, IDS.batch, new Map([[IDS.candidate, "SUCCEEDED"]]) as never)).toThrow();
    expect(() => parseReceiptResponse({
      batch_id: IDS.batch,
      status: "pending",
      accepted_candidate_ids: [IDS.candidate],
      pending_candidate_ids: [],
    }, IDS.batch, new Map([[IDS.candidate, "SUCCEEDED"]]) as never)).toThrow();
  });

  it("rejects species create and edit receipts that disagree with submitted fields", () => {
    expect(() => parseSpeciesReceipt(
      { ...speciesFixture.items[0], id: IDS.batch, code: "SF003", candidate_count: 0 },
      { code: "SF003", submitted: { name_zh: "新鱼", name_en: "New fish", scientific_name: "Piscis novus", sort_order: 30, active: true }, create: true } as never,
    )).toThrow();
    expect(() => parseSpeciesReceipt(
      { ...speciesFixture.items[0], name_en: "unchanged" },
      { id: IDS.species1, submitted: { name_en: "Corrected fish" } } as never,
    )).toThrow();
  });

  it.each([
    ["release", { ...candidateFixture, version: 2, current_review: candidateFixture.current_review }],
    ["transfer", { ...candidateFixture, version: 2, current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true }, current_started_at: null, current_review: null }],
    ["reopen", { ...candidateFixture, version: 2, current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true }, current_started_at: null, current_review: null }],
    ["invalidation", { ...candidateFixture, species: { ...currentFixture.items[0].species, id: IDS.species2, code: "SF002" }, version: 2, current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true }, current_started_at: null, current_review: null }],
  ])("rejects malformed %s candidate operation receipts", (operation, receipt) => {
    expect(() => parseCandidateReceipt(receipt, {
      id: IDS.candidate,
      previousVersion: 1,
      operation,
      targetReviewerId: IDS.xinhui,
      speciesId: IDS.species2,
    } as never)).toThrow();
  });

  it("rejects an ordinary candidate receipt that omits an exact submitted change", () => {
    expect(() => parseCandidateReceipt({ ...candidateFixture, version: 2 }, {
      id: IDS.candidate,
      previousVersion: 1,
      operation: "patch",
      submitted: { preview_url: "https://images.example.test/new-preview.jpg" },
      previous: candidateFixture,
    } as never)).toThrow();
  });

  it("accepts an unreviewed candidate species patch while preserving assignment identity", () => {
    const previous = { ...candidateFixture, current_reviewer: null, current_started_at: null, current_review: null };
    const receipt = {
      ...previous,
      species: { ...currentFixture.items[0].species, id: IDS.species2, code: "SF002" },
      version: 2,
    };
    expect(parseCandidateReceipt(receipt, {
      id: IDS.candidate,
      previousVersion: 1,
      operation: "patch",
      submitted: { species_id: IDS.species2 },
      previous,
    } as never)).toEqual(receipt);
  });

  it("rejects review edit receipts with noncanonical derived facts", () => {
    expect(() => parseReviewReceipt({
      id: IDS.review,
      candidate_id: IDS.candidate,
      reviewer_id: IDS.hassan,
      decision: "REJECTED",
      rejection_reason: "DUPLICATE",
      notes: null,
      whole_fish: "YES",
      exact_species_verified: "YES",
      is_current: true,
      version: 2,
    }, {
      id: IDS.review,
      candidateId: IDS.candidate,
      reviewerId: IDS.hassan,
      previousVersion: 1,
      decision: "REJECTED",
      rejectionReason: "DUPLICATE",
      notes: null,
      wholeFish: "REVIEW",
      exactSpeciesVerified: "REVIEW",
    } as never)).toThrow();
  });
});
