# Build + deploy the FastAPI Cloud Run service with all env vars + secrets.
# Safe to re-run.
#
# The build step is NOT skippable: deploying `:latest` without rebuilding
# reuses whatever image was last pushed, so local code changes silently
# never reach prod. Use -SkipBuild only when redeploying to swap env vars
# or secrets without a code change (e.g., rotating an API key).

param(
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$PROJECT_ID = (gcloud config get-value project).Trim()
$REGION = "us-east1"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/aistatcharts-api:latest"

Write-Host "Project: $PROJECT_ID"
Write-Host "Image:   $IMAGE"

if (-not $SkipBuild) {
  Write-Host ""
  Write-Host "Building image via Cloud Build (3-5 min)..."
  & gcloud builds submit --config=cloudbuild.api.yaml --substitutions=_IMAGE=$IMAGE .
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
} else {
  Write-Host "Skipping build (-SkipBuild flag); deploying existing :latest image."
}

$envVars = "SUPABASE_URL=https://diyhmmpegkxlwwhmqkyo.supabase.co,ADMIN_EMAILS=jdmeyer05@gmail.com"

$secretPairs = @(
  "SUPABASE_JWT_SECRET=supabase-jwt-secret:latest",
  "SUPABASE_KEY=supabase-key:latest",
  "SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest",
  "MASSIVE_API_KEY=massive-api-key:latest",
  "FRED_API_KEY=fred-api-key:latest",
  "EIA_API_KEY=eia-api-key:latest",
  "ANTHROPIC_API_KEY=anthropic-api-key:latest",
  "GEMINI_API_KEY=gemini-api-key:latest",
  "GROK_API_KEY=grok-api-key:latest",
  "FINNHUB_API_KEY=finnhub-api-key:latest"
  # OI_CAPTURE_KEY removed 2026-04-18: the `oi-capture-key` secret was
  # pre-registered here for the OI history worker, but the activation
  # steps in OI_WORKER_SETUP.md (create secret + grant SA access) were
  # never completed. Leaving it in blocked every deploy with a
  # "secret not found" error. Add the line back when activating the
  # worker — see project_oi_worker_pending memory.
)
$secrets = $secretPairs -join ","

$deployArgs = @(
  "run", "deploy", "aistatcharts-api",
  "--image=$IMAGE",
  "--region=$REGION",
  "--platform=managed",
  "--allow-unauthenticated",
  "--port=8080",
  "--memory=2Gi",
  "--cpu=2",
  "--min-instances=0",
  "--max-instances=10",
  "--timeout=540",
  "--concurrency=40",
  "--set-env-vars=$envVars",
  "--set-secrets=$secrets"
)

& gcloud @deployArgs

if ($LASTEXITCODE -ne 0) {
  Write-Error "Deploy failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Verifying..."
$SERVICE_URL = (gcloud run services describe aistatcharts-api --region=$REGION --format='value(status.url)').Trim()
Write-Host "Service URL: $SERVICE_URL"

try {
  $resp = Invoke-WebRequest -Uri "$SERVICE_URL/api/health" -UseBasicParsing -TimeoutSec 30
  Write-Host "Health: $($resp.StatusCode) - $($resp.Content)"
} catch {
  Write-Warning "Health check failed: $_"
}
