# County Budget Watchdog

The County Budget Watchdog is an agentic application designed to parse massive (400+ page) county budget PDFs and provide transparent, plain-language answers to citizens. It utilizes Gemini 1.5 Pro's long context window to understand entire documents simultaneously and maps out actionable tools to run queries and send digests.

## User Review Required

> [!IMPORTANT]
> You mentioned using **only Google tools**. Google does not have a native SMS gateway (like Twilio or Africa's Talking). 
> For the `send_sms_digest` tool, should we:
> 1. Keep **Africa's Talking** for the actual SMS delivery (since it's the standard in Kenya)?
> 2. Replace the tool to send emails via a Google Workspace/Gmail API instead?
> 3. Just mock/simulate the SMS sending for the scope of the hackathon?

## Proposed Architecture & Changes

We will orchestrate the architecture primarily using Google's ecosystem. The application will be a lightweight Python FastAPI backend, containerized via Docker for easy deployment to Google Cloud Run. 

### Where the AI Components Plug In:
1.  **Vertex AI Agent Builder:** We will use Vertex AI Agent Builder as the orchestration layer for the agent (instead of building a custom python while-loop from scratch). It will handle routing intent, managing conversation state, and calling the defined Tools.
2.  **Google ADK (Agent Development Kit):** We will use the Google Agent Development Kit or Google GenAI SDK to programmatically define the Tools functions (`get_ward_allocation`, `send_sms_digest`, etc.) in python and bridge them to the Vertex AI Agent.
3.  **Gemini Vision (Gemini 1.5 Pro natively):** Real county budgets often contain scanned pages and poorly formatted image charts. When we pass the 400-page PDF to the File API, the multimodal Gemini Vision naturally kicks in allowing the model to understand the visual charts and tables directly from the visual layout, bypassing the need for a separate text-extraction pipeline.

### Core Application Components (FastAPI)

#### [NEW] `main.py`
The FastAPI application entry point. It will serve as the webhook/integration interface bridging incoming requests (like a chat UI or SMS incoming webhook) to the deployed Vertex AI Agent.

#### [NEW] `agent/`
Directory containing the Vertex/ADK setup code.
*   **`adk_setup.py`:** Uses the Google GenAI SDK/ADK to define and deploy the tool specifications to your Vertex Agent.
*   **`tools.py`:** The actual python execution logic for the tools that the Agent will call via function calling:
    *   `answer_budget_question(question, file_uri)` -> (Direct Gemini Vision call against the PDF)
    *   `get_ward_allocation(ward_name)` -> (Queries BigQuery)
    *   `check_gazette_notices(topic)`
    *   `send_sms_digest(phone, ward)` 

### Infrastructure & Deployment (Google Cloud)

#### [NEW] `Dockerfile`
A standard Python Dockerfile to containerize the FastAPI application.

#### [NEW] `requirements.txt`
Dependencies including `fastapi`, `uvicorn`, `google-genai`, `google-cloud-bigquery`.

#### [NEW] `deploy.sh`
A helper script containing the `gcloud run deploy` command to quickly push updates to Google Cloud Run.

## Open Questions

1.  **SMS Gateway:** As mentioned in the review section, what is the plan for the SMS API given the "Google tools only" constraint?
2.  **PDF Sources:** Do you have a sample County Budget PDF downloaded that we can use for initial testing and prompt engineering?
3.  **BigQuery Setup:** Should I scaffold a python script to help schema setup and data ingestion into BigQuery, or will you handle the initial extraction of tables into BigQuery separately (e.g., via Document AI)?

## Verification Plan

### Automated/Local Tests
*   Run the FastAPI server locally using `uvicorn`.
*   Trigger the `upload-budget` script to verify the Google AI File API successfully returns a File URI.
*   Run sample queries against the agent to verify tool selection (e.g., ensuring it decides to call `get_ward_allocation` when asked about a specific ward).

### Manual Verification
*   Test the full flow via Swagger UI (`http://localhost:8000/docs`).
*   Deploy to Cloud Run via the CLI and verify public endpoint access.
