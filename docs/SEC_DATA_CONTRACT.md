# FilingLens — SEC Data Contract

This document records the source/access assumptions that FilingLens code must treat as explicit external contracts. Review these assumptions periodically because SEC interfaces and access policies can change.

**Last policy review:** 2026-10-13

## 1. Canonical Sources

### Submissions / filing history

Per-company submissions:

`https://data.sec.gov/submissions/CIK##########.json`

Historical bulk bootstrap:

`https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`

### XBRL aggregate facts

Per-company standardized facts:

`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

Per-concept endpoint is also available. SEC also exposes a `frames` API for cross-entity standardized facts.

Historical bulk bootstrap:

`https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`

### Accession package / filing documents

Filing content lives under the SEC Archives accession directory. Directory trees expose machine-readable `index.json` / `index.xml` in addition to HTML directory views. Filing detail/index pages identify the primary document and exhibit/data files.

## 2. Fair Access

SEC currently publishes a maximum automated request rate of **10 requests/second**. FilingLens should default to a lower configurable rate, use bounded concurrency/cache/backoff, and treat the SEC-published ceiling as an external policy that may change.

Live mode requires a descriptive configured User-Agent/contact. Do not evade rate controls or use anti-bot bypass behavior.

## 3. Browser Boundary

`data.sec.gov` does not support CORS. The Streamlit/browser surface therefore should not directly fetch SEC APIs; the backend SEC adapter owns those calls.

## 4. XBRL Completeness Contract

SEC `companyfacts` / `companyconcept` aggregation is designed around facts that use non-custom standard taxonomies and apply to the entire filing entity. Companies can also use custom extension taxonomies and dimensional facts.

Therefore:

- `companyfacts` is useful for normalized standardized trends.
- It is **not** treated as a complete representation of every filing-specific fact.
- Filing-specific iXBRL extraction is required for custom concepts, dimensions and context-sensitive analysis.

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
