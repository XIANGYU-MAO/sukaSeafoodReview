import { expect, test } from "vitest";

import { computeResize, rankPredictions, softmax, toNchw } from "./cv-core.mjs";

test("normalizes large logits without overflow", () => {
  expect(softmax([1000, 1000])).toEqual([0.5, 0.5]);
});

test("keeps the configured aspect ratio and CHW channel order", () => {
  expect(computeResize(400, 200, 256)).toEqual({ width: 512, height: 256 });
  expect(computeResize(333, 200, 256)).toEqual({ width: 426, height: 256 });

  const tensor = toNchw(
    { width: 1, height: 1, data: [255, 0, 127, 255] },
    [0, 0, 0],
    [1, 1, 1],
  );
  expect(tensor).toHaveLength(3);
  expect(tensor[0]).toBeCloseTo(1, 6);
  expect(tensor[1]).toBeCloseTo(0, 6);
  expect(tensor[2]).toBeCloseTo(127 / 255, 6);
});

test("returns Top-3 with canonical seafood IDs and threshold status", () => {
  const classes = [
    { class_index: 0, class_code: "SF001", fish_id: "SF001", seafood_item_id: "uuid-kembung" },
    { class_index: 1, class_code: "SF002", seafood_item_id: "uuid-bawal" },
    { class_index: 2, class_code: "SF007", seafood_item_id: "uuid-cencaru" },
    { class_index: 3, class_code: "SF008", seafood_item_id: "uuid-jenahak" },
    { class_index: 4, class_code: "SF012", seafood_item_id: "uuid-tenggiri" },
  ];
  const atThreshold = rankPredictions(
    [Math.log(0.3), Math.log(0.25), Math.log(0.2), Math.log(0.15), Math.log(0.1)],
    classes,
    0.3,
    3,
  );

  expect(atThreshold.status).toBe("CANDIDATES");
  expect(atThreshold.predictions.map(({ class_code }) => class_code)).toEqual([
    "SF001",
    "SF002",
    "SF007",
  ]);
  expect(atThreshold.predictions[0].seafood_item_id).toBe("uuid-kembung");
  expect(atThreshold.predictions[0]).not.toHaveProperty("fish_id");
  expect(atThreshold.predictions[0].confidence).toBeCloseTo(0.3, 8);

  expect(rankPredictions([0, 0, 0, 0, 0], classes, 0.3, 3).status)
    .toBe("LOW_CONFIDENCE");
});
