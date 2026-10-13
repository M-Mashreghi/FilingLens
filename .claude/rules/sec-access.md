# SEC Access & Provenance Rules

- All SEC requests must use the shared SEC HTTP client/rate governor.
- Live mode requires a descriptive configured User-Agent/contact.
- Use bounded concurrency, caching, timeouts and backoff.
- Never evade SEC rate controls or add anti-bot bypass behavior.
- Treat the published SEC request ceiling as an external policy that can change; configuration must be conservative and testable.
- For large historical loads, prefer official SEC bulk archives when practical.
- Preserve source URL, retrieval timestamp, HTTP metadata where useful, byte hash and accession linkage.
- Do not let Streamlit/browser code call `data.sec.gov` directly; backend fetches SEC data.
- A network cache is not provenance storage. Completed research must retain or be able to verify the exact raw evidence it used.
