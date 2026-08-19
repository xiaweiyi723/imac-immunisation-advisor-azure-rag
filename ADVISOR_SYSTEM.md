# IMAC Guidance Advisor System

This system prototype implements the use case described in
`Immunisation_Guidelines_Adviser_Agent (2).pdf`.

## Purpose

The application supports IMAC clinical advisors by turning repeated
immunisation questions into case records with:

- advisor sign-in
- consultation capture
- Azure AI Foundry Agent draft answer
- evidence snippets from approved guidance
- case classification
- status and priority tracking
- CRM-ready JSON export

It does not provide autonomous clinical advice and does not connect to live
production CRM or telephony systems.

## Run

```bat
start-advisor-system.bat
```

Or:

```bat
venv\Scripts\python.exe system_app.py
```

Open:

```text
http://localhost:8600
```

## Demo accounts

```text
advisor@imac.local / advisor123
lead@imac.local / lead123
systems@imac.local / systems123
```

## Data

Consultations are saved locally in:

```text
advisor_system.db
```

The existing Azure Agent configuration is still read from `.env`.
