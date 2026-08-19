# IMAC Immunisation Guidelines Advisor

An Azure-enabled retrieval-augmented generation (RAG) proof of concept for clinical advisors handling repeated immunisation questions.

## What the project demonstrates

- Case-centred advisor workflow rather than a public medical chatbot
- Azure AI Foundry Agent integration
- Retrieval from approved immunisation guidance
- Evidence snippets and source transparency
- Consultation classification, priority, status and case history
- CRM-ready JSON export
- Human review and clinical-safety boundaries

## Main files

- `system_app.py` - full advisor workflow PoC and local case database
- `backend.py` - Azure AI Foundry Agent and Azure AI Search integration
- `app.py` - lightweight Streamlit conversational interface
- `build_azure_search_index.py` - knowledge-index preparation utility
- `screenshots/` - login, dashboard, case detail, CRM export and architecture images

## Run locally

1. Install Python 3.10 or newer and Azure CLI.
2. Create a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and enter your own Azure resource settings.
5. Sign in with `az login`.
6. Run `python system_app.py` for the full advisor workspace, or `streamlit run app.py` for the lightweight chat interface.

The application creates `advisor_system.db` automatically when the full advisor workspace starts.

## Safety and privacy

This proof of concept supports qualified advisors and does not provide autonomous diagnosis or treatment advice. Generated drafts require human review. Do not upload patient-identifiable information or production credentials.

## Portfolio note

The packaged version intentionally excludes the original `.env`, local database, virtual environment, dependency cache and logs. Azure resource names have been replaced with placeholders so the folder is safe to upload as a portfolio project.
