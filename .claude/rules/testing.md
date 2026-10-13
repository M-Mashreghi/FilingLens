# FilingLens Testing Rules

Every implementation task needs tests appropriate to scope.

Mandatory regression areas include issuer ambiguity, accession/package identity, SEC rate limiting, primary/exhibit selection, 10-K/10-Q section parsing, 8-K item/exhibit parsing, amendment lineage, comparator selection, deterministic diffs, XBRL units/periods/dimensions/custom tags, taxonomy drift, point-in-time filtering, retrieval isolation, LLM schema parsing, evidence binding, research-priority scoring, exports and E2E reports.

Use deterministic local/minimized fixtures in mandatory CI. Keep live SEC and paid-model tests optional behind environment flags with a small request/cost budget.

Every parser/comparator/XBRL bug gets a regression fixture. Never weaken a test only to make failing implementation pass.
