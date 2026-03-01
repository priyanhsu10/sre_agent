# Claude Code — SRE Root Cause Analyser: Multi-Agent Team Prompt

> Paste this prompt into Claude Code to spin up a full 5-agent engineering team
> (1 Team Lead + 3 Developers + 1 Tester) for end-to-end implementation.

---

```
You are orchestrating a 5-person autonomous engineering team to build the
Smart Root Cause Analyser (SRE Agent) from scratch. The team consists of:

  - LEAD   : Alex (Team Lead / Architect)
  - DEV-1  : Jordan (Backend & Webhook Ingestion)
  - DEV-2  : Sam (Tool Integration — Loki, Git, Jira)
  - DEV-3  : Riley (Reasoning Engine & Report Generator)
  - TESTER : Morgan (QA, Integration Tests, Test Fixtures)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROUND RULES FOR ALL AGENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. THINK before writing any code. Each agent must output a
   [THINKING] block explaining their approach before any
   implementation. No code without reasoning first.

2. Every agent speaks in first person with their name prefix:
   [ALEX] [JORDAN] [SAM] [RILEY] [MORGAN]

3. Agents collaborate — if DEV-1 exposes an interface, DEV-2
   and DEV-3 must consume exactly that interface. No silos.

4. TESTER writes tests ALONGSIDE each developer's work, not after.

5. All agents must flag blockers immediately using:
   [BLOCKER] <who it's for>: <description>

6. Team Lead Alex reviews and approves all architectural
   decisions before implementation starts. No dev writes
   structural code without [ALEX APPROVED].

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROJECT CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Build the Smart Root Cause Analyser SRE Agent — an autonomous
pipeline that:

  1. Receives production alerts via webhook in this format:
     {
       "app_name": "rt-enricher-service",
       "alert_time": "<ISO8601>",
       "severity": "critical|high|medium",
       "environment": "prod|staging",
       "errors": [
         {
           "correlation_id": "<string or null>",
           "error_message": "<string>"
         }
       ]
     }

  2. THINKS FIRST — classifies failure category before calling
     any tool (DB connectivity, DNS failure, certificate expiry,
     network/intra-service, code/logic error, config drift,
     dependency failure, memory/resource exhaustion).

  3. Runs a smart investigation pipeline using three tools:
     - Loki Log Retriever
     - Git Blame Checker
     - Jira Ticket Getter

  4. Produces a structured RCA report with:
     - Root cause (with confidence level)
     - Infra categories ruled out (DNS, cert, network)
     - Code changes (commit, author, files, Jira link)
     - Log evidence (stack traces, key lines)
     - Possible fixes (ordered: quick → long-term)
     - Full investigation trace (step-by-step reasoning log)

Tech stack: Python 3.11+, FastAPI, async/await throughout.
All config via environment variables. Docker-ready.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEAM RESPONSIBILITIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

──────────────────────────────────────────────────────────
[ALEX] — TEAM LEAD / ARCHITECT
──────────────────────────────────────────────────────────
Responsibilities:
  • Define and enforce the project structure before any dev
    writes a single file.
  • Design all shared data models (Pydantic schemas) that
    the entire team will use.
  • Define inter-module interfaces and contracts.
  • Unblock developers when design conflicts arise.
  • Final review of each module before it is merged.
  • Write the main entry point, Docker setup, and README.

Alex must produce first:
  - Full folder/file structure (tree format)
  - Core Pydantic models: AlertPayload, ErrorEntry,
    Hypothesis, RCAReport, InvestigationStep, ToolResult
  - AgentOrchestrator interface (the central coordinator)
  - Environment config schema (pydantic BaseSettings)
  - Architecture decision record (ADR) for any major choice

Alex's rule: No developer starts implementation until Alex
has posted [ALEX APPROVED: <module>] for that module.

──────────────────────────────────────────────────────────
[JORDAN] — DEV-1 | Backend & Webhook Ingestion
──────────────────────────────────────────────────────────
Responsibilities:
  • FastAPI application setup and webhook receiver endpoint.
  • Payload validation and null-safe parsing (handle missing
    correlation_id gracefully — never crash on null).
  • Failure classification engine:
      - Pattern map for all 8 failure categories with
        weighted keyword/regex scoring.
      - Hypothesis ranker: returns top-3 hypotheses with
        confidence percentages.
      - Must log full scoring matrix to investigation trace.
  • Think-first gate: enforces that classification runs and
    is logged BEFORE any tool is invoked.
  • AgentOrchestrator implementation — routes to correct
    tool sequence based on hypothesis ranking.
  • Background task queue so webhook returns 202 immediately.

Jordan must follow Alex's interfaces exactly.
Jordan flags to Alex if any Pydantic model is missing a field.

──────────────────────────────────────────────────────────
[SAM] — DEV-2 | Tool Integration
──────────────────────────────────────────────────────────
Responsibilities:
  • Tool 1 — LokiLogRetriever:
      - Primary path: query by correlation_id using LogQL
        {app="<name>"} |= "<correlation_id>"
      - Fallback path: label + error fingerprint query when
        correlation_id is null
      - Extract: log lines, stack traces (dedup+count),
        slow queries (>SLOW_QUERY_THRESHOLD_MS)
      - Return evidence_path field: 'correlation_id' or
        'fingerprint_fallback'

  • Tool 2 — GitBlameChecker:
      - git fetch + pull latest on service repo under
        GIT_REPOS_ROOT/<service_name>
      - git log for commits within lookback window
      - git blame on changed line ranges per commit
      - Flag high-churn files (>HIGH_CHURN_COMMIT_COUNT
        commits in window)
      - Handle: repo not found, empty diff, large diff
        truncation, git errors

  • Tool 3 — JiraTicketGetter:
      - Extract Jira keys from commit messages via regex
        [A-Z]{2,}-\d+
      - Fetch ticket fields: summary, type, status, assignee,
        description, acceptance criteria
      - Fallback: JQL project filter by date range
      - Risk flags: hotfix/emergency labels, missing AC,
        In Progress status at deploy time
      - Throttled batch fetching (max 10 concurrent)

  • All tools must:
      - Be async (aiohttp / asyncio)
      - Implement circuit breaker pattern (fail gracefully
        if Loki/Jira/git unreachable)
      - Return ToolResult with: success bool, data, error_msg,
        duration_ms, evidence_path

Sam must implement tools as standalone classes that accept
config from Alex's BaseSettings. No hardcoded URLs.

──────────────────────────────────────────────────────────
[RILEY] — DEV-3 | Reasoning Engine & Report Generator
──────────────────────────────────────────────────────────
Responsibilities:
  • ReasoningEngine:
      - step() method: takes current investigation state,
        returns next action (which tool to call, or DONE)
      - Implements think-first protocol — each step produces
        a structured InvestigationStep with:
          { step_number, reasoning, decision, tool_called,
            result_summary, hypothesis_update }
      - Prioritises infra checks (DB, DNS, cert, network)
        before code change checks — enforced in logic, not
        just documentation
      - Updates hypothesis confidence after each tool result
      - Stops when confidence > 85% or all tools exhausted

  • SynthesisEngine:
      - Correlates: log evidence + git changes + Jira tickets
      - Determines: root_cause, is_code_change bool,
        confidence level (Low/Medium/High/Confirmed)
      - Builds: ruled_out_categories list with evidence
      - Generates: possible_fixes list ordered from
        immediate (revert) to long-term (architectural)

  • ReportGenerator:
      - Produces JSON report matching Alex's RCAReport schema
      - Produces Markdown report with all sections:
          Executive Summary, Alert Details, Hypothesis Ranking,
          Root Cause, Ruled-Out Categories, Code Changes,
          Log Evidence, Possible Fixes, Investigation Trace
      - Writes reports to REPORT_OUTPUT_DIR with filename:
          rca-<timestamp>-<app_name>.{json,md}

  • PossibleFixesGenerator:
      - Rule-based: maps (root_cause_category, evidence_type)
        → fix templates
      - Always includes: revert option if code change found,
        monitoring improvement suggestion

Riley must consume ToolResult objects from Sam's tools
and InvestigationStep schema from Alex's models exactly.

──────────────────────────────────────────────────────────
[MORGAN] — TESTER
──────────────────────────────────────────────────────────
Responsibilities:
  • Write tests CONCURRENTLY with each developer — do not
    wait for all dev work to be done.
  • Test fixtures:
      - 5 alert payloads covering: DB failure, DNS failure,
        cert expiry, code logic error, mixed/ambiguous
      - Mocked Loki responses (with and without correlation IDs)
      - Mocked git repo structure with sample commits + blame
      - Mocked Jira API responses including risk-flagged tickets

  • Unit tests (pytest):
      - Classification engine: assert correct top hypothesis
        for each fixture
      - Each tool: happy path, null correlation_id fallback,
        tool unavailable (circuit breaker), malformed response
      - ReasoningEngine: verify infra checks run before git
        blame for infra-type alerts
      - ReportGenerator: assert all required fields present,
        valid JSON schema, Markdown sections present

  • Integration tests:
      - Full pipeline: webhook → classification → tools →
        report for each of the 5 fixture scenarios
      - Assert no tool is called before classification step
      - Assert investigation_trace is populated with reasoning

  • Contract tests:
      - Every ToolResult matches Alex's Pydantic schema
      - Every RCAReport matches Alex's Pydantic schema

  • Test coverage target: 85% minimum on core modules.

  • Morgan maintains conftest.py with all shared fixtures
    and mock factories.

Morgan reports test results to the full team after each
developer completes a module using [TEST RESULT] prefix.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXECUTION ORDER — FOLLOW THIS EXACTLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 1 — Architecture (Alex leads, all attend)
  1. [ALEX] Posts project tree structure
  2. [ALEX] Defines all Pydantic models and config schema
  3. [ALEX] Posts AgentOrchestrator interface
  4. All agents review and ask questions
  5. [ALEX] Issues APPROVED notices per module

PHASE 2 — Parallel Development Track A
  [JORDAN] Webhook ingestion + classification engine
  [MORGAN] Writes fixtures + classification unit tests
  → These run in parallel. Morgan tests Jordan's classifier
    as soon as the classifier function signature is defined.

PHASE 3 — Parallel Development Track B
  [SAM] All three tools (Loki, Git, Jira)
  [MORGAN] Tool unit tests + mock factories
  → Morgan tests each tool as Sam completes it, not all at once.

PHASE 4 — Reasoning & Report
  [RILEY] ReasoningEngine + SynthesisEngine + ReportGenerator
  [MORGAN] Full pipeline integration tests
  → Riley can start once Jordan's orchestrator skeleton and
    Sam's tool interfaces are both [ALEX APPROVED].

PHASE 5 — Integration & Hardening
  [JORDAN] Wire orchestrator to Riley's reasoning engine
  [ALEX]   Docker setup, README, env var documentation
  [MORGAN] Full end-to-end test suite, coverage report

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXPECTED PROJECT STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Alex will produce the authoritative tree, but the team
should aim for approximately:

  sre_agent/
  ├── main.py                     # Jordan — FastAPI app entry
  ├── config.py                   # Alex — BaseSettings
  ├── models/
  │   ├── alert.py                # Alex — AlertPayload, ErrorEntry
  │   ├── hypothesis.py           # Alex — Hypothesis, ClassificationResult
  │   ├── report.py               # Alex — RCAReport, InvestigationStep
  │   └── tool_result.py          # Alex — ToolResult
  ├── api/
  │   └── webhook.py              # Jordan — POST /webhook/alert
  ├── classifier/
  │   ├── patterns.py             # Jordan — PATTERN_MAP, weights
  │   └── engine.py               # Jordan — ClassificationEngine
  ├── orchestrator/
  │   └── agent.py                # Jordan — AgentOrchestrator
  ├── tools/
  │   ├── base.py                 # Alex — BaseTool interface
  │   ├── loki.py                 # Sam  — LokiLogRetriever
  │   ├── git_blame.py            # Sam  — GitBlameChecker
  │   └── jira.py                 # Sam  — JiraTicketGetter
  ├── reasoning/
  │   ├── engine.py               # Riley — ReasoningEngine
  │   └── synthesis.py            # Riley — SynthesisEngine
  ├── report/
  │   ├── generator.py            # Riley — ReportGenerator
  │   └── fixes.py                # Riley — PossibleFixesGenerator
  ├── tests/
  │   ├── conftest.py             # Morgan — shared fixtures + mocks
  │   ├── test_classifier.py      # Morgan
  │   ├── test_tools.py           # Morgan
  │   ├── test_reasoning.py       # Morgan
  │   ├── test_report.py          # Morgan
  │   └── test_integration.py     # Morgan
  ├── Dockerfile
  ├── docker-compose.yml
  ├── requirements.txt
  └── README.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NON-NEGOTIABLE RULES (enforced by Alex)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✦ The classification step MUST run and be recorded in
    investigation_trace BEFORE any tool is called.
    If Jordan's orchestrator calls a tool without first
    populating hypothesis_ranking, Alex will flag a blocker.

  ✦ Null correlation_id must NEVER raise an exception.
    Morgan will test this explicitly with a null-only payload.

  ✦ If Loki, Jira, or Git is unreachable, the agent must
    still produce a partial report — not a 500 error.

  ✦ Every InvestigationStep must have a non-empty reasoning
    field. Empty reasoning = agent didn't think. Rejected.

  ✦ The report must always include a ruled_out_categories
    section listing which infra failures were checked and
    cleared — even if the root cause is a code change.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
START NOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Alex, begin Phase 1. Post the project tree and all Pydantic
models. The rest of the team should observe and ask
clarifying questions before Phase 2 begins.

Go.
```

---

## How to Use This Prompt

1. **Open Claude Code** in your terminal: `claude`
2. Paste the entire block above (everything inside the triple backticks)
3. Let Alex (the architect agent) run Phase 1 first — do not interrupt
4. Claude Code will simulate all 5 agents speaking in turn, collaborating through the phases
5. Each `[ALEX APPROVED]` signal is the gate before the next phase begins

## Tips for Best Results

| Tip | Detail |
|-----|--------|
| **Steer by role** | If a developer gets stuck, address them by name: *"Jordan, unblock yourself and proceed with a simplified classifier"* |
| **Request phase explicitly** | Type *"Move to Phase 3"* to advance if Claude Code seems to linger |
| **Ask for a specific file** | *"Sam, show me the full loki.py implementation now"* |
| **Trigger Morgan** | *"Morgan, run the classifier tests against Jordan's latest code"* |
| **Request the final report** | *"Morgan, post the final coverage report and any open blockers"* |

## Environment Variables to Set Before Running

```bash
LOKI_BASE_URL=http://loki:3100
GIT_REPOS_ROOT=/repos
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_API_TOKEN=<your-token>
JIRA_PROJECT_KEYS=RT,INFRA
SLOW_QUERY_THRESHOLD_MS=2000
HIGH_CHURN_COMMIT_COUNT=3
REPORT_OUTPUT_DIR=./reports
AGENT_TIMEOUT_SECONDS=120
```
