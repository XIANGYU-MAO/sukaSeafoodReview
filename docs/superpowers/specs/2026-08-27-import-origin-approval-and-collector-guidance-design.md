# Import Origin Approval and Collector Guidance Design

## Goal

Make repeated GBIF collection and CSV import a normal admin workflow: Mao can approve a newly observed public HTTPS image host once, future rows from that exact host pass automatically, genuinely unsafe URLs remain blocked, valid rows can be imported without hand-editing a CSV, and reviewers do not receive repeated candidates for the same original URL.

The collection page also generates correct Windows or Unix commands, explains every parameter, and gives a concrete replenishment recipe when an already-reviewed dataset is too small.

## Confirmed user requirements

- GBIF and other upstream datasets may add rows and new publisher hosts over time.
- Approval is per exact hostname, not per image URL.
- Only Mao may approve a hostname.
- Public HTTPS hosts not yet approved are “待批准来源”, not generically “地址不安全”.
- An unknown host is grouped once with its row count and sample rows.
- Mao can approve that host and rerun the existing preview without selecting the file again.
- Mao can explicitly skip all remaining blocked rows and import the valid rows; no manual row deletion is required.
- HTTP, credentials, localhost, literal IPs, private addresses, whitespace and malformed hosts remain non-approvable.
- Approvals are stored in PostgreSQL and audited.
- Exported training manifests carry the exact approved preview/original host for each row so the local downloader uses the same decision without a software update.
- Same species plus same canonical original URL is automatically skipped before review.
- The same canonical original URL assigned to different species is blocking.
- Repeated warnings are grouped; full row samples remain available in a collapsible detail.
- The current verified Natural History Museum host `data.nhm.ac.uk` is included in the built-in server and local defaults.
- The command UI uses pill buttons for Windows and macOS/Linux, a positive `--max-per-species` input, and first/replenishment mode.
- Replenishment mode adds `--resume`; the help popup explains `--source`, `--max-per-species`, `--resume`, output location, deduplication and what to do when quantity is insufficient.
- CSV selection is a full-width accessible drop zone. It accepts one `.csv` by drag-and-drop or native file selection, highlights while a file is dragged over it, displays the selected filename, and reports invalid formats in Chinese.
- The species enabled checkbox, label and help trigger remain together on one baseline.
- Species form action buttons share one height and vertically centered text.
- Species table body cells and edit buttons are vertically centered within every row.

## Import classification

URL validation has two stages:

1. `normalize_public_https_url()` rejects malformed or intrinsically unsafe URLs. These rows use `UNSAFE_URL`, have no approvable host and remain blocking.
2. `require_approved_image_url()` checks the normalized image hostname against built-in settings plus database approvals. A valid but unknown hostname uses `UNAPPROVED_IMAGE_HOST`, includes the exact lowercase hostname, and is blocking until approved or skipped.

Issue output contains bounded row details and exact aggregate groups:

```json
{
  "code": "UNAPPROVED_IMAGE_HOST",
  "message": "图片主机尚未批准",
  "blocking": true,
  "host": "data.example.org",
  "count": 17,
  "sample_rows": [577, 578, 579],
  "omitted_rows": 14
}
```

Date parsing remains metadata-only. Unsupported source date formats retain `raw_source_date`, produce one non-blocking group, and never prevent import.

## Host approval

`image_origin_approvals` stores exact lowercase ASCII hostnames. Suffix/wildcard approvals are not accepted through the UI. The approval endpoint consumes the current preview token and hostname; it only accepts a hostname that appears in that actor/session’s unexpired `UNAPPROVED_IMAGE_HOST` group. Duplicate approval is idempotent. Every first approval writes an `IMAGE_ORIGIN_APPROVED` audit event.

The frontend immediately runs preview again using the already selected `File`. This creates a fresh preview token under the new effective allowlist and invalidates the visible old result.

## Skipping blocked rows

The commit request adds `skip_blocking_rows`, default `false`. With `false`, existing all-or-nothing behavior remains. With `true`, the server locks and recomputes the exact staged file, inserts only `new_normalized_rows`, and records `skipped_blocking` in the result and audit. Fatal file errors never receive a preview token and cannot be skipped. The UI requires a second confirmation naming the blocked and valid counts.

## URL deduplication

Candidate identity deduplication remains `(source_dataset, source_record_id)`.

Canonical `original_url` classification changes:

- Same URL and same species: increment `url_duplicates`, issue a non-blocking `DUPLICATE_IMAGE_URL`, and omit the later row from `new_normalized_rows`.
- Same URL and different species: increment `conflicting_identities`, issue blocking `CONFLICTING_IMAGE_SPECIES`, and omit the later row.
- Existing database candidates participate in the same check.

The collector’s `--resume` still merges its local manifest and deduplicates by species plus image URL. Reuploading the whole manifest is expected; exact database duplicates and URL duplicates are skipped.

## Local training synchronization

Export CSV rows add `preview_origin` and `original_origin`, each the normalized exact hostname already approved by the server when the batch was created. The local manifest parser requires both columns, validates them as exact hostnames, requires each URL against its corresponding exact hostname, and does not require that a future software build know the hostname. The CSV is obtained by Mao from the authenticated application; no credentials or cookies are sent to image origins.

## Command UI

The admin page generates:

```text
Windows first collection:
python .\collect_fish_images.py --config .\species_config.json --source all --max-per-species 100

macOS/Linux replenishment:
python ./collect_fish_images.py --config ./species_config.json --source all --max-per-species 200 --resume
```

`--max-per-species` means the maximum usable licensed rows for each fish species from each selected source, not a total for the whole CSV. If quantity is insufficient, increase it, choose replenishment, retain the existing `output/candidates.csv`, rerun, and upload the complete resulting CSV.

## Admin form and CSV interaction

The candidate CSV control is a `<label>`-backed drop zone with a real file input, so clicking and keyboard activation retain native accessibility. Drag handlers only accept one file and reuse the same `choose(file)` state transition as the native input. The zone never uploads on drop; Mao must still click precheck.

Species form actions use the shared `equal-action-row` layout. The enabled checkbox uses an intrinsic-width checkbox and adjacent text/help. Table body cells use middle vertical alignment and action buttons use inline flex centering.

## Non-goals

- No automatic approval of every host returned by GBIF.
- No wildcard hostname approval from the admin page.
- No server-side image download, proxy or cache.
- No attempt to identify visually similar images before original bytes are downloaded.
- No manual CSV editor in the browser.

## Verification

- API unit/integration tests cover approval authorization, preview-token binding, unsafe versus unapproved classification, idempotent approval, audit, skip-blocking commit, same/different-species URL dedupe, migration and export validation.
- Local sync tests cover the two origin columns and reject a mismatch between a URL and its declared exact host.
- Frontend tests cover grouped issue solutions, approve-and-repreview, skip confirmation, command modes, Unix slashes, parameter help and equal action layout.
- Frontend tests cover CSV drag entry, drag highlight, filename display, invalid extension rejection, compact checkbox layout and table/action vertical centering hooks.
- Full API, local sync and web test suites, typecheck and production build must pass before deployment.
