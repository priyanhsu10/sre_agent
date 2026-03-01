# SRE Agent - Smart Root Cause Analyser

An autonomous pipeline that receives production alerts, classifies failures, investigates root causes using logs/git/Jira, and generates comprehensive RCA reports with a live web dashboard.

## Features

- **LLM-Enhanced Classification**: Hybrid AI + rule-based analysis for novel errors
- **LLM-Enhanced Synthesis**: AI-powered root cause analysis with intelligent evidence correlation
- **8 Failure Categories**: DB, DNS, Certificate, Network, Code, Config, Dependency, Memory
- **Think-First Protocol**: Always classifies before investigating (prevents wasted tool calls)
- **Multi-Tool Investigation**: Loki (logs), Git (commits), Jira (tickets)
- **Null-Safe**: Handles missing correlation IDs with intelligent fingerprint fallback
- **Live Dashboard**: Web UI with charts, filters, auto-refresh, and rich RCA report modals
- **DB Persistence**: All reports saved to SQLite, queryable via REST API
- **Custom LLM Provider**: Supports Anthropic, OpenAI, or any internal self-hosted model
- **SSL Bypass**: Works in internal environments with self-signed certificates

## Architecture

```
┌─────────────┐
│   Webhook   │  POST /webhook/alert → 202 Accepted
└──────┬──────┘
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
- Git repositories for your services

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
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000
```

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

Investigation runs in the background (~5-10s). Report is saved to DB and `./reports/`.

### Dashboard

```
http://localhost:8000/dashboard-ui
```

- Overview stats + charts (severity donut, category bar)
- Filter by app, severity, environment, category, time range
- Search by app name
- Click any report to view full RCA details (root cause, evidence, fixes, trace)
- Auto-refreshes every 30 seconds

### Dashboard API

```bash
# List reports
GET /dashboard/reports?limit=10&offset=0&severity=critical&environment=prod

# Get single report
GET /dashboard/reports/{report_id}

# Stats
GET /dashboard/stats
```

### Other Endpoints

```bash
GET /health          # App health
GET /webhook/health  # Webhook health
GET /docs            # Swagger UI
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

## LLM Configuration

Supports Anthropic, OpenAI, or any internal self-hosted model (vLLM, Ollama, TGI, etc.).

```bash
# .env
LLM_ENABLED=true

# Anthropic (Claude)
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-3-5-sonnet-20241022

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

Works without LLM (pattern-only mode) when `LLM_ENABLED=false`.

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LOKI_URL` | Required | Loki API base URL |
| `JIRA_URL` | Required | Jira instance URL |
| `JIRA_USERNAME` | Required | Jira username |
| `JIRA_API_TOKEN` | Required | Jira API token |
| `GIT_REPOS_ROOT` | `./repos` | Root directory for git repositories |
| `REPORT_OUTPUT_DIR` | `./reports` | Directory for JSON/MD report files |
| `CONFIDENCE_THRESHOLD` | `85.0` | Stop investigation when confidence exceeds this |
| `LLM_ENABLED` | `false` | Enable LLM-enhanced analysis |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `custom` |
| `LLM_API_KEY` | — | API key / bearer token |
| `LLM_BASE_URL` | — | Base URL for custom/internal LLM |
| `LLM_MODEL` | `claude-3-5-sonnet-20241022` | Model name |

## Project Structure

```
sre_agent/
├── main.py              # FastAPI app + dashboard route
├── config.py            # All settings (env vars)
├── models/              # Pydantic models
│   ├── alert.py
│   ├── hypothesis.py
│   ├── report.py
│   └── tool_result.py
├── api/
│   ├── webhook.py       # POST /webhook/alert
│   └── dashboard.py     # Dashboard REST API
├── classifier/
│   ├── patterns.py      # Pattern rules for 8 categories
│   ├── engine.py        # Pattern-based classifier
│   └── llm_classifier.py # LLM-enhanced classifier
├── orchestrator/
│   └── agent.py         # Pipeline coordinator
├── tools/
│   ├── loki.py          # Log retrieval
│   ├── git_blame.py     # Commit analysis
│   └── jira.py          # Ticket fetcher
├── reasoning/
│   ├── engine.py        # Tool selection logic
│   ├── synthesis.py     # Rule-based evidence synthesis
│   └── llm_synthesis.py # LLM-powered synthesis
├── report/
│   ├── generator.py     # JSON + MD report writer + DB save
│   └── fixes.py         # Prioritised fix suggestions
├── llm/
│   ├── client.py        # LLM client (Anthropic/OpenAI/Custom)
│   └── prompts.py       # LLM prompt templates
├── database/
│   ├── models.py        # SQLAlchemy ORM models
│   └── service.py       # DB read/write service
├── reports/
│   └── dashboard.html   # Dashboard web UI
└── tests/               # 58 tests
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=. --cov-report=html
```

**Test coverage: 58 tests across classifier, tools, orchestrator, and webhook.**

## Security Notes

- Never commit `.env` (contains API tokens)
- Use Jira API tokens, not passwords
- Restrict `/webhook/alert` access via API gateway or firewall
- Logs may contain sensitive error messages — sanitize as needed

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Loki unreachable | Check `LOKI_URL`, verify network access |
| Jira auth failed | Verify `JIRA_USERNAME` and `JIRA_API_TOKEN` |
| Git repo not found | Clone service repos into `GIT_REPOS_ROOT` |
| SSL errors | Set `ssl=False` is already applied to all HTTP calls |
| Dashboard empty | Run `python seed_reports.py` to populate with sample data |
| Reports not in DB | Check server logs for `saved to database` confirmation |

---

**Built with Claude Code**
