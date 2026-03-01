# SRE Agent - Smart Root Cause Analyser

An autonomous pipeline that receives production alerts, classifies failures, investigates root causes using logs/git/Jira, and generates comprehensive RCA reports.

## 🎯 Features

- **Intelligent Classification**: Categorizes failures into 8 types (DB, DNS, Cert, Network, Code, Config, Dependency, Memory)
- **Think-First Protocol**: Always classifies before investigating (prevents wasted tool calls)
- **Infra-First Checking**: Prioritizes infrastructure checks before code blame
- **Multi-Tool Investigation**: Integrates Loki (logs), Git (commits), and Jira (tickets)
- **Null-Safe**: Handles missing correlation IDs with intelligent fallback
- **Circuit Breakers**: Gracefully handles tool failures
- **Comprehensive Reports**: Generates JSON + Markdown RCA reports with full investigation trace

## 📊 Architecture

```
┌─────────────┐
│   Webhook   │  Receives alert (202 Accepted)
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  Orchestrator   │  Coordinates investigation
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ Classification  │  Think-first: Classify failure (8 categories)
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
│  Report Gen     │  JSON + Markdown reports
└─────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (optional)
- Loki instance
- Jira instance + API token
- Git repositories for your services

### Installation

1. **Clone the repository**

```bash
git clone <repository-url>
cd sre_agent
```

2. **Install dependencies**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Configure environment**

```bash
cp .env.example .env
# Edit .env with your configuration
```

4. **Set up git repositories**

```bash
mkdir -p repos
cd repos
git clone <your-service-repo-1>
git clone <your-service-repo-2>
cd ..
```

5. **Run the application**

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Docker Deployment

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f sre-agent

# Stop
docker-compose down
```

## 📡 API Usage

### Send Alert

```bash
curl -X POST http://localhost:8000/webhook/alert \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "rt-enricher-service",
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
  "investigation_id": "rca-rt-enricher-service-1709294130",
  "message": "Alert received. Investigation started for rt-enricher-service.",
  "app_name": "rt-enricher-service",
  "severity": "critical",
  "environment": "prod",
  "error_count": 1,
  "null_correlation_ids": 0
}
```

The investigation runs asynchronously in the background. Reports are written to `./reports/`.

### Health Check

```bash
curl http://localhost:8000/health
```

## 📋 Alert Payload Format

```json
{
  "app_name": "string",              // Required: Service name
  "alert_time": "2026-03-01T10:00:00Z",  // Required: ISO8601 timestamp
  "severity": "critical|high|medium",     // Required: Severity level
  "environment": "prod|staging",          // Required: Environment
  "errors": [                             // Required: At least 1 error
    {
      "correlation_id": "string|null",    // Optional: Can be null
      "error_message": "string"           // Required: Error message
    }
  ]
}
```

**Important**: `correlation_id` can be `null`. The system will use fingerprint-based log retrieval as fallback.

## 🔍 Failure Categories

The system classifies failures into 8 categories:

1. **DB Connectivity** - Database connection failures, pool exhaustion
2. **DNS Failure** - Name resolution failures
3. **Certificate Expiry** - SSL/TLS certificate issues
4. **Network/Intra-Service** - Service-to-service communication failures
5. **Code Logic Error** - NullPointerException, KeyError, etc.
6. **Config Drift** - Missing environment variables, wrong configuration
7. **Dependency Failure** - External service failures (Kafka, Redis, etc.)
8. **Memory/Resource Exhaustion** - OOM errors, disk full

## 📊 Investigation Pipeline

### 1. Classification (Think-First)

- Analyzes error messages using pattern matching
- Returns top 3 hypotheses with confidence percentages
- **Runs BEFORE any tool calls** (enforced by architecture)

### 2. Reasoning Loop (Infra-First)

**Priority Order:**
- **Infra categories** (DB, DNS, Cert, Network) → Check Loki logs FIRST
- **Code categories** (Code Error, Config Drift) → Check Loki, then Git, then Jira

**Stop Conditions:**
- Confidence > 85%
- All relevant tools called
- Max steps (10) reached

### 3. Tool Execution

**Loki Log Retriever:**
- Primary: Query by correlation_id
- Fallback: Query by error fingerprint (when correlation_id is null)
- Extracts: Stack traces, slow queries, key error lines

**Git Blame Checker:**
- Fetches recent commits (last 7 days)
- Extracts Jira keys from commit messages
- Flags high-churn files

**Jira Ticket Getter:**
- Fetches ticket details
- Flags risks: hotfix labels, In Progress status, missing acceptance criteria

### 4. Synthesis

- Correlates evidence from all tools
- Determines root cause with confidence level
- Builds ruled-out categories list

### 5. Report Generation

Generates two formats:

**JSON** (`reports/rca-{id}.json`): Machine-readable, complete data

**Markdown** (`reports/rca-{id}.md`): Human-readable with sections:
- Executive Summary
- Alert Details
- Hypothesis Ranking
- Root Cause
- Ruled-Out Categories
- Code Changes
- Log Evidence
- Possible Fixes (prioritized)
- Investigation Trace (full step-by-step)

## ⚙️ Configuration

All configuration via environment variables (see `.env.example`):

### Key Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LOKI_URL` | Required | Loki API base URL |
| `JIRA_URL` | Required | Jira instance URL |
| `JIRA_API_TOKEN` | Required | Jira API token |
| `GIT_REPOS_ROOT` | `./repos` | Root directory for git repositories |
| `CONFIDENCE_THRESHOLD` | `85.0` | Stop investigation when confidence exceeds this |
| `REPORT_OUTPUT_DIR` | `./reports` | Where to write RCA reports |

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test file
pytest tests/test_classifier.py -v
```

### Test Coverage

- ✅ Classification engine (20+ tests)
- ✅ All tools (25+ tests)
- ✅ Orchestrator (11+ tests)
- ✅ Webhook (12+ tests)
- ✅ Contract compliance
- ✅ Null safety verification

## 🏗️ Development

### Project Structure

```
sre_agent/
├── main.py              # FastAPI application entry point
├── config.py            # Configuration (environment variables)
├── models/              # Pydantic models
│   ├── alert.py
│   ├── hypothesis.py
│   ├── report.py
│   └── tool_result.py
├── api/                 # API endpoints
│   └── webhook.py
├── classifier/          # Failure classification
│   ├── patterns.py
│   └── engine.py
├── orchestrator/        # Investigation coordination
│   └── agent.py
├── tools/               # Investigation tools
│   ├── base.py
│   ├── loki.py
│   ├── git_blame.py
│   └── jira.py
├── reasoning/           # Decision-making and synthesis
│   ├── engine.py
│   └── synthesis.py
├── report/              # Report generation
│   ├── generator.py
│   └── fixes.py
└── tests/               # Test suite
```

### Adding a New Failure Category

1. Add to `FailureCategory` enum in `models/hypothesis.py`
2. Add patterns to `PATTERN_MAP` in `classifier/patterns.py`
3. Add tool priority in `ReasoningEngine.TOOL_PRIORITY` in `reasoning/engine.py`
4. Add fix templates in `PossibleFixesGenerator.FIX_TEMPLATES` in `report/fixes.py`

## 🔒 Security Notes

- **Never commit `.env` file** (contains API tokens)
- Use Jira API tokens (not passwords)
- Restrict webhook endpoint access (use API gateway/firewall)
- Sanitize log output (may contain sensitive data)

## 🐛 Troubleshooting

### Investigation not running

Check logs:
```bash
docker-compose logs -f sre-agent
```

Common issues:
- Loki unreachable → Check `LOKI_URL`
- Git repo not found → Ensure repos are in `GIT_REPOS_ROOT`
- Jira auth failed → Verify `JIRA_API_TOKEN`

### No reports generated

- Check `REPORT_OUTPUT_DIR` permissions
- Verify investigation completed (check logs)
- Ensure alert payload is valid (use `/webhook/alert` endpoint)

## 📈 Monitoring

The application exposes metrics at:
- `/health` - Health check
- `/webhook/health` - Webhook health
- `/docs` - Swagger UI (API documentation)

## 🤝 Contributing

Built by the autonomous engineering team:
- **Alex** - Architecture & Integration
- **Jordan** - Backend & Orchestration
- **Sam** - Tool Integration
- **Riley** - Reasoning & Reporting
- **Morgan** - Testing & QA

## 📄 License

MIT License

---

**Generated by Claude Code** 🤖
