# FilingLens — Evaluation & Golden Corpus

FilingLens is not considered reliable because a few demos look good. Parser, comparator, retrieval, XBRL and LLM quality must be measured on a versioned benchmark.

## 1. Evaluation Layers

### A. Identity & ingestion

Measure:

- issuer/CIK resolution accuracy
- accession/package identity accuracy
- primary/exhibit classification accuracy
- point-in-time eligibility correctness

Blocking target on curated release set: **100% filing/accession identity accuracy**.

### B. Parsing

Gold labels include section/item boundaries and 8-K item/exhibit associations.

Metrics:

- section/item exact or overlap boundary score
- required-section success rate
- exhibit-link resolution accuracy
- ambiguous-case detection rate

Initial target: **>95% required section extraction success**, with no silent false-success on labeled ambiguous cases.

### C. Comparator / lineage

Gold labels include correct prior filing and amendment/base relationships.

Initial target: **>98% comparator accuracy** on curated cases; point-in-time leakage is a blocking zero-tolerance failure.

### D. Diff

Deterministic fixtures must reproduce exact expected added/removed/changed blocks. Table diff is evaluated only on supported normalized table shapes.

### E. XBRL

Corpus must include:

- annual/quarterly duration facts
- instant facts
- multiple units
- dimensional facts
- custom concepts
- taxonomy-version changes
- amended/restated filings

Metrics focus on correct fact selection, period/unit/dimension preservation and canonical metric reconciliation.

### F. Retrieval

Use question/evidence pairs with accession/item/document filters.

Metrics:

- recall@k
- wrong-accession retrieval rate
- wrong-section retrieval rate

Wrong-accession retrieval in a supposedly bounded query is a blocking defect.

### G. LLM findings

Human labels distinguish:

- supported finding
- partially supported
- unsupported
- missed material research change
- interpretation disagreement

Track precision, recall, evidence-binding rate, unsupported-claim rate and calibration by confidence bucket.

Blocking report requirement: **100% accepted material factual findings have valid evidence ids** and the reviewer must block known unsupported claims in the release suite.

## 2. Suggested Initial Corpus

Aim for a diverse but maintainable set, for example:

- 20 issuers across multiple sectors and filing styles
- 20 10-K comparison pairs
- 30 10-Q comparison pairs
- 20 8-K packages with exhibits
- 10 amendment/restatement lineage cases
- 15 filing-specific iXBRL cases with custom or dimensional facts

This is a target corpus, not a requirement for Task 1.1. Build it incrementally with tasks that introduce each capability.

## 3. Fixture Policy

- Prefer minimized legal fixtures derived from public filing structure when possible.
- Do not commit unnecessary full raw filings just to test one parser edge case.
- Store accession/source metadata for any public-source regression case.
- Keep expected outputs versioned.

## 4. LLM Eval Policy

Mandatory CI uses deterministic provider fakes and schema/evidence tests. Paid/live LLM evaluation is separate, versioned and reproducible with recorded model/prompt settings.

## 5. Release Qualification

A release record should contain:

- code commit
- corpus version
- parser/comparator/XBRL metrics
- LLM model + prompt version for live eval
- point-in-time test result
- unsupported-claim result
- known waivers/regressions
