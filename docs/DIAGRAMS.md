# FilingLens Diagrams

Mermaid diagrams for implementation and review.

## 1. System Architecture

```mermaid
flowchart TB
    U[Analyst] --> UI[Streamlit Dashboard]
    UI --> API[FastAPI]
    API --> WF[Research Workflow]
    WF --> SEC[SEC Gateway + Rate Governor]
    SEC --> SUB[data.sec.gov Submissions / XBRL]
    SEC --> ARC[SEC Archives / Accession Package]
    WF --> RAW[(Immutable Raw Snapshots)]
    WF --> DB[(SQLite Metadata)]
    WF --> PARSE[10-K / 10-Q / 8-K Parsers]
    PARSE --> LIN[Lineage + Comparator]
    LIN --> DIFF[Deterministic Text/Table Diff]
    WF --> XBRL[CompanyFacts + Direct iXBRL]
    DIFF --> LLM[Structured LLM Analyst]
    XBRL --> LLM
    LLM --> REV[Evidence + Contradiction Reviewer]
    REV --> REPORT[Cited Report / Alerts / Export]
```

## 2. End-to-End Research Run

```mermaid
sequenceDiagram
    autonumber
    actor A as Analyst
    participant API
    participant SEC as SEC Gateway
    participant STORE as Snapshot Store
    participant P as Parser
    participant L as Lineage/Diff
    participant X as XBRL Engine
    participant M as LLM Analyst
    participant R as Reviewer

    A->>API: Analyze issuer/form as_of
    API->>SEC: Resolve issuer + filing metadata
    SEC-->>API: Canonical CIK + accessions
    API->>SEC: Get accession manifest/documents
    SEC->>STORE: Persist immutable snapshots
    STORE-->>P: Exact primary/exhibit bytes
    P-->>L: Parsed items + locators
    L->>L: choose comparator + amendment lineage + diff
    API->>X: standardized + filing-specific facts
    X-->>M: deterministic financial signals
    L-->>M: deterministic changes + evidence ids
    M-->>R: structured findings
    alt evidence valid
        R-->>API: ranked cited report
        API-->>A: report + comparison workspace
    else unsupported/contradictory
        R-->>API: block / needs review
        API-->>A: explicit incomplete result
    end
```

## 3. Accession Package Model

```mermaid
flowchart LR
    ACC[Accession] --> PRI[Primary Filing]
    ACC --> EX[Exhibits]
    ACC --> TXT[Complete Submission Text]
    ACC --> IX[iXBRL / XBRL Files]
    ACC --> IMG[Other Attachments]
    PRI --> S1[Immutable Snapshot + Hash]
    EX --> S2[Immutable Snapshots + Hashes]
    IX --> S3[Immutable XBRL Snapshot + Hash]
```

## 4. Filing Lineage

```mermaid
flowchart TB
    P[Prior Comparable Period] --> BASE[Original Filing]
    BASE -->|amended by| AM[10-K/A or 10-Q/A]
    E[Related 8-K Event] -. non-reliance/restatement evidence .-> AM
    BASE --> V1[Historical view before amendment]
    AM --> V2[View after amendment availability]
    P --> C1[Period Comparison]
    BASE --> C1
    BASE --> C2[Amendment Diff]
    AM --> C2
```

## 5. XBRL Dual Path

```mermaid
flowchart LR
    SEC[Official SEC] --> CF[CompanyFacts\nstandardized entity-wide]
    SEC --> IX[Direct Filing iXBRL\ncustom + dimensions + context]
    CF --> N[Normalized Fact Model]
    IX --> N
    N --> MAP[Versioned Canonical Metric Mapping]
    MAP --> FIN[Deterministic Financial Signals]
    FIN --> REC[Narrative ↔ Numeric Reconciliation]
```

## 6. Evidence-Backed Finding

```mermaid
flowchart LR
    OLD[Comparator Evidence] --> D[Deterministic Diff]
    NEW[Focal Evidence] --> D
    XF[XBRL Facts] --> CALC[Deterministic Metrics]
    D --> M[LLM Interpretation]
    CALC --> M
    M --> F[Structured Finding]
    F --> C{Evidence / contradiction checks}
    C -->|Fail| B[Blocked / not_supported]
    C -->|Pass| S[Research Priority Score]
    S --> O[Cited Report]
```

## 7. Run State Machine

```mermaid
stateDiagram-v2
    [*] --> Created
    Created --> Resolving
    Resolving --> Collecting
    Resolving --> NeedsInput
    Collecting --> Parsing
    Collecting --> BlockedBySource
    Parsing --> Aligning
    Parsing --> Failed
    Aligning --> Analyzing
    Aligning --> NeedsInput
    Analyzing --> Reviewing
    Analyzing --> Failed
    Reviewing --> Completed
    Reviewing --> NeedsInput
    Reviewing --> Failed
    BlockedBySource --> Collecting
    NeedsInput --> Resolving
    NeedsInput --> Aligning
    NeedsInput --> Reviewing
    Completed --> [*]
    Failed --> [*]
```

## 8. Watchlist Automation

```mermaid
sequenceDiagram
    participant J as Scheduler
    participant DB as Watchlist DB
    participant SEC as SEC Gateway
    participant WF as Research Workflow
    participant Q as Triage Queue

    J->>DB: watched CIKs + last-seen accession
    J->>SEC: recent submissions
    SEC-->>J: accession metadata + accepted time
    alt unseen eligible filing
        J->>DB: reserve accession idempotently
        J->>WF: create analysis run
        WF-->>Q: report / high-priority findings / failure
    else already processed
        J-->>DB: no-op
    end
```
