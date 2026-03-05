# SRE Agent - Smart Root Cause Analyser

An autonomous pipeline that receives production alerts, classifies failures, investigates root causes using logs/git/Jira, generates comprehensive RCA reports, and automatically applies code fixes with a live web dashboard.

## Features

- **Alert Deduplication**: SHA-256 content fingerprinting suppresses duplicate alerts within a configurable window
- **LLM-Enhanced Classification**: Hybrid AI + rule-based analysis for novel errors
- **LLM-Enhanced Synthesis**: AI-powered root cause analysis with intelligent evidence correlation
- **8 Failure Categories**: DB, DNS, Certificate, Network, Code, Config, Dependency, Memory
- **Think-First Protocol**: Always classifies before investigating (prevents wasted tool calls)
- **Multi-Tool Investigation**: Loki (logs), Git (commits), Jira (tickets)
- **Automated Code Fix**: Detects runtime, creates branch, applies git revert or Claude agent patch, runs tests, pushes branch
- **Claude Agentic Loop**: Multi-turn tool-use loop for high-quality code patches (read → fix → test → iterate)
- **Graceful Fallback**: When Claude unavailable, creates `FIX_INSTRUCTIONS.md` on branch with full manual steps
- **Live Dashboard**: Web UI with charts, filters, RCA modals, and one-click "Apply Fix" button
- **DB Persistence**: All reports saved to SQLite, queryable via REST API
- **Custom LLM Provider**: Supports Anthropic, OpenAI, or any internal self-hosted model

## Architecture

```
┌─────────────┐
│   Webhook   │  POST /webhook/alert → 202 Accepted
└──────┬──────┘
       │ dedup check → suppress if duplicate
       │ background task
       ▼
┌─────────────────┐
│  Orchestrator   │  Coordinates pipeline
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ Classification  │  Think-first: Pattern match + LLM (8 categories)
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ Reasoning Loop  │  Decide which tools to call
└──────┬──────────┘
       │
       ├─► Loki (logs)     ─┐
       ├─► Git (commits)    ├─► Evidence gathering
       └─► Jira (tickets)  ─┘
       │
       ▼
┌─────────────────┐
│   Synthesis     │  Correlate evidence, determine root cause
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  Report Gen     │  JSON + Markdown files + SQLite DB
└──────┬──────────┘
       │ auto-trigger if code fix identified
       ▼
┌─────────────────────────────────────┐
│  Remediation Pipeline               │
│  1. Detect runtime (Python/Java/JS) │
│  2. Create fix branch               │
│  3. Apply revert or Claude patch    │
│  4. Run tests                       │
│  5. Push branch                     │
└──────┬──────────────────────────────┘
       │
       ▼
┌─────────────────┐
│   Dashboard     │  Live web UI at /dashboard-ui
└─────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- Loki instance
- Jira instance + API token
- Git repositories for your services (cloned into `GIT_REPOS_ROOT`)

### Installation

```bash
git clone <repository-url>
cd sre_agent

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your configuration
```

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — redirects to the dashboard automatically.

### Docker

```bash
docker-compose up -d
docker-compose logs -f sre-agent
```

## API Usage

### Send Alert

```bash
curl -X POST http://localhost:8000/webhook/alert \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "payment-service",
    "alert_time": "2026-03-01T10:15:30Z",
    "severity": "critical",
    "environment": "prod",
    "errors": [
      {
        "correlation_id": "abc-123",
        "error_message": "Connection refused to database"
      }
    ]
  }'
```

**Response (202 Accepted):**
```json
{
  "status": "accepted",
  "investigation_id": "rca-payment-service-1709294130",
  "message": "Alert received. Investigation started for payment-service.",
  "app_name": "payment-service",
  "severity": "critical",
  "environment": "prod",
  "error_count": 1,
  "null_correlation_ids": 0
}
```

**Duplicate alert response (200):**
```json
{
  "status": "duplicate",
  "investigation_id": "rca-payment-service-1709294130",
  "message": "Duplicate alert detected. Investigation already in_progress.",
  "existing_status": "in_progress"
}
```

Investigation runs in the background (~5-10s). Report is saved to DB and `./reports/`.

### Dashboard

```
http://localhost:8000
```

- Overview stats + charts (severity donut, category bar)
- Filter by app, severity, environment, category, time range
- Click any report to view full RCA details (root cause, evidence, fixes, trace)
- **"Apply Automated Fix"** button in the Developer Action Plan section for fixable reports
- Auto-refreshes every 30 seconds

### Trigger Fix Manually

```bash
# Trigger remediation for an existing report
POST /remediation/{report_id}

# Poll status
GET /remediation/{report_id}
```

**Status values:** `pending` → `branch_created` → `fix_applied` → `tests_running` → `tests_passed` → `pushed`

### Dashboard API

```bash
GET /dashboard/reports?limit=10&offset=0&severity=critical&environment=prod
GET /dashboard/reports/{report_id}
GET /dashboard/stats
```

### Other Endpoints

```bash
GET /             # Redirects to dashboard
GET /health       # App health
GET /webhook/health  # Webhook health + dedup stats
GET /docs         # Swagger UI
```

## Alert Payload

```json
{
  "app_name": "string",                  // Required
  "alert_time": "2026-03-01T10:00:00Z", // Required: ISO8601
  "severity": "critical|high|medium",    // Required
  "environment": "prod|staging",         // Required
  "errors": [
    {
      "correlation_id": "string|null",   // Optional: null triggers fingerprint fallback
      "error_message": "string"          // Required
    }
  ]
}
```

## Failure Categories

| Category | Examples |
|----------|---------|
| `db_connectivity` | Connection refused, pool exhausted, postgres errors |
| `dns_failure` | Name resolution failed, NXDOMAIN |
| `certificate_expiry` | SSL certificate expired, TLS handshake failure |
| `network_intra_service` | 502/503, connection timeout between services |
| `code_logic_error` | NullPointerException, KeyError, AttributeError |
| `config_drift` | Missing env var, wrong config value |
| `dependency_failure` | Kafka, Redis, S3 unreachable |
| `memory_resource_exhaustion` | OOMKilled, heap full, disk full |

## Alert Deduplication

Same alert firing repeatedly is suppressed automatically.

**Fingerprint** = SHA-256 of `app_name + environment + sorted(error_messages)` — `alert_time` is excluded so retries with new timestamps are still caught.

| State | Behaviour |
|-------|-----------|
| `in_progress` | Always suppressed — returns existing investigation ID |
| `completed` | Suppressed within `DEDUP_WINDOW_MINUTES` (default 30 min) |
| `failed` | Allowed through — retry is permitted |

## Automated Code Fix (Remediation)

Triggered automatically after RCA when a code fix is identified, or manually via `POST /remediation/{report_id}`.

### Fix Types

| Condition | Fix Type | What Happens |
|-----------|----------|-------------|
| `is_code_change=true` + Priority 1 fix says "Revert commit..." | `revert` | `git revert {commit_hash}`, run tests, push |
| `root_cause_category=code_logic_error` + Claude available | `claude_agent_patch` | Multi-turn Claude loop reads files, writes fix, runs tests, iterates |
| `root_cause_category=code_logic_error` + Claude unavailable | `manual_instructions` | Creates `FIX_INSTRUCTIONS.md` on branch with full RCA context |
| Other categories | `none` | No automated fix |

### Runtime Auto-Detection

The agent detects how to run tests from the repo contents:

| File present | Runtime | Test command |
|---|---|---|
| `pom.xml` + `mvnw` | spring_boot | `./mvnw test -B` |
| `build.gradle` + `gradlew` | spring_boot | `./gradlew test` |
| `package.json` | react | `npm test -- --watchAll=false` |
| `requirements.txt` / `pytest.ini` | python | `python -m pytest` |
| fallback | unknown | `make test` |

### Claude Agentic Loop

When `fix_type=claude_agent_patch`, Claude runs a multi-turn conversation with tools:

```
Iteration 1:  Claude → calls read_file("src/order_validator.py")
              You    → return file contents
Iteration 2:  Claude → calls write_file("src/order_validator.py", fixed_code)
              You    → write file, return "OK"
Iteration 3:  Claude → calls run_tests()
              You    → run pytest, return output
Iteration 4:  If tests pass → Claude stops
              If tests fail → Claude reads failure, refines fix, repeats
Max 5 iterations.
```

## LLM Configuration

Supports Anthropic, OpenAI, or any internal self-hosted model (vLLM, Ollama, TGI, etc.).

```bash
# .env

# Anthropic (Claude) — used for both classification and code fix agent
LLM_ENABLED=true
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6

# OpenAI
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Custom / Internal (OpenAI-compatible endpoint)
LLM_PROVIDER=custom
LLM_BASE_URL=http://internal-llm.company.com/v1
LLM_API_KEY=your-bearer-token
LLM_MODEL=your-model-name
```

Works without LLM (`LLM_ENABLED=false`) — falls back to pattern-only classification and manual fix instructions.

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LOKI_URL` | `http://localhost:3100` | Loki API base URL |
| `JIRA_URL` | Required | Jira instance URL |
| `JIRA_USERNAME` | Required | Jira username |
| `JIRA_API_TOKEN` | Required | Jira API token |
| `GIT_REPOS_ROOT` | `./repos` | Root dir for app git repositories |
| `REPORT_OUTPUT_DIR` | `./reports` | Directory for JSON/MD report files |
| `CONFIDENCE_THRESHOLD` | `85.0` | Stop investigation when confidence exceeds this |
| `DEDUP_WINDOW_MINUTES` | `30` | Suppress duplicate alerts within this window |
| `LLM_ENABLED` | `false` | Enable LLM-enhanced analysis |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `custom` |
| `LLM_API_KEY` | — | API key / bearer token |
| `LLM_MODEL` | `claude-sonnet-4-6` | Model name |
| `LLM_CONFIDENCE_THRESHOLD` | `40.0` | Use LLM only if pattern confidence < this |
| `AUTO_REMEDIATION_ENABLED` | `true` | Auto-trigger fix after RCA |
| `AUTO_REMEDIATION_MIN_CONFIDENCE` | `High` | Min confidence to auto-remediate |
| `REMEDIATION_BRANCH_PREFIX` | `fix/rca` | Git branch prefix for fix branches |
| `REMEDIATION_REMOTE` | `origin` | Git remote to push fix branches to |
| `REMEDIATION_TEST_TIMEOUT_SECONDS` | `300` | Max seconds to wait for tests |
| `REMEDIATION_MAX_FIX_ITERATIONS` | `5` | Max Claude agent iterations for code patch |

## Project Structure

```
sre_agent/
├── main.py                  # FastAPI app entry point
├── config.py                # All settings (env vars)
├── models/
│   ├── alert.py             # AlertPayload, ErrorEntry
│   ├── hypothesis.py        # FailureCategory, ConfidenceLevel
│   ├── report.py            # RCAReport, PossibleFix, CodeChange
│   ├── tool_result.py       # ToolResult
│   └── remediation.py       # RemediationResult, RemediationStatus
├── api/
│   ├── webhook.py           # POST /webhook/alert (with dedup)
│   ├── dedup.py             # Alert deduplication (fingerprint + registry)
│   ├── dashboard.py         # Dashboard REST API
│   └── remediation.py       # POST/GET /remediation/{report_id}
├── classifier/
│   ├── patterns.py          # Pattern rules for 8 categories
│   ├── engine.py            # Pattern-based classifier
│   └── llm_classifier.py    # LLM fallback classifier
├── orchestrator/
│   └── agent.py             # Pipeline coordinator + auto-remediation trigger
├── tools/
│   ├── loki.py              # Log retrieval
│   ├── git_blame.py         # Commit analysis
│   └── jira.py              # Ticket fetcher
├── reasoning/
│   ├── engine.py            # Tool selection logic
│   ├── synthesis.py         # Rule-based evidence synthesis
│   └── llm_synthesis.py     # LLM-powered synthesis
├── remediation/
│   ├── agent.py             # RemediationAgent — 8-step fix pipeline
│   ├── capability.py        # Claude availability check (LLM_ENABLED + API key + DNS)
│   ├── branch_manager.py    # git branch create/push
│   ├── fix_applier.py       # Apply revert, Claude patch, or manual instructions
│   ├── code_fix_agent.py    # Multi-turn Claude agentic loop with tool use
│   └── test_runner.py       # Runtime detection + test execution
├── report/
│   ├── generator.py         # JSON + MD report writer + DB save
│   └── fixes.py             # Prioritised fix suggestions
├── llm/
│   ├── client.py            # LLM client (Anthropic/OpenAI/Custom/Mock)
│   └── prompts.py           # LLM prompt templates
├── database/
│   ├── models.py            # SQLAlchemy ORM models
│   └── service.py           # DB read/write service
├── reports/
│   └── dashboard.html       # Dashboard web UI (single-file)
└── tests/                   # 91 tests
    ├── test_classifier.py
    ├── test_tools.py
    ├── test_orchestrator.py
    ├── test_webhook.py
    ├── test_dedup.py         # 16 dedup tests
    └── test_remediation.py  # 17 remediation tests
```

## Testing

```bash
# Run all tests
venv/bin/python -m pytest tests/ -v

# With coverage
venv/bin/python -m pytest tests/ --cov=. --cov-report=html
```

**91 tests** across classifier, tools, orchestrator, webhook, dedup, and remediation.

## Security Notes

- Never commit `.env` (contains API tokens)
- Use Jira API tokens, not passwords
- Restrict `/webhook/alert` access via API gateway or firewall
- The Claude fix agent uses an allowlist for tool execution — only the configured test command can be run (no arbitrary shell)
- Path traversal is prevented in the code fix agent's file read/write tools

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Loki unreachable | Check `LOKI_URL`, verify network access |
| Jira auth failed | Verify `JIRA_USERNAME` and `JIRA_API_TOKEN` |
| Git repo not found | Clone service repos into `GIT_REPOS_ROOT/{app_name}` |
| Fix branch not created | Ensure repo exists at `GIT_REPOS_ROOT/{app_name}` |
| Tests not detected | Check repo has `requirements.txt`, `pom.xml`, or `package.json` |
| Claude not patching | Set `LLM_ENABLED=true` and `LLM_API_KEY` in `.env` |
| SSL errors | `ssl=False` is already applied to all HTTP calls |
| Dashboard empty | Run `python seed_reports.py` to populate with sample data |
| Duplicate alert not suppressed | Check `DEDUP_WINDOW_MINUTES` and `/webhook/health` for dedup stats |

---

**Built by Priyanshu Parate**
