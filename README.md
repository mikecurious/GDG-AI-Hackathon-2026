# County Budget Watchdog

**GDG Nairobi Agentathon 2026 · Track 04**

> Every Kenyan county publishes a budget. Almost nobody reads it. Billions leak between allocation and expenditure with no accountability.

---

## The Problem

Nairobi County manages nearly **KES 49 billion** of public money every year. That budget is published as a 400-page PDF that almost no resident has ever opened. Ward residents cannot answer basic questions like:

- "How much is actually going to my ward?"
- "Did the health budget change after supplementary estimates?"
- "Which department got the biggest allocation this year?"

The information is technically public — but practically inaccessible. This project fixes that.

---

## What It Does

The **County Budget Watchdog** is an AI agent that:

1. **Answers plain-language budget questions** — ask anything about the Nairobi FY 2025/2026 budget in English or Swahili. The agent reads the full 400-page PDF using Gemini's long context window and cites the exact page, department, and vote code.
2. **Looks up ward allocations** — query how much is budgeted for any ward, sub-county, department, or programme directly from BigQuery.
3. **Monitors gazette amendments** — checks for any supplementary estimates or reallocation notices published in the Kenya Gazette.
4. **Sends SMS budget digests** — citizens subscribe with their phone number and ward, and receive plain-language SMS summaries via Celcom Africa. The agent can also broadcast to all subscribers in a ward at once.

---

## Agent Architecture

```
Citizen
  │  (Web chat UI or direct API)
  ▼
FastAPI  ──────────────────────────────────────────────
  │
  ▼
Google ADK  LlmAgent  (Gemini 2.5 Flash)
  │
  ├── answer_budget_question(question, document)
  │       └── Uploads PDF to Gemini File API → generates grounded answer
  │
  ├── get_ward_allocation(ward_name, fiscal_year)
  │       └── Parameterised query → BigQuery  budget_line_items
  │
  ├── check_gazette_notices(topic)
  │       └── Parameterised query → BigQuery  gazette_amendments
  │
  ├── send_sms_digest(phone_number, ward)
  │       └── Fetches BQ totals → Celcom Africa SMS → logs to BigQuery  sms_digests
  │
  └── broadcast_ward_digest(ward)
          └── Fetches all active subscribers from BigQuery  subscribers
                  → Celcom Africa SMS (one per subscriber)
                  → logs each send to BigQuery  sms_digests
```

**Tools used:**
| Tool | Purpose |
|---|---|
| Google ADK | Agent orchestration, tool routing, session management |
| Gemini 2.5 Flash | Core LLM — long-context PDF reasoning + tool selection |
| Gemini File API | Uploads and caches the 400-page budget PDF for repeated queries |
| BigQuery | Stores extracted budget line items, gazette amendments, SMS digests, and subscribers |
| Cloud Run | Hosts the FastAPI application |
| Celcom Africa SMS | Sends budget digests to registered ward residents |

**BigQuery tables:**
- `budget_line_items` — structured line items extracted from PDFs by Gemini
- `gazette_amendments` — budget change notices from the Kenya Gazette
- `sms_digests` — log of every SMS sent
- `subscribers` — registered citizens (phone, ward, name)

---

## How to Run Locally

**Prerequisites:** Python 3.11+, `gcloud` CLI authenticated, a GCP project with BigQuery enabled.

```bash
# 1. Clone the repo
git clone <repo-url>
cd county-budget-watchdog

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Fill in: GOOGLE_API_KEY, CELCOM_AFRICA_API_KEY, CELCOM_AFRICA_PARTNER_ID, CELCOM_AFRICA_SHORTCODE

# 4. Authenticate with GCP (for BigQuery)
gcloud auth application-default login

# 5. Create BigQuery dataset and tables
python src/bq_schema.py

# 6. Download the Nairobi County budget PDFs (6 documents)
python src/fetch_county_budgets.py

# 7. Extract budget line items into BigQuery using Gemini
python src/pdf_to_bq.py --pdf data/raw/nairobi_itemized_estimates_2025_2026.pdf --pages 1-30

# 8. Start the agent
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the full chat UI loads immediately.

---

## How to Interact with the Deployed Version

**Live URL:** `<CLOUD_RUN_URL>`

Open the URL in any browser. The chat interface loads with suggested questions. Try:

- *"What is the total Nairobi County budget for 2025/2026?"*
- *"How much is allocated for health services?"*
- *"Which department has the highest allocation?"*
- *"Bajeti ya barabara ni ngapi?"* (Swahili: How much is the roads budget?)
- *"Check gazette amendments for supplementary estimates"*
- *"Send a budget digest to +254712345678 for Westlands ward"*

To subscribe to SMS alerts, click **Get SMS Alerts** in the top right, enter your phone number and ward, and submit. You will receive Celcom Africa SMS digests when broadcasts are triggered.

---

## Screenshots

> _Screenshots / demo video to be added after final deployment._

---

## Deployment

Deploys to Google Cloud Run via Cloud Build — no local Docker installation required.

```bash
# Export credentials from your .env first
export GOOGLE_API_KEY=...
export CELCOM_AFRICA_API_KEY=...
export CELCOM_AFRICA_PARTNER_ID=...
export CELCOM_AFRICA_SHORTCODE=...

bash deploy.sh
```

The script builds the container image with Cloud Build, deploys to Cloud Run, and prints the live URL.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | Yes | GCP project ID |
| `GCP_REGION` | Yes | Cloud Run region (e.g. `us-central1`) |
| `BQ_DATASET` | Yes | BigQuery dataset name (default: `county_budget`) |
| `GEMINI_MODEL` | Yes | Gemini model ID (default: `gemini-2.5-flash`) |
| `GOOGLE_API_KEY` | Yes | Google AI Studio API key |
| `CELCOM_AFRICA_API_KEY` | Yes | Celcom Africa SMS API key |
| `CELCOM_AFRICA_PARTNER_ID` | Yes | Celcom Africa partner ID |
| `CELCOM_AFRICA_SHORTCODE` | Yes | SMS sender name / shortcode |

---

## Budget Documents Indexed

| Document | Source |
|---|---|
| Nairobi City County Itemized Revenue & Expenditure Estimates FY 2025/2026 | Nairobi County Assembly |
| Nairobi City County Annual Development Plan FY 2026/2027 | Nairobi County Assembly |
| Nairobi County Supplementary I Estimates FY 2025/2026 | Nairobi County Assembly |
| Nairobi County Fiscal Strategy Paper FY 2025/2026 | Nairobi County Assembly |
| Nairobi County Budget Review and Outlook Paper 2025 | Nairobi County Assembly |
| National Budget Policy Statement 2026 | National Treasury |

---

## Team

| Name | Role |
|---|---|
| _[Team Member 1]_ | _[Role]_ |
| _[Team Member 2]_ | _[Role]_ |
| _[Team Member 3]_ | _[Role]_ |
| _[Team Member 4]_ | _[Role]_ |
| _[Team Member 5]_ | _[Role]_ |

**Built at GDG Nairobi Agentathon 2026** · Simba Corp, Nairobi · 16 May 2026
