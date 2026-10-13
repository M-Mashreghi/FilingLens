# FilingLens v2 Review Notes

The original 35-task plan was a good MVP skeleton, but the review identified several gaps that matter for a credible SEC filing intelligence product.

## Critical additions

1. **SEC fair-access governor** — explicit central request-rate/concurrency enforcement.
2. **Bulk metadata path** — official SEC submissions/companyfacts bulk archives for historical bootstrap.
3. **Full accession package manifest** — primary filing is not the whole filing package.
4. **8-K item + exhibit parsing** — important corporate events frequently rely on exhibits.
5. **Amendment/restatement lineage** — 10-K/A / 10-Q/A and related event history must not overwrite originals.
6. **Direct filing iXBRL extraction** — needed for custom concepts, dimensions and filing-specific context not represented by aggregate standardized APIs.
7. **Taxonomy-version drift** — XBRL taxonomies evolve; mappings must be versioned.
8. **Deterministic table diff** — narrative changes often live in tables as well as paragraphs.
9. **Golden evaluation corpus** — parser/retrieval/LLM quality needs measurable regression tests.
10. **Research triage alerts** — daily workflow needs deduplicated analyst review, not just watchlist polling.
11. **Production fair-access/integrity qualification** — rate, leakage, citation and provenance checks become an explicit release gate.

## Scope result

- Previous atomic implementation tasks: **35**
- Revised v1 atomic implementation tasks: **44**
- Post-v1 form extensions remain a roadmap backlog rather than bloating v1.
