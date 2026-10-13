# FilingLens Financial & Research Integrity Rules

## Point-in-Time

Every historical run has an explicit `as_of`. A source is eligible only if it was publicly available by that timestamp. Prefer accepted/disseminated timestamp over date-only metadata where available.

## Deterministic Numbers

LLMs are not authoritative calculators for financial statement values, growth, margins, cash/debt changes, XBRL deltas, ratios, returns or statistical tests.

## Provenance

Every numeric signal retains CIK, accession, form, period, taxonomy/concept, unit, context/dimensions where applicable, source fact ids and transformation version.

## Filing Lineage

Original filings and amendments are separate immutable records. Historical `as_of` runs must not apply an amendment that had not yet been filed.

## Missing Data

Missing is not zero. Unknown is not unchanged. Do not silently impute.

## Research Priority

FilingLens ranks analyst attention. It does not issue legal materiality determinations or allegations of misconduct.
