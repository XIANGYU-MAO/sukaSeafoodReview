const keyFor = (reviewerId: string) => `sukaseafood:review-guidelines:${reviewerId}`;

export const hasSeenReviewGuidelines = (reviewerId: string) =>
  sessionStorage.getItem(keyFor(reviewerId)) === "1";

export const markReviewGuidelinesSeen = (reviewerId: string) =>
  sessionStorage.setItem(keyFor(reviewerId), "1");

export const resetReviewGuidelines = (reviewerId: string) =>
  sessionStorage.removeItem(keyFor(reviewerId));
