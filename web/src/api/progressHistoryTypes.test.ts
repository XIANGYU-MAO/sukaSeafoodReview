import { describe, expect, it } from "vitest";

import {
  parseHistoryResponse,
  parseProgressResponse,
} from "./types";
import { historyFixture, historyItem, progressFixture, reviewerId } from "../test/task11Fixtures";

describe("progress wire parsing", () => {
  it("accepts the exact six-member aggregate response in canonical order", () => {
    expect(parseProgressResponse(progressFixture)).toEqual(progressFixture);
  });

  it.each([
    ["negative count", { ...progressFixture, reviewed: -1 }],
    ["unsafe percent", { ...progressFixture, completion_percent: Number.NaN }],
    ["duplicate member", { ...progressFixture, members: progressFixture.members.map((row, index) => index === 5 ? { ...row, name: "Hassan" } : row) }],
    ["unknown member", { ...progressFixture, members: progressFixture.members.map((row, index) => index === 5 ? { ...row, name: "Intruder" } : row) }],
    ["wrong order", { ...progressFixture, members: [...progressFixture.members].reverse() }],
    ["private detail", { ...progressFixture, notes: "private review note" }],
    ["unsafe member text", { ...progressFixture, members: progressFixture.members.map((row, index) => index === 0 ? { ...row, name: "Hassan<script>" } : row) }],
  ])("rejects %s before rendering", (_label, value) => {
    expect(() => parseProgressResponse(value)).toThrow("Invalid progress response");
  });
});

describe("history wire parsing", () => {
  it("accepts complete owned items and bounded facet data", () => {
    expect(parseHistoryResponse(historyFixture, reviewerId)).toEqual(historyFixture);
  });

  it.each([
    ["other reviewer", { ...historyItem, reviewer_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" }],
    ["unsafe preview URL", { ...historyItem, preview_url: "http://upload.example.test/fish.jpg" }],
    ["credentialed source URL", { ...historyItem, source_url: "https://secret@example.test/fish" }],
    ["invalid timestamp", { ...historyItem, updated_at: "not-a-time" }],
    ["zero version", { ...historyItem, version: 0 }],
    ["unknown rejection reason", { ...historyItem, rejection_reason: "SECRET_REASON" }],
    ["rejection on approved", { ...historyItem, decision: "APPROVED", rejection_reason: "DUPLICATE" }],
    ["unsafe edit consistency", { ...historyItem, is_current: false, read_only: false }],
  ])("rejects an item with %s", (_label, item) => {
    expect(() => parseHistoryResponse({ ...historyFixture, items: [item] }, reviewerId)).toThrow(
      "Invalid history response",
    );
  });

  it.each([
    ["duplicate species", { species: [historyItem.species, historyItem.species], sources: ["WIKIMEDIA_COMMONS"] }],
    ["duplicate source", { species: [historyItem.species], sources: ["WIKIMEDIA_COMMONS", "WIKIMEDIA_COMMONS"] }],
    ["oversized source", { species: [historyItem.species], sources: ["x".repeat(129)] }],
  ])("rejects %s facets", (_label, filters) => {
    expect(() => parseHistoryResponse({ ...historyFixture, filters }, reviewerId)).toThrow(
      "Invalid history response",
    );
  });
});
