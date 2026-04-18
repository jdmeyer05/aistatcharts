"""Create GCP Secret Manager entries from .streamlit/secrets.toml.

Run once before `gcloud run deploy`. Idempotent — re-running adds a new secret
version for any value that changed locally and skips anything unchanged.

Usage:
    python scripts/create_gcp_secrets.py

Prereqs:
    - gcloud authenticated with the target project set
    - .streamlit/secrets.toml populated with provider keys
    - SUPABASE_JWT_SECRET added to secrets.toml (or set as env var) —
      grab it from Supabase dashboard → Project Settings → API → Reveal JWT Secret
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import toml
except ImportError:
    sys.stderr.write("Missing `toml` package. Install with: pip install toml\n")
    sys.exit(1)

# ── Mapping: GCP secret name → local source ────────────────────────
# Values come from either (a) a key in .streamlit/secrets.toml, or
# (b) the current process environment, or (c) a hardcoded literal.
#
# `required=True` aborts if missing; `required=False` just warns + skips.

SECRETS: list[dict] = [
    {"gcp_name": "supabase-jwt-secret",  "source_key": "SUPABASE_JWT_SECRET",  "required": True,
     "hint": "Supabase dashboard → Project Settings → API → Reveal JWT Secret"},
    {"gcp_name": "supabase-key",         "source_key": "SUPABASE_KEY",         "required": True,
     "hint": "Supabase service_role key (not anon) → Project Settings → API"},
    {"gcp_name": "massive-api-key",      "source_key": "MASSIVE_API_KEY",      "required": True,
     "hint": "Polygon.io API key (stored as MASSIVE_API_KEY locally for legacy reasons)"},
    {"gcp_name": "fred-api-key",         "source_key": "FRED_API_KEY",         "required": True},
    {"gcp_name": "eia-api-key",          "source_key": "EIA_API_KEY",          "required": True},
    {"gcp_name": "anthropic-api-key",    "source_key": "ANTHROPIC_API_KEY",    "required": True},
    {"gcp_name": "gemini-api-key",       "source_key": "GEMINI_API_KEY",       "required": True},
    {"gcp_name": "grok-api-key",         "source_key": "GROK_API_KEY",         "required": False},
    {"gcp_name": "finnhub-api-key",      "source_key": "FINNHUB_API_KEY",      "required": False},
    {"gcp_name": "oi-capture-key",       "source_key": "OI_CAPTURE_KEY",       "required": False,
     "hint": "random token shared with Cloud Scheduler — generate via `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`"},
    {"gcp_name": "polygon-s3-access-key","source_key": "POLYGON_S3_ACCESS_KEY","required": False,
     "hint": "Polygon S3 flat files access key — unused in Cloud Run today but available for future flat-file ingest"},
    {"gcp_name": "polygon-s3-secret-key","source_key": "POLYGON_S3_SECRET_KEY","required": False,
     "hint": "Polygon S3 flat files secret key — paired with access key above"},
]


def _resolve_cmd(cmd: list[str]) -> list[str]:
    """On Windows, subprocess doesn't auto-find `.cmd`/`.exe` suffixes for argv[0].
    Use shutil.which so we invoke gcloud.cmd explicitly.
    """
    if not cmd:
        return cmd
    import shutil
    resolved = shutil.which(cmd[0])
    if resolved:
        return [resolved, *cmd[1:]]
    return cmd


def run(cmd: list[str], *, capture: bool = False, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        _resolve_cmd(cmd),
        capture_output=capture,
        input=input_text,
        text=True,
    )


def gcloud_project() -> str:
    r = run(["gcloud", "config", "get-value", "project"], capture=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write("ERROR: gcloud not configured — run `gcloud auth login` and `gcloud config set project <id>` first.\n")
        sys.exit(1)
    return r.stdout.strip()


def gcloud_sa() -> str:
    """Resolve the Compute Engine default service account — the one Cloud Run
    uses by default. Matches by email suffix since displayName varies.
    """
    r = run(
        ["gcloud", "iam", "service-accounts", "list",
         "--filter=email~-compute@developer.gserviceaccount.com",
         "--format=value(email)"],
        capture=True,
    )
    if r.returncode != 0:
        sys.stderr.write(f"ERROR: couldn't list service accounts: {r.stderr}\n")
        sys.exit(1)
    sa = (r.stdout.strip().splitlines() or [""])[0].strip()
    if sa:
        return sa

    # Fallback: derive from project number (Compute SA is always
    # <project-number>-compute@developer.gserviceaccount.com)
    rp = run(
        ["gcloud", "projects", "describe", gcloud_project(),
         "--format=value(projectNumber)"],
        capture=True,
    )
    if rp.returncode == 0 and rp.stdout.strip():
        return f"{rp.stdout.strip()}-compute@developer.gserviceaccount.com"

    sys.stderr.write("ERROR: couldn't resolve service account. Pass one explicitly or enable the default Compute SA.\n")
    sys.exit(1)


def secret_exists(name: str) -> bool:
    r = run(["gcloud", "secrets", "describe", name], capture=True)
    return r.returncode == 0


def create_or_update(name: str, value: str) -> str:
    """Create a secret or add a new version. Returns action taken."""
    if secret_exists(name):
        r = run(
            ["gcloud", "secrets", "versions", "add", name, "--data-file=-"],
            capture=True,
            input_text=value,
        )
        if r.returncode != 0:
            sys.stderr.write(f"ERROR updating {name}: {r.stderr}\n")
            sys.exit(1)
        return "updated"
    r = run(
        ["gcloud", "secrets", "create", name, "--data-file=-", "--replication-policy=automatic"],
        capture=True,
        input_text=value,
    )
    if r.returncode != 0:
        sys.stderr.write(f"ERROR creating {name}: {r.stderr}\n")
        sys.exit(1)
    return "created"


def grant_access(name: str, sa: str) -> None:
    r = run(
        ["gcloud", "secrets", "add-iam-policy-binding", name,
         f"--member=serviceAccount:{sa}",
         "--role=roles/secretmanager.secretAccessor"],
        capture=True,
    )
    # Succeeds silently (or "binding already exists"); only fail on unexpected errors.
    if r.returncode != 0 and "already exists" not in (r.stderr or ""):
        sys.stderr.write(f"WARN granting access to {name}: {r.stderr}\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    secrets_path = repo_root / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        sys.stderr.write(f"ERROR: {secrets_path} not found.\n")
        return 1

    local = toml.load(secrets_path)

    project = gcloud_project()
    sa = gcloud_sa()
    print(f"Project: {project}")
    print(f"Runtime service account: {sa}")
    print()

    missing_required: list[str] = []
    actions: list[tuple[str, str]] = []

    for spec in SECRETS:
        gcp_name = spec["gcp_name"]
        key = spec["source_key"]
        # Precedence: env var > secrets.toml. Env lets you inject JWT_SECRET
        # without committing it to the toml file.
        value = os.environ.get(key) or local.get(key)
        if not value or str(value).strip() == "":
            msg = f"  SKIP {gcp_name} — {key} not set locally"
            if spec.get("hint"):
                msg += f" ({spec['hint']})"
            print(msg)
            if spec.get("required"):
                missing_required.append(key)
            continue

        action = create_or_update(gcp_name, str(value))
        grant_access(gcp_name, sa)
        actions.append((gcp_name, action))
        print(f"  {action.upper():7s} {gcp_name}")

    print()
    if missing_required:
        print("ERROR: required secrets missing — fix these, then re-run:")
        for k in missing_required:
            print(f"  - {k}")
        return 2

    print(f"Done. {len(actions)} secret(s) in place.")
    print("You can verify with:  gcloud secrets list --filter='name~(supabase|massive|fred|eia|anthropic|gemini|grok|finnhub)'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
