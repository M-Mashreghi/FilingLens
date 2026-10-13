# FilingLens — SEC Data Contract

This document records the source/access assumptions that FilingLens code must treat as explicit external contracts. Review these assumptions periodically because SEC interfaces and access policies can change.

**Last policy review:** 2026-10-13

## 1. Canonical Sources

### Submissions / filing history

Per-company submissions:

`https://data.sec.gov/submissions/CIK##########.json`

Historical bulk bootstrap:

`https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`

Implementation (`filinglens.infrastructure.sec.submissions`, `sec-submissions/1`):

- `SubmissionsAdapter` fetches `CIK##########.json` and every history page listed in `filings.files` through the governed client. History pages hold the same columnar arrays at top level; mismatched column lengths are rejected.
- `BulkSubmissionsArchive` reads a locally downloaded `submissions.zip`; per-company and bulk paths produce identical `Filing` identities (verified by test) and both are recorded in `filing_observations` with their sha256 dataset version.
- **`acceptanceDateTime` quirk:** the value carries a `Z` suffix but is EDGAR acceptance time in US Eastern time (e.g. an after-17:30 acceptance on day D receives filing date D+1). FilingLens reads it as America/New_York, which is also the conservative point-in-time choice (a later UTC instant than reading it as UTC).
- 8-K `items`, `primaryDocDescription`, `isXBRL` and `isInlineXBRL` are preserved on `Filing`.
- `FilingMetadataService.filings_as_of` is the point-in-time view (`available_at <= as_of`).

### XBRL aggregate facts

Per-company standardized facts:

`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

Per-concept endpoint is also available. SEC also exposes a `frames` API for cross-entity standardized facts.

Historical bulk bootstrap:

`https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`

### Accession package / filing documents

Filing content lives under the SEC Archives accession directory. Directory trees expose machine-readable `index.json` / `index.xml` in addition to HTML directory views. Filing detail/index pages identify the primary document and exhibit/data files.

Implementation (`filinglens.infrastructure.sec.package`, `sec-package/1`; classifier `package-classifier/1` in `filinglens.domain.package`):

- `index.json` supplies the complete file inventory (name, size); `{accession}-index.htm` (`table.tableFile` rows) supplies Seq/Description/Type. Files are merged by filename and a file present in only one source is kept.
- Roles: `primary` (the submissions `primaryDocument`, else Seq 1 whose Type equals the form), `exhibit` (`EX-*` except `EX-101*`), `xbrl` (`EX-101*`, `.xsd`, linkbases, extracted `_htm.xml`, `FilingSummary.xml`, `MetaLinks.json`), `complete_submission` (`{accession}.txt`), `index`, `supporting` (graphics, `R*.htm` renderings, `Financial_Report.xlsx`) and `unknown` (retained, never dropped).
- Document identity is `(accession, filename)`; `PackageManifest` rejects documents from another accession or duplicate filenames.

## 2. Fair Access

SEC currently publishes a maximum automated request rate of **10 requests/second**. FilingLens should default to a lower configurable rate, use bounded concurrency/cache/backoff, and treat the SEC-published ceiling as an external policy that may change.

Live mode requires a descriptive configured User-Agent/contact. Do not evade rate controls or use anti-bot bypass behavior.

Implementation (`filinglens.infrastructure.sec.client.SecHttpClient`, version `sec-http/1`):

- the only module allowed to import an HTTP library (enforced by a test); hosts are restricted to `www.sec.gov`, `data.sec.gov`, `efts.sec.gov`;
- live mode (`SEC_LIVE_MODE=true`) refuses to start without a `SEC_USER_AGENT` containing an e-mail or URL contact; offline mode refuses to start without an injected fixture transport;
- `RateGovernor` (GCRA, burst 1) spaces requests at `1/SEC_MAX_RPS` (default 5/s; values above the published ceiling are rejected) and every retry passes through it; `SEC_MAX_CONCURRENCY` (default 2) bounds in-flight requests;
- 429/5xx/timeouts/transport errors: at most `max_retries` (default 3) retries with capped exponential backoff and jitter; `Retry-After` honoured up to 60 s; then `SecUnavailableError`;
- 403 → `SecForbiddenError` (never retried); 404 → `SecNotFoundError`;
- HTTP cache (`FileHttpCache` under `FILINGLENS_CACHE_DIR/http`) with TTL and `ETag`/`Last-Modified` revalidation; it is a network optimisation, not provenance storage;
- `SecMetrics` counts requests, network requests, cache hits/revalidations, retries, 429/403/5xx/transport errors and throttle wait time.

## 3. Browser Boundary

`data.sec.gov` does not support CORS. The Streamlit/browser surface therefore should not directly fetch SEC APIs; the backend SEC adapter owns those calls.

## 4. XBRL Completeness Contract

SEC `companyfacts` / `companyconcept` aggregation is designed around facts that use non-custom standard taxonomies and apply to the entire filing entity. Companies can also use custom extension taxonomies and dimensional facts.

Therefore:

- `companyfacts` is useful for normalized standardized trends.
- It is **not** treated as a complete representation of every filing-specific fact.
- Filing-specific iXBRL extraction is required for custom concepts, dimensions and context-sensitive analysis.

Implementation (`filinglens.infrastructure.sec.companyfacts`, `sec-companyfacts/1`): `CompanyFactsAdapter` (governed client, no API key) and `BulkCompanyFactsArchive` produce identical `XbrlFact` sets (`filinglens.domain.xbrl`, source `companyfacts`) preserving prefix/concept, label, unit, start/end (instant when no start), accession, form, fy/fp, filed date and frame. `CompanyFacts.lookup` returns `missing_concept` / `missing_unit` explicitly. Fact ids are content hashes, so a value re-reported in a later accession is a distinct fact.

## 5. Taxonomy Versioning

SEC-supported XBRL taxonomies change over time. FilingLens stores taxonomy namespace/version and versions its canonical concept mappings. A newly supported taxonomy version must not silently change historical metric semantics.

## 6. Availability / Point-in-Time

For research `as_of` logic:

- preserve filing date and accepted/disseminated timestamp where available;
- use the most precise public-availability timestamp available to determine eligibility;
- never include a later amendment in a historical run before that amendment was public;
- preserve source retrieval time separately from public availability.

Implemented policy `availability/1` (`filinglens.domain.availability`):

1. `available_at` = accepted/disseminated timestamp (UTC) when known (`accepted_timestamp` basis).
2. Otherwise `available_at` = 23:59:59 America/New_York on the filing date (`filing_date_end_of_day_et` basis). EDGAR can assign a filing date after acceptance, so this fallback can only delay eligibility, never leak it.
3. A source is eligible for a run iff `available_at <= as_of`; naive `as_of` values are rejected.

`Filing.available_at` / `availability_basis` are derived fields, recomputed whenever a more precise timestamp is merged in.

## 7. Identity

- CIK identifies the filer; tickers can change.
- Accession number identifies the submission package.
- An accession package can contain a primary filing, exhibits, XBRL files, graphics and the complete submission text.
- Document-level identities are scoped to the accession package.
- Accessions are stored in canonical dashed form `##########-YY-######`; the no-dash form is used in Archives paths: `https://www.sec.gov/Archives/edgar/data/{cik}/{accession-nodash}/{filename}`, with `index.json`, `{accession}-index.htm` and the complete submission `{accession}.txt` in the same directory (`filinglens.domain.accession`).

## 8. External References

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Accessing EDGAR Data / fair access: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- Inline XBRL: https://www.sec.gov/data-research/structured-data/inline-xbrl
- Standard taxonomies: https://www.sec.gov/data-research/structured-data/taxonomies-schemas/standard-taxonomies
