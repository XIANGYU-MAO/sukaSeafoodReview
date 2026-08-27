# Task 4 report — collector ZIP

## RED

`python -m pytest collector/tests/test_package.py -q` failed as expected at the
first assertion: `assert ZIP.is_file()`. The published ZIP did not exist yet,
so the test did not attempt to open a missing archive.

## GREEN

Added a fixed-allowlist package builder and generated
`web/public/downloads/sukaseafood-collector.zip`. The package test verifies
that the ZIP has only the five required members and that each member matches
its current collector source bytes. The compose test verifies that the web
build context remains `./web` and contains the generated ZIP.

## Verification

- `python collector/build_package.py --check` — passed
- `python -m pytest collector/tests/test_package.py tests/test_compose_config.py -q` — 9 passed
- `python -m compileall -q collector` — passed
- `git diff --check` — passed

## Commit

`build(web): publish the Windows collector bundle`

## Concerns

None. `--check` intentionally validates only the current fixed allowlist and
never rewrites the archive.
