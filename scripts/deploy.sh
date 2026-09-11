#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${GCP_PROJECT:?Set GCP_PROJECT}"
: "${CLOUD_SQL_INSTANCE:?Set CLOUD_SQL_INSTANCE to project:region:instance}"
: "${ATTACHMENT_BUCKET:?Set ATTACHMENT_BUCKET to the private GCS bucket}"
REGION="${REGION:-us-central1}"
SERVICE="cmpmanager"
RUNTIME_ACCOUNT="cmpmanager-runtime@${GCP_PROJECT}.iam.gserviceaccount.com"
# Uses dedicated secrets and account. Never auto-detect an existing service.
gcloud run deploy "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" \
  --source . --service-account "$RUNTIME_ACCOUNT" \
  --add-cloudsql-instances "$CLOUD_SQL_INSTANCE" \
  --update-secrets 'DATABASE_URL=cmpmanager-database-url:latest,ADMIN_INITIAL_PASSWORD=cmpmanager-admin-password:latest' \
  --update-env-vars "FINANCE_MEMBERS=true,ATTACHMENT_BUCKET=$ATTACHMENT_BUCKET" \
  --allow-unauthenticated --min-instances 0 --max-instances 2 \
  --memory 512Mi --cpu 1 --concurrency 20 --timeout 180 --quiet
URL="$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')"
curl --fail --silent --show-error "$URL/api/healthz"
echo ""
echo "Deployed: $URL"
