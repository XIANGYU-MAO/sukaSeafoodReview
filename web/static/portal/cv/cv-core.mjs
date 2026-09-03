function positiveNumber(value, name) {
  if (!Number.isFinite(value) || value <= 0) {
    throw new TypeError(`${name} must be a positive number`);
  }
}

export function softmax(logits) {
  if (!logits?.length || Array.from(logits).some((value) => !Number.isFinite(value))) {
    throw new TypeError("logits must be a non-empty finite vector");
  }
  const max = Math.max(...logits);
  const exponentials = Array.from(logits, (value) => Math.exp(value - max));
  const total = exponentials.reduce((sum, value) => sum + value, 0);
  return exponentials.map((value) => value / total);
}

export function computeResize(width, height, shortSide) {
  positiveNumber(width, "width");
  positiveNumber(height, "height");
  positiveNumber(shortSide, "shortSide");
  return width <= height
    ? { width: Math.round(shortSide), height: Math.floor((height * shortSide) / width) }
    : { width: Math.floor((width * shortSide) / height), height: Math.round(shortSide) };
}

export function toNchw(imageData, mean, std) {
  const { width, height, data } = imageData ?? {};
  positiveNumber(width, "image width");
  positiveNumber(height, "image height");
  if (!data || data.length !== width * height * 4) {
    throw new TypeError("image data must contain RGBA pixels");
  }
  if (mean?.length !== 3 || std?.length !== 3 || std.some((value) => !value)) {
    throw new TypeError("mean and std must contain three non-zero channels");
  }

  const pixels = width * height;
  const output = new Float32Array(pixels * 3);
  for (let pixel = 0; pixel < pixels; pixel += 1) {
    const rgba = pixel * 4;
    for (let channel = 0; channel < 3; channel += 1) {
      output[channel * pixels + pixel] = (data[rgba + channel] / 255 - mean[channel]) / std[channel];
    }
  }
  return output;
}

export function rankPredictions(logits, classes, threshold, limit = 3) {
  if (!Array.isArray(classes) || classes.length !== logits?.length) {
    throw new TypeError("class map must match the model output length");
  }
  if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
    throw new TypeError("threshold must be between zero and one");
  }
  positiveNumber(limit, "limit");

  const predictions = softmax(logits)
    .map((confidence, index) => {
      const { fish_id: _legacyFishId, ...mappedClass } = classes[index];
      return { ...mappedClass, confidence };
    })
    .sort((left, right) => right.confidence - left.confidence)
    .slice(0, Math.min(Math.floor(limit), classes.length))
    .map((prediction, index) => ({ rank: index + 1, ...prediction }));

  return {
    status: predictions[0].confidence >= threshold ? "CANDIDATES" : "LOW_CONFIDENCE",
    predictions,
  };
}

