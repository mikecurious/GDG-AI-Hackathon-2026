# County Budget Watchdog

> GDG Nairobi Agentathon 2026 · Track 04

An AI agent that turns Nairobi County's 400-page budget PDF into plain-language answers for ward residents. Monitor gazette amendments, query ward allocations, and receive SMS budget digests.

## The Problem

Every Kenyan county publishes a budget. Almost nobody reads it. Billions leak between allocation and expenditure with no accountability. Citizens have no easy way to ask "how much is going to my ward?" or "did anything change after supplementary estimates?"

## Agent Architecture

```
Citizen (Web UI / SMS)
        │
        ▼
FastAPI (main.py)
        │
        ▼
Google ADK LlmAgent  ──── Gemini 1.5 Pro (long context)
        │
        ├── answer_budget_question()  →  Gemini File API (full 400-page PDF)
        ├── get_ward_allocation()     →  BigQuery (budget_line_items table)
        ├── check_gazette_notices()   →  BigQuery (gazette_amendments table)
        └── send_sms_digest()         →  Africa's Talking SMS API
                                              └── logs to BigQuery (sms_digests)
```

**Stack:** Google ADK · Gemini 1.5 Pro · Vertex AI · BigQuery · Cloud Run · Africa's Talking

## Data Pipeline

```
src/fetch_county_budgets.py     # Downloads 6 Nairobi County PDFs
src/scrape_assembly_papers.py   # Scrapes assembly papers-laid page
src/monitor_gazette.py          # Monitors Kenya Law Gazette for amendments
src/analyze_pdf_structure.py    # Determines OCR vs form-parser strategy
src/pdf_to_bq.py                # Gemini-powered extraction → BigQuery
src/bq_schema.py                # Creates BigQuery dataset and tables
```

## How to Run Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env with your GCP project ID, AT API keys, etc.

# 3. Authenticate with GCP
gcloud auth application-default login

# 4. Set up BigQuery tables
python src/bq_schema.py

# 5. Download budget PDFs
python src/fetch_county_budgets.py

# 6. Extract data into BigQuery (takes ~30 min for full PDF)
python src/pdf_to_bq.py --pdf data/raw/nairobi_itemized_estimates_2025_2026.pdf --pages 1-50

# 7. Start the agent server
uvicorn main:app --reload --port 8000
# Open http://localhost:8000
```

## How to Interact with the Deployed Version

Visit the Cloud Run URL and use the chat interface. Try:

- "How much is the total Nairobi County budget for 2025/2026?"
- "What is allocated for health services?"
- "How much goes to roads?"
- "Check gazette amendments for supplementary estimates"
- "Send an SMS digest to +254712345678 for Westlands ward"

## Deployment

```bash
bash deploy.sh
```

Deploys to Cloud Run via Google Cloud Build (no local Docker required).

## Environment Variables

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_REGION` | Cloud Run region |
| `BQ_DATASET` | BigQuery dataset name |
| `GEMINI_MODEL` | Gemini model ID |
| `AT_API_KEY` | Africa's Talking API key |
| `AT_USERNAME` | Africa's Talking username |

## Budget Documents Used

- Nairobi City County Itemized Revenue & Expenditure Estimates FY 2025/2026
- Nairobi City County Annual Development Plan FY 2026/2027
- Nairobi County Supplementary I Estimates FY 2025/2026
- Nairobi County Fiscal Strategy Paper FY 2025/2026
- Nairobi County Budget Review and Outlook Paper 2025
- National Budget Policy Statement 2026

## Team

GDG Nairobi Agentathon 2026
