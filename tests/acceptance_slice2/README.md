# Slice 2 independent acceptance suite

## Boundary

The suite is a black-box gate for baseline `93f12a3` plus the slice 2 candidate.
Its frozen normative source is `docs/testing/slice2_acceptance_contract.md`.
Tests do not import production modules and observe the product only through:

- `/api/v1` HTTP responses;
- exported portable skill JSON;
- diagnostic ZIP artifacts;
- the published Draft 2020-12 evidence schema;
- the independent DeepSeek/MCP fixture request journal.

Synthetic skill variants are derived from public exports, RFC8785-signed in the
test process, and imported through the public multipart endpoint. They do not
change the production package or relax schema/semantic validation.

## Priority

P0 release blockers:

1. Eight distinct execution outcomes, empty/null/typed-zero classification,
   partial versus contract-error reduction, and malformed MCP fail-closed.
2. Retry budgets, overall deadline, dependency-specific diagnostics, and
   recovery on the next turn in the same session.
3. Page-size-plus-one at 0/19/20/21 rows; lossless 43-row keyset traversal;
   no static TOP; no continuation replanning.
4. Opaque continuation syntax, session, single-use, concurrent claim, catalog,
   skill, marker and server-owned argument binding with exact status/code.
5. Maintenance preview/confirm, canonical scope closure, stale/active targets,
   replay, atomic concurrent confirm and preservation of the catalog.
6. Package-level keyset gate for R01A-D, R06, SP03 and SL01.
7. Atomic semantic rejection of malformed keyset cursor/sort/identity contracts.

P1 contract and composition evidence:

1. R06 metadata-only fields, optional defaults, positive/empty execution and
   bare-export transfer to a clean catalog.
2. SP04 keyset/page-scoped count contract and absence of a false total.
3. SP01 full ten-field required header plus five execution indicators.
4. Real Q054 R06-to-stock typed step binding with one planner call and two MCP
   calls.
5. In-flight catalog snapshot pinning across public hot replacement.
6. Barcode-register projection deduplication across keyset pages and evidence
   1.0/1.1 diagnostic compatibility.

## Independent Oracles

- Row values and order come from fixture-owned datasets, not rendered text or
  production normalization helpers.
- Query shape, normalized optional defaults, limits, retries and call counts
  come from the fixture transport journal.
- Facts, coverage criticality, pinned skill digests and hidden-context rules
  come from the exported diagnostic evidence bundle.
- Public status, error code, stable reason, pagination DTO and clear counts are
  asserted directly from HTTP responses.
- Portable contract assertions inspect bytes exported by the running app; a
  second clean data directory proves transfer rather than local registry reuse.

## Fixture Model

The fixture server provides deterministic warehouse, shipment, stock and order
schemas. It supports 0/19/20/21/43 rows, equal sort values across a page edge,
typed zero, one-row null sentinel, missing/wrong/null columns, query errors,
provider outages, bounded delays/retries, malformed MCP wrappers, and blocking
barriers for race tests. The planner response is generated from the public
skill manifest and echo fields supplied by the app; it contains no copied
production planner implementation.

## Release Gate

The slice gate passes only when every non-live test passes and both exact
wall-clock TTL cases have also been run successfully. Default fast runs skip the
five-minute maintenance and thirty-minute continuation expiry waits; enable
them explicitly with `SLICE2_RUN_WALL_CLOCK_TTL=1` before release.

Synthetic evidence cannot satisfy a live MCP gate. `AC-024.list` is reported
separately; `AC-024.rank`, full Q031 total, and global `AC-024` remain `not_run`
until their dedicated suites execute.

Run:

```bash
.venv/bin/pytest -q tests/acceptance_slice2
SLICE2_RUN_WALL_CLOCK_TTL=1 .venv/bin/pytest -q tests/acceptance_slice2
```
