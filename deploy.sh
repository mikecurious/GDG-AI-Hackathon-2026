#!/usr/bin/env bash
set -euo pipefail

# Load .env so keys are available even if caller didn't export them
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

PROJECT_ID="gdg-ai-2026-496507"
REGION="us-central1"
SERVICE_NAME="county-budget-watchdog"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "==> Building and deploying County Budget Watchdog to Cloud Run"
echo "    Project: ${PROJECT_ID}"
echo "    Region:  ${REGION}"
echo "    Service: ${SERVICE_NAME}"

# Build and push via Cloud Build (no local Docker needed)
gcloud builds submit \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}" \
  --timeout=20m \
  .

# Deploy to Cloud Run
gcloud run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --timeout=300 \
  --min-instances=1 \
  --max-instances=3 \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},BQ_DATASET=county_budget,GEMINI_MODEL=gemini-2.5-flash,GOOGLE_API_KEY=${GOOGLE_API_KEY:-},CELCOM_AFRICA_API_KEY=${CELCOM_AFRICA_API_KEY:-},CELCOM_AFRICA_PARTNER_ID=${CELCOM_AFRICA_PARTNER_ID:-},CELCOM_AFRICA_SHORTCODE=${CELCOM_AFRICA_SHORTCODE:-}"

echo ""
echo "==> Deployment complete!"
gcloud run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format="value(status.url)"
