# FilingLens — Target Project Structure

```text
FilingLens/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── .claude/
│   └── rules/
│       ├── architecture.md
│       ├── sec-access.md
│       ├── finance-integrity.md
│       ├── xbrl-integrity.md
│       ├── llm-analysis.md
│       ├── testing.md
│       └── documentation.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SEC_DATA_CONTRACT.md
│   ├── EVALUATION.md
│   ├── ROADMAP.md
│   ├── PHASES.md
│   ├── PROJECT_PHASES_AND_TASKS.md
│   ├── TASKS.md
│   ├── DIAGRAMS.md
│   ├── DEPLOYMENT.md
│   └── RUNBOOK.md
├── src/filinglens/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   │   ├── sec/
│   │   ├── storage/
│   │   ├── xbrl/
│   │   └── llm/
│   ├── parsing/
│   ├── lineage/
│   ├── diff/
│   ├── analytics/
│   ├── retrieval/
│   ├── reporting/
│   ├── api/
│   └── ui/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   ├── e2e/
│   └── fixtures/
└── scripts/
    ├── bootstrap_sec_bulk.py
    └── run_eval.py
```

The layout is directional, not a requirement to create empty modules before their task is implemented. Keep architecture boundaries; avoid scaffolding unused infrastructure.
