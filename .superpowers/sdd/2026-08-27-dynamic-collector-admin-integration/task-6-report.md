# Task 6 report — retire fixed five-species import

## RED

Updated only the existing focused deployment/README contract assertions to the
current collector path and count-agnostic import result. The required focused
run failed as expected before implementation:

```text
3 failed, 9 passed
```

The failures identified the legacy Windows collector path, the fixed import
assumptions in the operations guide, and the retired README manifest contract.

## GREEN

- Deleted the default five-species command and its exact test; six-account
  seeding remains unchanged and first deployment leaves the catalog empty.
- Changed the Windows import helper default to
  `C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv`.
  It still validates a regular CSV, hashes and verifies upload, requires a
  clean dry-run with `blocking_errors == 0` and `can_commit`, and requires
  explicit `-Commit`.
- After a commit, the helper retrieves the commit report, verifies
  `file_sha256` against the local SHA-256, and prints `total`, `inserted`,
  `skipped_exact`, and `possible_url_duplicates` without count/source/database
  assumptions.
- Updated the current root and operational documentation for the four-step
  `采集与导入` workflow, `collector/`, an empty initial catalog, arbitrary valid
  row/source mixes, and `local_sync/` as the separate approved-original
  downloader. The plan index now links the approved dynamic-collector design as
  current authority without changing dated historical plans/specs.

## Executable -WhatIf evidence

```text
WHATIF-NO-NETWORK: validate one .csv, hash it, upload as SHA256.csv, run dry-run, retrieve report, and commit only with -Commit.
python -m pytest tests/test_import_deploy.py::test_import_whatif_performs_no_network_or_file_read -q
1 passed
```

## Verification

```text
python -m pytest tests/test_import_deploy.py tests/test_first_deploy.py api/tests/test_readme_contract.py -q
12 passed

python -m compileall -q api/app api/tests
passed

git diff --check
passed

rg -n "app\.commands\.seed_species|DEFAULT_SPECIES" api deploy docker-compose.yml docker-compose.production.yml README.md README_ZH.md
no matches
```

## Commit

`docs(deploy): retire the fixed five-species import`

## Concerns

No remote deployment, SSH call, push, or legacy desktop-directory deletion was
performed. The normal single-operator helper flow is covered; no compatibility,
upgrade, concurrency, or extreme-path behavior was added.
