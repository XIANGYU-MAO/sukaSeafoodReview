import { describe, expect, it } from "vitest";

import { IDS, candidateFixture, candidatesFixture, currentFixture, exportBatch, reviewsFixture, speciesFixture } from "../test/task12Fixtures";
import { parseAdminReviewList, parseCandidateList, parseCandidateReceipt, parseCurrentList, parseExportCreate, parseReceiptResponse, parseReviewReceipt, parseSpeciesList, parseSpeciesReceipt } from "./types";

describe("Task 12 review runtime mutation contracts", () => {
  it("accepts API-shaped six-field species summaries in candidates, current work, and reviews", () => {
    const expected = { id: IDS.species1, code: "SF001", name_zh: "测试鱼", name_en: "Test fish", scientific_name: "Piscis probatio", active: true };
    expect(parseCandidateList(candidatesFixture).items[0].species).toEqual(expected);
    expect(parseCurrentList(currentFixture).items[0].species).toEqual(expected);
    expect(parseAdminReviewList(reviewsFixture).items[0].species).toEqual(expected);
  });

  it("requires every bounded nullable source override in species responses", () => {
    const species = {
      ...speciesFixture.items[0],
      inat_taxon_id: 123,
      gbif_taxon_key: 456,
      commons_category: "Category:Test fish",
      fish_vista_filter: "Test fish",
    };
    expect(parseSpeciesList({ total: 1, items: [species] }).items[0]).toMatchObject(species);
    expect(() => parseSpeciesList({ total: 1, items: [{ ...species, inat_taxon_id: 0 }] })).toThrow("鱼种响应无效");
    const missing = { ...species } as Record<string, unknown>;
    delete missing.commons_category;
    expect(() => parseSpeciesList({ total: 1, items: [missing] })).toThrow("鱼种响应无效");
  });
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

  it("requires an exact classification for every submitted receipt item", () => {
    const second = "30000000-0000-4000-8000-000000000002";
    const unsent = "30000000-0000-4000-8000-000000000003";
    const response = (accepted: string[], pending: string[]) => ({
      batch_id: IDS.batch,
      status: "pending",
      accepted_candidate_ids: accepted,
      pending_candidate_ids: pending,
    });

    expect(() => parseReceiptResponse(
      response([IDS.candidate], [second]),
      IDS.batch,
      new Map([[IDS.candidate, "SUCCEEDED"], [second, "SUCCEEDED"]]) as never,
    )).toThrow();
    expect(() => parseReceiptResponse(
      response([], [IDS.candidate]),
      IDS.batch,
      new Map([[IDS.candidate, "SUCCEEDED"]]) as never,
    )).toThrow();
    expect(() => parseReceiptResponse(
      response([IDS.candidate], [unsent]),
      IDS.batch,
      new Map([[IDS.candidate, "FAILED"]]) as never,
    )).toThrow();
    expect(() => parseReceiptResponse(
      response([], [unsent]),
      IDS.batch,
      new Map([[IDS.candidate, "FAILED"]]) as never,
    )).toThrow();

    expect(parseReceiptResponse(
      response([IDS.candidate], [second, unsent]),
      IDS.batch,
      new Map([[IDS.candidate, "SUCCEEDED"], [second, "FAILED"]]) as never,
    )).toEqual({ accepted: 1, pending: 2 });
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

  it("rejects an otherwise valid species edit receipt whose immutable code changed", () => {
    expect(() => parseSpeciesReceipt(
      { ...speciesFixture.items[0], code: "SF999", name_en: "Corrected fish" },
      { id: IDS.species1, code: "SF001", submitted: { name_en: "Corrected fish" } } as never,
    )).toThrow();
  });

  it.each([
    ["SF001", { ...exportBatch, species_code: "SF002" }],
    [null, { ...exportBatch, species_code: "SF001" }],
  ])("rejects an export batch outside requested scope %s", (scope, response) => {
    expect(() => parseExportCreate(response, scope as never)).toThrow();
  });

  it("keeps NO_WORK explicitly bound to the requested export scope", () => {
    expect(parseExportCreate({ code: "NO_WORK", created: false, batch: null }, "SF001" as never)).toEqual({
      kind: "no-work",
      scope: "SF001",
    });
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

  it("accepts canonical facts when an image is rejected because it is not a fish", () => {
    expect(() => parseReviewReceipt({
      id: IDS.review,
      candidate_id: IDS.candidate,
      reviewer_id: IDS.hassan,
      decision: "REJECTED",
      rejection_reason: "NOT_A_FISH",
      notes: null,
      whole_fish: "NO",
      exact_species_verified: "NO",
      is_current: true,
      version: 2,
    }, {
      id: IDS.review,
      candidateId: IDS.candidate,
      reviewerId: IDS.hassan,
      previousVersion: 1,
      decision: "REJECTED",
      rejectionReason: "NOT_A_FISH",
      notes: null,
    })).not.toThrow();
  });
});
