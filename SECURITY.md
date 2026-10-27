# Security Policy

## Supported versions

Only the latest release is supported. PAE is local-first software: it runs
on your machine, stores its data under your PAE_HOME, and makes no network
calls except to the data providers you configure (see docs/DATA_SOURCES.md).

## Reporting a vulnerability

Email **info@aimlds.org** with the details and steps to reproduce. Please
do not open a public issue for a security report. We aim to acknowledge
reports within 3 business days.

## Design notes

- Vault records and journal entries are encrypted in the browser
  (AES-256-GCM; key derived with Argon2id, RFC 9106 second-recommended
  parameters); the plaintext analytics store is a local file, and the
  knowledge base is encrypted only when content is ingested with a DEK —
  see README.md for the exact split.
- Agent (A2A) and crypto-oracle surfaces require a per-install bearer
  token (file mode 0600, created on first start, never logged).
- PAE is an analytics tool. It does not provide financial advice,
  recommendations, or order execution.
