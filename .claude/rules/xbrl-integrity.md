# XBRL Integrity Rules

- `companyfacts` is a convenient standardized aggregate source, not a complete representation of every filing-specific fact.
- Direct filing iXBRL extraction is required when custom concepts, dimensions, hidden facts, or filing-specific context matter.
- Preserve taxonomy namespace/version, concept identity, standard-vs-custom status, context, period, unit, decimals/scale/sign and dimensions.
- Never flatten dimensional facts into an entity-wide metric silently.
- Custom concepts are first-class facts. Mapping a custom concept to a canonical metric requires an explicit versioned rule/evidence.
- Taxonomy versions evolve; concept mappings must be versioned and regression-tested.
- Missing facts are not zeros. Duplicate contexts require a documented resolution policy.
