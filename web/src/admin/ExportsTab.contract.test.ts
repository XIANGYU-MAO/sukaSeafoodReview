import { describe, expect, it } from "vitest";

import { MAX_RECEIPT_FILE_BYTES, receiptFileSizeAllowed } from "./ExportsTab";


describe("export envelope contract", () => {
  it("uses the same 20 MiB receipt boundary as the API and local tool", () => {
    expect(MAX_RECEIPT_FILE_BYTES).toBe(20 * 1024 * 1024);
    expect(receiptFileSizeAllowed(MAX_RECEIPT_FILE_BYTES)).toBe(true);
    expect(receiptFileSizeAllowed(MAX_RECEIPT_FILE_BYTES + 1)).toBe(false);
  });
});
