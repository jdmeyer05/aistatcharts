# OI History Worker — Setup

One-time setup for the daily OI capture job. After this runs, the Options Intelligence "OI Changes" tab populates itself day by day.

## 1. Apply Supabase schema

Open the Supabase SQL editor (dashboard → SQL Editor → New query), paste the contents of `supabase_oi_history_schema.sql`, and click **Run**. Creates two tables:
- `options_oi_history` — per-contract OI rows, one per ticker/date
- `options_oi_universe` — daily top-200 ranking by total OI

## 2. Generate capture key

The scheduler authenticates via a shared header. Generate a random token and store it both locally and in GCP.

```bash
# Generate key (run once, save the output)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add to `.streamlit/secrets.toml` locally:
```toml
OI_CAPTURE_KEY = "<paste_token>"
```

Add to `scripts/create_gcp_secrets.py` SECRETS list, then:
```bash
python scripts/create_gcp_secrets.py
```

Add to `scripts/deploy_api.ps1` `$secretPairs` as `"OI_CAPTURE_KEY=oi-capture-key:latest"` and redeploy.

## 3. One-off first-run (optional)

Verify the capture endpoint works before scheduling. From local:

```powershell
# Mint a local JWT for admin calls and hit the endpoint
python -c "
import toml, jwt, time, requests, json
s = toml.load('.streamlit/secrets.toml')
tok = jwt.encode({'sub':'local','email':'jdmeyer05@gmail.com','aud':'authenticated','exp':int(time.time())+3600},
                 s['SUPABASE_JWT_SECRET'], algorithm='HS256')
r = requests.post('http://localhost:8000/api/market/admin/oi-snapshot',
                  headers={'Authorization': f'Bearer {tok}'},
                  json={'tickers': ['SPY','QQQ','IWM','DIA'], 'top_n': 4},
                  timeout=120)
print(json.dumps(r.json(), indent=2))
"
```

Expected output: `rows_written > 0`, `top_5` list populated.

## 4. Create Cloud Scheduler job

One-time command after the first prod deploy with `OI_CAPTURE_KEY` wired in:

```bash
# Capture the key for header substitution
KEY=$(gcloud secrets versions access latest --secret=oi-capture-key)
SERVICE_URL=$(gcloud run services describe aistatcharts-api --region=us-east1 --format='value(status.url)')

gcloud scheduler jobs create http oi-snapshot-daily \
  --location=us-east1 \
  --schedule="30 16 * * 1-5" \
  --time-zone="America/New_York" \
  --uri="$SERVICE_URL/api/market/admin/oi-snapshot" \
  --http-method=POST \
  --headers="Content-Type=application/json,X-Capture-Key=$KEY" \
  --message-body='{"top_n": 200, "min_oi": 10}' \
  --attempt-deadline=540s
```

Runs at 4:30 PM ET Monday–Friday. 540s deadline gives room for 500+ candidate tickers.

## 5. Smoke-test the job manually

```bash
gcloud scheduler jobs run oi-snapshot-daily --location=us-east1
# Watch the service logs for the capture
gcloud run services logs read aistatcharts-api --region=us-east1 --limit=50 | grep -i "oi-snapshot\|rows_written"
```

## Expected storage footprint

- ~80k rows/day (200 tickers × ~200 contracts each, OI≥10 filter)
- ~8 MB/day, ~250 MB/month, ~3 GB/year
- Supabase free tier (500 MB) fills in ~2 months — plan on Pro ($25/mo, 8 GB)
  or migrate cold data to GCS Parquet later.

## Monitoring

Check the `options_oi_universe` table after the first capture:

```sql
SELECT capture_date, COUNT(*) AS n_tickers, MAX(total_oi) AS top_oi
FROM options_oi_universe
GROUP BY capture_date ORDER BY capture_date DESC LIMIT 10;
```
