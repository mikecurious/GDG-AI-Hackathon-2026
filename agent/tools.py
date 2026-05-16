"""Tool implementations for the County Budget Watchdog agent."""
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from google import genai
from google.cloud import bigquery

load_dotenv()
log = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "gdg-ai-2026-496507")
DATASET_ID = os.getenv("BQ_DATASET", "county_budget")
LOCATION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

CELCOM_API_KEY = os.getenv("CELCOM_AFRICA_API_KEY", "")
CELCOM_PARTNER_ID = os.getenv("CELCOM_AFRICA_PARTNER_ID", "")
CELCOM_SHORTCODE = os.getenv("CELCOM_AFRICA_SHORTCODE", "WATCHDOG")
CELCOM_SMS_ENDPOINT = "https://isms.celcomafrica.com/api/services/sendsms/"

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
# Cloud Run stores PDFs under /tmp since the container fs is ephemeral
TMP_RAW_DIR = Path("/tmp/data/raw")
DEFAULT_PDF = "nairobi_itemized_estimates_2025_2026.pdf"


def send_quick_sms(phone: str, message: str) -> bool:
    """Send a raw SMS via Celcom. Returns True on success. For internal use."""
    if not (CELCOM_API_KEY and CELCOM_PARTNER_ID):
        log.info("[DEMO] SMS to %s: %s", phone, message[:80])
        return False
    mobile = _format_phone(phone)
    payload = {
        "apikey": CELCOM_API_KEY,
        "partnerID": CELCOM_PARTNER_ID,
        "message": quote(message[:160]),
        "shortcode": CELCOM_SHORTCODE,
        "mobile": mobile,
    }
    try:
        resp = requests.post(CELCOM_SMS_ENDPOINT, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        responses = data.get("responses", [])
        ok = bool(responses and responses[0].get("response-code") == 200)
        if ok:
            log.info("Quick SMS sent to %s", mobile)
        return ok
    except Exception as exc:
        log.error("Quick SMS failed to %s: %s", mobile, exc)
        return False

_genai_client: genai.Client | None = None
_bq_client: bigquery.Client | None = None
_file_uri_cache: dict[str, str] = {}


def _get_genai() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        if GOOGLE_API_KEY:
            _genai_client = genai.Client(api_key=GOOGLE_API_KEY)
        else:
            _genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
    return _genai_client


def _get_bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _format_phone(phone: str) -> str:
    """Normalize phone to Celcom format: 254XXXXXXXXX."""
    cleaned = "".join(c for c in phone if c.isdigit())
    if cleaned.startswith("254"):
        return cleaned
    if cleaned.startswith("0"):
        return "254" + cleaned[1:]
    if len(cleaned) == 9:
        return "254" + cleaned
    return cleaned


def _get_or_upload_pdf(filename: str = DEFAULT_PDF) -> str:
    """Upload PDF to Gemini File API and cache the URI."""
    if filename in _file_uri_cache:
        return _file_uri_cache[filename]

    # Check local data/raw/, then /tmp (Cloud Run)
    pdf_path = RAW_DIR / filename
    if not pdf_path.exists():
        pdf_path = TMP_RAW_DIR / filename
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Budget PDF not available on this instance. "
            f"The agent will answer from BigQuery data only."
        )

    client = _get_genai()
    log.info("Uploading %s to Gemini File API ...", filename)
    with open(pdf_path, "rb") as f:
        uploaded = client.files.upload(
            file=f,
            config={"display_name": filename, "mime_type": "application/pdf"},
        )
    uri = uploaded.uri
    _file_uri_cache[filename] = uri
    log.info("Uploaded %s -> %s", filename, uri)
    return uri


def answer_budget_question(question: str, document: str = DEFAULT_PDF) -> str:
    """
    Answer a plain-language question about the Nairobi County budget.

    Uses Gemini 1.5 Pro's long context window to reason over the full PDF.

    Args:
        question: The citizen's question in English or Swahili.
        document: Which budget document to query (default: itemized estimates).

    Returns:
        Plain-language answer grounded in the budget document.
    """
    try:
        file_uri = _get_or_upload_pdf(document)
    except FileNotFoundError as exc:
        return f"Budget document not available yet: {exc}"

    client = _get_genai()
    system_prompt = (
        "You are a transparent, plain-language Kenyan county budget analyst. "
        "Answer citizens' questions about the Nairobi City County budget clearly and honestly. "
        "Always cite the specific department, programme, vote code, or page number where the figure appears. "
        "Convert large KES figures to millions (e.g., KES 450,000,000 → KES 450M). "
        "If information is not in the document, say so explicitly. "
        "Keep answers under 200 words. Respond in the same language as the question."
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            {
                "parts": [
                    {"text": system_prompt + "\n\nQuestion: " + question},
                    {"file_data": {"mime_type": "application/pdf", "file_uri": file_uri}},
                ]
            }
        ],
    )
    return response.text


def get_ward_allocation(ward_name: str, fiscal_year: str = "2025/2026") -> str:
    """
    Query BigQuery for budget allocations related to a specific ward or sub-county.

    Args:
        ward_name: The ward or sub-county name (e.g., "Westlands", "Embakasi").
        fiscal_year: Fiscal year string (default: 2025/2026).

    Returns:
        Summary of allocations for that ward/area.
    """
    bq = _get_bq()
    query = f"""
        SELECT
            department,
            programme,
            sub_programme,
            item_description,
            SUM(amount_approved) AS total_approved,
            COUNT(*) AS line_items
        FROM `{PROJECT_ID}.{DATASET_ID}.budget_line_items`
        WHERE fiscal_year = @fiscal_year
          AND (
            LOWER(department) LIKE LOWER(@ward)
            OR LOWER(programme) LIKE LOWER(@ward)
            OR LOWER(sub_programme) LIKE LOWER(@ward)
            OR LOWER(vote_name) LIKE LOWER(@ward)
            OR LOWER(item_description) LIKE LOWER(@ward)
          )
        GROUP BY 1, 2, 3, 4
        ORDER BY total_approved DESC
        LIMIT 20
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fiscal_year", "STRING", fiscal_year),
            bigquery.ScalarQueryParameter("ward", "STRING", f"%{ward_name}%"),
        ]
    )
    try:
        rows = list(bq.query(query, job_config=job_config).result())
        if not rows:
            return (
                f"No specific line items found for '{ward_name}' in {fiscal_year}. "
                "The budget may aggregate this area under a broader department. "
                "Try asking the budget question tool instead."
            )

        total = sum(r.total_approved or 0 for r in rows)
        lines = [f"Total allocation related to '{ward_name}': KES {total/1e6:.1f}M\n"]
        for r in rows[:5]:
            amt = (r.total_approved or 0) / 1e6
            lines.append(f"• {r.programme or r.department}: KES {amt:.1f}M ({r.line_items} line items)")
        return "\n".join(lines)
    except Exception as exc:
        log.error("BigQuery error: %s", exc)
        return f"Could not query ward allocation: {exc}. Ensure BigQuery data is loaded (run src/pdf_to_bq.py)."


def check_gazette_notices(topic: str, limit: int = 5) -> str:
    """
    Check BigQuery for gazette amendments related to a topic.

    Args:
        topic: Topic to search for (e.g., "supplementary", "health", "roads").
        limit: Max number of notices to return.

    Returns:
        Summary of relevant gazette notices.
    """
    bq = _get_bq()
    query = f"""
        SELECT
            gazette_number,
            gazette_date,
            notice_title,
            amendment_type,
            affected_vote_code,
            original_amount,
            revised_amount,
            change_amount,
            source_url
        FROM `{PROJECT_ID}.{DATASET_ID}.gazette_amendments`
        WHERE (
            LOWER(notice_title) LIKE LOWER(@topic)
            OR LOWER(amendment_type) LIKE LOWER(@topic)
        )
        ORDER BY gazette_date DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("topic", "STRING", f"%{topic}%"),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    try:
        rows = list(bq.query(query, job_config=job_config).result())
        if not rows:
            return (
                f"No gazette amendments found for '{topic}'. "
                "The gazette monitor may need to run (src/monitor_gazette.py) "
                "or no amendments have been issued yet."
            )

        lines = [f"Found {len(rows)} gazette notice(s) for '{topic}':\n"]
        for r in rows:
            change = (r.change_amount or 0) / 1e6
            sign = "+" if change >= 0 else ""
            lines.append(
                f"• Gazette {r.gazette_number} ({r.gazette_date}): {r.notice_title}\n"
                f"  Type: {r.amendment_type}  Change: {sign}KES {change:.1f}M\n"
                f"  Ref: {r.source_url or 'N/A'}"
            )
        return "\n".join(lines)
    except Exception as exc:
        log.error("BigQuery gazette error: %s", exc)
        return f"Could not query gazette notices: {exc}"


def send_sms_digest(phone_number: str, ward: str, fiscal_year: str = "2025/2026") -> str:
    """
    Generate and send an SMS budget digest for a ward via Celcom Africa.

    Args:
        phone_number: Recipient phone (e.g., 0712345678 or +254712345678).
        ward: Ward name for the digest.
        fiscal_year: Fiscal year.

    Returns:
        Confirmation message with delivery status.
    """
    bq = _get_bq()
    query = f"""
        SELECT
            SUM(amount_approved) AS total_approved,
            COUNT(DISTINCT department) AS dept_count
        FROM `{PROJECT_ID}.{DATASET_ID}.budget_line_items`
        WHERE fiscal_year = @fiscal_year
          AND county = 'Nairobi'
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("fiscal_year", "STRING", fiscal_year)]
    )
    try:
        rows = list(bq.query(query, job_config=job_config).result())
        total = (rows[0].total_approved or 0) / 1e9 if rows else 0
        depts = rows[0].dept_count if rows else 0
        msg = (
            f"Nairobi Budget {fiscal_year}: KES {total:.1f}B total. "
            f"{depts} depts. Ward: {ward}. "
            f"Reply WARD <name> for details. CountyWatchdog"
        )
    except Exception:
        msg = (
            f"Nairobi Budget {fiscal_year}. "
            f"Ward: {ward}. Text BUDGET for details. CountyWatchdog"
        )

    msg = msg[:160]
    digest_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    sent = False
    status = "not sent"

    if CELCOM_API_KEY and CELCOM_PARTNER_ID:
        try:
            mobile = _format_phone(phone_number)
            payload = {
                "apikey": CELCOM_API_KEY,
                "partnerID": CELCOM_PARTNER_ID,
                "message": quote(msg),
                "shortcode": CELCOM_SHORTCODE,
                "mobile": mobile,
            }
            resp = requests.post(CELCOM_SMS_ENDPOINT, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            responses = data.get("responses", [])
            if responses and responses[0].get("response-code") == 200:
                sent = True
                status = f"Delivered (msgid={responses[0].get('messageid')})"
                log.info("SMS sent to %s via Celcom: %s", mobile, status)
            else:
                code = responses[0].get("response-code") if responses else "unknown"
                status = f"Celcom error code {code}"
                log.error("Celcom SMS failed: %s", data)
        except Exception as exc:
            log.error("SMS send failed: %s", exc)
            status = f"SMS error: {exc}"
    else:
        status = f"[DEMO] SMS would be sent to {phone_number}: '{msg}'"
        log.info(status)

    digest_row = {
        "digest_id": digest_id,
        "county": "Nairobi",
        "ward": ward,
        "fiscal_year": fiscal_year,
        "digest_type": "ward_summary",
        "message_text": msg,
        "generated_at": now.isoformat(),
        "sent": sent,
        "sent_at": now.isoformat() if sent else None,
    }
    try:
        bq.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.sms_digests", [digest_row])
    except Exception as exc:
        log.warning("Could not log digest to BQ: %s", exc)

    return f"SMS digest for {ward}: '{msg}'\nStatus: {status}"


def _send_single_sms(mobile: str, msg: str) -> bool:
    """Send one SMS via Celcom. Returns True on success."""
    payload = {
        "apikey": CELCOM_API_KEY,
        "partnerID": CELCOM_PARTNER_ID,
        "message": quote(msg),
        "shortcode": CELCOM_SHORTCODE,
        "mobile": mobile,
    }
    resp = requests.post(CELCOM_SMS_ENDPOINT, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    responses = data.get("responses", [])
    return bool(responses and responses[0].get("response-code") == 200)


def broadcast_ward_digest(ward: str, fiscal_year: str = "2025/2026") -> str:
    """
    Send a budget digest SMS to all active subscribers in a ward.

    Args:
        ward: The ward name to broadcast to.
        fiscal_year: Fiscal year for the digest.

    Returns:
        Broadcast summary with sent/failed counts.
    """
    bq = _get_bq()

    sub_query = f"""
        SELECT phone, name
        FROM `{PROJECT_ID}.{DATASET_ID}.subscribers`
        WHERE LOWER(ward) = LOWER(@ward)
          AND county = 'Nairobi'
          AND active = TRUE
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ward", "STRING", ward)]
    )
    try:
        subscribers = list(bq.query(sub_query, job_config=job_config).result())
    except Exception as exc:
        return f"Could not fetch subscribers: {exc}"

    if not subscribers:
        return f"No active subscribers found for {ward}. Share the subscription link so residents can sign up."

    budget_query = f"""
        SELECT SUM(amount_approved) AS total, COUNT(DISTINCT department) AS depts
        FROM `{PROJECT_ID}.{DATASET_ID}.budget_line_items`
        WHERE fiscal_year = @fy AND county = 'Nairobi'
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("fy", "STRING", fiscal_year)]
    )
    try:
        row = list(bq.query(budget_query, job_config=cfg).result())[0]
        total = (row.total or 0) / 1e9
        depts = row.depts or 0
        msg = (
            f"Nairobi Budget {fiscal_year}: KES {total:.1f}B total across {depts} depts. "
            f"Ward: {ward}. Reply STOP to unsubscribe. CountyWatchdog"
        )
    except Exception:
        msg = f"Nairobi Budget {fiscal_year} digest for {ward}. CountyWatchdog"

    msg = msg[:160]
    sent_count = 0
    failed_count = 0
    now = datetime.now(timezone.utc)

    for sub in subscribers:
        mobile = _format_phone(sub.phone)
        try:
            if CELCOM_API_KEY and CELCOM_PARTNER_ID:
                ok = _send_single_sms(mobile, msg)
                if ok:
                    sent_count += 1
                else:
                    failed_count += 1
            else:
                log.info("[DEMO] Would send to %s: %s", mobile, msg)
                sent_count += 1
        except Exception as exc:
            log.error("Failed to send to %s: %s", mobile, exc)
            failed_count += 1

        digest_row = {
            "digest_id": str(uuid.uuid4()),
            "county": "Nairobi",
            "ward": ward,
            "fiscal_year": fiscal_year,
            "digest_type": "broadcast",
            "message_text": msg,
            "generated_at": now.isoformat(),
            "sent": sent_count > 0,
            "sent_at": now.isoformat() if sent_count > 0 else None,
        }
        try:
            bq.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.sms_digests", [digest_row])
        except Exception:
            pass

    return (
        f"Broadcast to {ward} complete: {sent_count} sent, {failed_count} failed "
        f"out of {len(subscribers)} subscribers."
    )
