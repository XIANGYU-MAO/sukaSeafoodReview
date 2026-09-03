# SukaSeafood CV Demo Specification

## Goal

Publish a bilingual, mobile-friendly proof-of-concept at
`https://findai.top/sukaseafood/cv/` that accepts an uploaded or newly captured
fish photo and returns the reviewed five-class model's Top-3 suggestions.

## Audience and primary flow

The page is for project stakeholders and backend/mobile developers who need to
try the frozen model without installing Python.

1. Open the page and immediately see the photo control.
2. Choose an existing JPEG/PNG/WebP image or use the phone's rear camera.
3. Preview the image and select **Identify fish**.
4. See the model status, Top-3 candidates, confidence, class code and canonical
   `seafood_item_id` UUID.
5. Read the short technical/configuration notes or switch between Chinese and
   English.

## Architecture

- Extend the existing `sukaSeafoodReview` static portal at
  `web/static/portal/cv/`; the current build, Nginx and Caddy routing already
  publish that directory at `/sukaseafood/cv/`.
- Run the frozen ONNX model locally in the browser with
  `onnxruntime-web@1.20.1` and the WebAssembly execution provider.
- Keep the selected photo in browser memory only. Do not upload, persist, log or
  reuse it.
- Use native `<input type="file" capture="environment">`; do not add a camera
  framework, database, authentication or new service.
- Load `class_map.json`, `preprocessing.json` and `model_card.json` from the same
  directory so displayed IDs and configuration stay tied to the artifact.

## Frozen model contract

- Version: `cv-i1-5class-20260902T174905Z-36ac9b6a-a53adadffa11`
- ONNX SHA-256:
  `69f0820c4e200128fb2dced98dcc79112188265714ad0b0d1df582d1af3f4208`
- Input: `input`, float32 NCHW `[1, 3, 224, 224]`
- Output: `logits`, float32 `[1, 5]`
- Preprocessing: EXIF orientation, RGB, short side 256, centre crop 224,
  scale to 0-1, ImageNet mean/std normalization, bilinear interpolation.
- Classes in index order: `SF001`, `SF002`, `SF007`, `SF008`, `SF012`.
- Decision threshold: 0.3. Every response remains a suggestion requiring human
  confirmation; confidence is an uncalibrated softmax score.

## Page content and states

- Header: SukaSeafood brand, project-portal link and Chinese/English toggle.
- First viewport: compact purpose text, privacy note, upload/camera control,
  image preview, identify button and result area.
- States: empty, selected, model-loading, analysing, invalid type/size/decode,
  candidates, low confidence and runtime failure.
- Result: Top-1 visually prominent followed by ranks 2-3. Show
  `class_code` and `seafood_item_id`; do not label the UUID as `fish_id`.
- Technical section: MobileNetV3 Small, transfer learning, ONNX/WASM,
  preprocessing, threshold and version.
- Evaluation section: clean test n=82, accuracy 90.24%, macro-F1 90.30%, Top-3
  hit rate 100%; clearly label these as dataset results, not production proof.
- Limitations: five known classes only, whole-fish imagery, small/non-retail
  dataset, Kembung/Cencaru confusion, licence review outstanding and mandatory
  human confirmation.

## Visual direction

Use an ice-market inspection console: deep navy canvas, cold cyan/teal accents,
crisp white content and a restrained scan-line treatment over the user's photo.
Extend the existing SukaSeafood visual language without copying the review SPA.
Body copy is at least 16px, controls are keyboard accessible and the layout
collapses to one column on small screens.

## Non-goals

- No server-side inference, image storage, telemetry or feedback persistence.
- No continuous camera preview, object detection or counting individual fish.
- No claims that the model recognizes species outside the five reviewed classes.
- No public use of the two private smoke-test photos as bundled site content.

