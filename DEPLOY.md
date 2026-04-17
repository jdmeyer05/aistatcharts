# Deployment Runbook — Next.js site on Vercel + FastAPI on Cloud Run

Production goal: `https://aistatcharts.com` serves the **Next.js** app, which calls a **FastAPI** service deployed on Cloud Run. The Streamlit app stays on its existing Cloud Run service as a backup until we're confident in the cutover.

This doc is the step-by-step playbook. Run commands from the repo root on your local machine.

---

## 0. Prerequisites (one-time)

- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- `docker` available locally
- Vercel CLI or dashboard access
- Supabase user `jdmeyer05@gmail.com` exists with a password
- You know these values:
  - **Google Cloud project ID** (`gcloud config get-value project`)
- **APIs enabled on the project** (one-time). Cloud Run, Cloud Build, Artifact Registry, and **Secret Manager** — this last one is easy to miss and will make the deploy silently accept but not attach your `--set-secrets` flags:
  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
  ```
  - **Cloud Run region** — we use `us-east1`
  - **Supabase JWT secret** — from Supabase dashboard → Project Settings → API → Reveal JWT Secret
  - **Supabase URL + anon key** — already in `frontend/.env.production`
  - API keys: `POLYGON_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GOOGLE_API_KEY`

---

## 1. Deploy the FastAPI service to Cloud Run

### 1a. Create the secrets (one-time, run FIRST — deploy references these)

The env-var names below match what `src/api_keys.py` and `src/db.py` actually read — not the conventional provider names. Keep them exact.

| GCP secret name      | Contents                                   | Where to get it                               |
|----------------------|--------------------------------------------|-----------------------------------------------|
| supabase-url         | `https://diyhmmpegkxlwwhmqkyo.supabase.co` | already known                                 |
| supabase-key         | Supabase **service_role** key              | Supabase dashboard → Project Settings → API   |
| supabase-jwt-secret  | Supabase JWT signing secret                | Supabase dashboard → Project Settings → API → "Reveal JWT Secret" |
| massive-api-key      | Polygon.io API key                         | `.streamlit/secrets.toml` → `MASSIVE_API_KEY` |
| fred-api-key         | FRED key                                   | same file → `FRED_API_KEY`                    |
| eia-api-key          | EIA key                                    | same file → `EIA_API_KEY`                     |
| anthropic-api-key    | Anthropic / Claude key                     | same file → `ANTHROPIC_API_KEY`               |
| gemini-api-key       | Google / Gemini key                        | same file → `GEMINI_API_KEY`                  |
| grok-api-key         | xAI / Grok key                             | same file → `GROK_API_KEY`                    |
| finnhub-api-key      | Finnhub key *(optional)*                   | same file → `FINNHUB_API_KEY`                 |

To push them all without copy-pasting real values into your shell, use the helper script in this repo:

```bash
python scripts/create_gcp_secrets.py
```

That script reads values from `.streamlit/secrets.toml`, creates each secret in GCP Secret Manager, and grants the Compute Engine default service account read access. Safe to re-run — it'll skip secrets that already exist.

If you'd rather do it manually, the equivalent individual commands look like:

```bash
printf '%s' 'YOUR_SUPABASE_JWT_SECRET' | gcloud secrets create supabase-jwt-secret --data-file=-
printf '%s' 'YOUR_SUPABASE_KEY'        | gcloud secrets create supabase-key         --data-file=-
printf '%s' 'YOUR_MASSIVE_KEY'         | gcloud secrets create massive-api-key      --data-file=-
# ...etc for each provider key
```

Then grant the Cloud Run runtime SA access:
```bash
export SA="$(gcloud iam service-accounts list --filter='displayName~Compute' --format='value(email)' | head -1)"
for s in supabase-jwt-secret supabase-key massive-api-key fred-api-key eia-api-key anthropic-api-key gemini-api-key grok-api-key finnhub-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
done
```

### 1b. Build + push the image

You can build either locally with Docker, or remotely with Cloud Build. **Remote build is simpler** — no local Docker required, matches how Streamlit was deployed — so that's the default path here.

```bash
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/aistatcharts-api:latest"

# One-time: create the Artifact Registry repo (skip if already present)
gcloud artifacts repositories create cloud-run-source-deploy \
  --repository-format=docker --location=${REGION} \
  --description="Cloud Run source deploys" || true

# Build on Google's servers (respects .dockerignore; 3–5 min first time)
gcloud builds submit --tag=${IMAGE} --file=Dockerfile.api .
```

<details><summary>Alternative — local Docker build</summary>

```bash
docker build -f Dockerfile.api -t ${IMAGE} .
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet
docker push ${IMAGE}
```

Requires local Docker Desktop + CPU virtualization enabled in BIOS. Not worth the trouble unless you're iterating quickly.
</details>

### 1c. Deploy the service

The `aistatcharts.com` + `www.aistatcharts.com` origins are already in the code's CORS default list, so we don't need `CORS_ALLOWED_ORIGINS` here. (If you *do* need to add more origins, see the note after this block — comma-in-value needs the `^|^` delimiter.)

```bash
gcloud run deploy aistatcharts-api \
  --image=${IMAGE} \
  --region=${REGION} \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=0 \
  --max-instances=10 \
  --timeout=180 \
  --concurrency=40 \
  --set-env-vars="SUPABASE_URL=https://diyhmmpegkxlwwhmqkyo.supabase.co,ADMIN_EMAILS=jdmeyer05@gmail.com" \
  --set-secrets="SUPABASE_JWT_SECRET=supabase-jwt-secret:latest,SUPABASE_KEY=supabase-key:latest,MASSIVE_API_KEY=massive-api-key:latest,FRED_API_KEY=fred-api-key:latest,EIA_API_KEY=eia-api-key:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,GROK_API_KEY=grok-api-key:latest,FINNHUB_API_KEY=finnhub-api-key:latest"
```

> **`--set-env-vars` uses `,` as the pair separator.** If any env value must contain a comma (e.g. a custom `CORS_ALLOWED_ORIGINS` with multiple origins), use `gcloud`'s alternate-delimiter syntax: prefix with `^|^` and use `|` as the separator:
> ```
> --set-env-vars "^|^CORS_ALLOWED_ORIGINS=https://foo.com,https://bar.com|OTHER=value"
> ```

> **Do not set `LOCAL_DEV=true` in production.** It disables the admin gate on `/api/positions/robinhood`, `/api/market/holding-deep-dive`, and `/api/market/trade-architect`.

### 1d. Verify the service is live

```bash
export API_URL=$(gcloud run services describe aistatcharts-api --region=${REGION} --format='value(status.url)')
echo "API URL: ${API_URL}"

# Expect 200 + {"status": "ok", ...}
curl -sS "${API_URL}/api/health"

# Expect 403 (fail-closed — anon user is not in ADMIN_EMAILS)
curl -sS -o /dev/null -w '%{http_code}\n' "${API_URL}/api/positions/robinhood"

# Expect 200
curl -sS -o /dev/null -w '%{http_code}\n' "${API_URL}/api/sectors/configs"
```

If any of those fails, check `gcloud run services logs read aistatcharts-api --region=${REGION}`.

---

## 2. Point Vercel at the API

### 2a. Update `NEXT_PUBLIC_API_URL` on Vercel

The URL printed in step 1d is the value. Set it via Vercel dashboard:

- Vercel → project → Settings → Environment Variables
- Add `NEXT_PUBLIC_API_URL` = the Cloud Run URL (e.g. `https://aistatcharts-api-abc123-uc.a.run.app`)
- Scope: Production + Preview + Development
- Redeploy production so the new env var is embedded into the static JS bundle.

Also confirm these are set (they already should be):
- `NEXT_PUBLIC_SUPABASE_URL=https://diyhmmpegkxlwwhmqkyo.supabase.co`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY=<the anon key from .env.production>`

### 2b. Unpause the Vercel project

Dashboard → project → Settings → General → Unpause Project.

### 2c. Trigger a redeploy

```bash
git commit --allow-empty -m "Trigger prod rebuild with NEXT_PUBLIC_API_URL"
git push
```

(Or use the "Redeploy" button on the latest production deployment.)

### 2d. Verify the Vercel deployment

- Open the Vercel preview URL (e.g. `https://aistatcharts.vercel.app`)
- Sign in with Supabase credentials
- DevTools → Network: API requests should go to `${NEXT_PUBLIC_API_URL}` and carry `Authorization: Bearer <jwt>`
- Load `/sector-analysis`, click **Load XLE Data** — should populate
- Load `/smart-money`, switch to **Macro & Rates** tab, click **Load Macro Data** — should show Fed Funds / 10Y / etc.

---

## 3. DNS cutover

Currently `aistatcharts.com` points at the Streamlit Cloud Run service. Switch to Vercel.

### 3a. In Vercel

- Settings → Domains → Add `aistatcharts.com` (and `www.aistatcharts.com`)
- Vercel will show the required A/CNAME records

### 3b. At your DNS provider

- Update the apex `A` record (or `ALIAS`/`ANAME` if supported) to point at Vercel's IP
- Set `www` as a `CNAME` to `cname.vercel-dns.com`

Propagation takes minutes–hours. Check with `dig aistatcharts.com +short`.

### 3c. Verify TLS

Vercel auto-provisions Let's Encrypt certificates. After propagation:

```bash
curl -sS -I https://aistatcharts.com | head -5
# Expect HTTP/2 200 and a Vercel server header
```

---

## 4. Post-cutover checks

Do all of these from the real domain, signed in as `jdmeyer05@gmail.com`:

- [ ] `/login` works (redirects to `/` on success)
- [ ] `/sector-analysis` loads sector configs and returns financials
- [ ] `/smart-money` — all 6 tabs load, including 13F (auth-gated endpoints returning proper data)
- [ ] `/vol-landscape` — "Scan Market" runs end-to-end
- [ ] `/position-monitor` (admin) — Robinhood positions load (this tests the admin JWT round-trip)
- [ ] `/meta-analysis` — backtest runs, not a CORS error
- [ ] Open DevTools while signed out — hitting `/api/positions/robinhood` should return 403

---

## 5. Decommission Streamlit (optional, after 1–2 weeks of stability)

```bash
# Keep the image for rollback — just stop traffic
gcloud run services update-traffic aistatcharts \
  --region=${REGION} --to-revisions=LATEST=0

# Or fully remove
gcloud run services delete aistatcharts --region=${REGION}
```

---

## 6. Rollback plan

If the Next.js cutover breaks something:

1. **DNS revert** — change the apex/CNAME back to Streamlit's Cloud Run URL. Propagation ~5 min.
2. **API revert** — if only the FastAPI service is broken, roll back to the prior revision:
   ```bash
   gcloud run services update-traffic aistatcharts-api \
     --region=${REGION} \
     --to-revisions=PRIOR_REVISION_NAME=100
   ```
   List revisions with `gcloud run revisions list --service=aistatcharts-api --region=${REGION}`.
3. **Vercel revert** — on the dashboard, click "Rollback" on the deployment list to ship the previous commit.

---

## 7. Troubleshooting cheatsheet

| Symptom | First thing to check |
|---|---|
| 403 on admin endpoint when signed in as admin | `ADMIN_EMAILS` env matches your Supabase email (case-insensitive); `SUPABASE_JWT_SECRET` matches the one Supabase uses to sign tokens. Tail Cloud Run logs. |
| 503 on admin endpoint | `ADMIN_EMAILS` env not set on Cloud Run. Fail-closed behavior. |
| CORS error in browser | Origin not in `CORS_ALLOWED_ORIGINS` and not matched by `CORS_ALLOWED_ORIGIN_REGEX`. Add it and redeploy. |
| `/api/health` returns `database: unavailable` | `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` env missing or wrong. |
| Slow cold start | First request after scale-to-zero loads heavy deps (scipy/sklearn). Raise `min-instances=1` if persistent. |
| Ticker data empty | Check `POLYGON_API_KEY` env; yfinance rate-limits also show as empty. |
| JWT audience errors in logs | Supabase tokens must use `aud: "authenticated"` — our decoder requires it. If you customized Supabase, align. |
