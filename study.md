# SRE Agent — Study Notes
## Async, LangChain, LangGraph & Orchestrator Logic

Quick reference for understanding how the core pipeline works.

---

## 1. Async / Await

### Why it exists

Network calls (Loki, Git, Jira, LLM) take time. Synchronous code sits idle while waiting.
Async lets Python do other work during that wait.

```
Sync:  Loki(200ms) → Git(150ms) → Jira(100ms) = 450ms total
Async: Loki + Git + Jira all at once           = ~200ms total
```

### Basic syntax

```python
# Normal function — blocks everything while running
def get_logs():
    return fetch()

# Async function — can be paused, lets other code run during wait
async def get_logs():
    result = await loki.fetch()   # pause here, don't block
    return result
```

Rules:
- `async def` → function can be paused
- `await` → pause here and wait (only inside `async def`)
- Every tool, LLM call, and pipeline node in this project is async

### Fire and forget — `asyncio.create_task`

Start something in the background without waiting for it.

```python
# orchestrator/agent.py:191 
# Return the report immediately, run remediation in background
if settings.AUTO_REMEDIATION_ENABLED and self._should_remediate(report):
    asyncio.create_task(self._run_remediation(report))

return report   # user gets this right away
```

Remediation (branch creation, tests, push) runs in background — could take minutes.

### Async chain in this project

```
POST /webhook/alert
    ↓ await
orchestrator.investigate(alert)
    ↓ await
self._graph.ainvoke(state)       ← LangGraph
    ↓ await
_classify_node → classifier.classify()
    ↓ await
_reason_node  → reasoning_engine.step()
    ↓ await
_execute_tool_node → tool.execute()
    ↓ await
loki / git / jira  ← actual network calls
    ↓ await
_synthesize_node → synthesis_engine.synthesize_root_cause()
    ↓
RCAReport
```

---

## 2. LangChain

### What it is

A library that wraps LLM APIs into a clean, unified interface.
Instead of building raw HTTP requests per provider, you use one standard interface.

### Before LangChain (old code — removed)

```python
# Had to manually build HTTP for each provider
async with aiohttp.ClientSession() as session:
    async with session.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json={"model": model, "messages": [...]}
    ) as response:
        data = await response.json()
        content = data["content"][0]["text"]
```

### After LangChain (current llm/client.py)

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# Build the client once in __init__
self._llm = ChatOpenAI(
    api_key=config.api_key,
    model=config.model,
    base_url=config.base_url,
    http_client=httpx.AsyncClient(verify=False),  # SSL off for corporate network
)

# Use it in complete()
response = await self._llm.ainvoke(messages)
print(response.content)
```

Switch models by changing one line (`model=`). SSL verification disabled for office network.

### Message types

LLMs use a conversation format with roles:

```python
messages = [
    SystemMessage(content="You are an SRE expert."),   # LLM's role/rules
    HumanMessage(content="What is causing this error?") # the actual question
]

response = await llm.ainvoke(messages)
# response.content → LLM's answer as string
```

`SystemMessage` = tells the LLM who it is and what rules to follow
`HumanMessage` = the actual prompt / question

### How it's used in this project (llm/client.py)

```python
async def complete(self, prompt: str, system_prompt: Optional[str] = None,
                   response_format: Optional[str] = None) -> LLMResponse:

    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))

    user_content = prompt
    if response_format == "json":
        user_content += "\n\nRespond with valid JSON only, no markdown formatting."
    messages.append(HumanMessage(content=user_content))

    # Optional: ask server to enforce JSON mode
    llm = self._llm
    if response_format == "json":
        try:
            llm = self._llm.bind(response_format={"type": "json_object"})
        except Exception:
            pass  # fallback to prompt-based JSON instruction

    response = await llm.ainvoke(messages)

    return LLMResponse(
        content=response.content,
        model=self.config.model,
        tokens_used=...,
    )
```

---

## 3. LangGraph

### What is a State Machine

A system with:
1. **Shared state** — data everyone reads and writes
2. **Nodes** — functions that process state
3. **Edges** — rules for which node runs next

Think of it like a flowchart that can loop.

### TypedDict — The Shared State

`TypedDict` is a Python dict with type hints. Every LangGraph node reads from it
and returns a partial update. LangGraph merges the update back automatically.

```python
# orchestrator/agent.py:51
class AgentState(TypedDict):
    investigation_id: str
    alert: AlertPayload
    classification_result: Optional[ClassificationResult]  # set by classify node
    investigation_trace: List[InvestigationStep]           # grows as nodes run
    tool_results: Dict[str, ToolResult]                    # set by execute_tool node
    tools_called: List[ToolName]
    step_number: int
    next_tool: Optional[ToolName]   # set by reason node, consumed by execute_tool
    report: Optional[RCAReport]     # set by synthesize node
    error: Optional[str]
```

Each node only returns what it changed:

```python
async def _classify_node(self, state: AgentState) -> dict:
    result = await self.classifier.classify(state["alert"])
    return {
        "classification_result": result,   # ← only update these two
        "investigation_trace": [step]
        # everything else in state is untouched
    }
```

### Building the Graph (orchestrator/agent.py:122)

```python
from langgraph.graph import StateGraph, END

def _build_graph(self):
    graph = StateGraph(AgentState)    # tell it what state looks like

    # Register nodes — name → function
    graph.add_node("classify",     self._classify_node)
    graph.add_node("reason",       self._reason_node)
    graph.add_node("execute_tool", self._execute_tool_node)
    graph.add_node("synthesize",   self._synthesize_node)

    # Fixed edges — always go this way
    graph.set_entry_point("classify")         # always start here
    graph.add_edge("classify", "reason")      # classify always → reason
    graph.add_edge("execute_tool", "reason")  # after tool → reason again (loop)
    graph.add_edge("synthesize", END)         # synthesize is final

    # Conditional edge — reason decides where to go
    graph.add_conditional_edges(
        "reason",                   # from this node
        self._route_after_reason,   # call this to decide
        {
            "execute_tool": "execute_tool",  # if returns "execute_tool" → go there
            "synthesize":   "synthesize",    # if returns "synthesize" → go there
        }
    )

    return graph.compile()
```

### Graph Topology

```
classify ──► reason ──(tool pending?)──► execute_tool ──┐
                │                                        │
                └──(no more tools)──► synthesize         └──► reason (loops)
                                          │
                                         END
```

### The Routing Function

The brain of the loop — decides whether to call another tool or stop.

```python
# orchestrator/agent.py:143
@staticmethod
def _route_after_reason(state: AgentState) -> str:
    if state["next_tool"] is not None and state["step_number"] <= MAX_STEPS:
        return "execute_tool"   # tool is queued, keep going
    return "synthesize"         # no more tools needed, wrap up
```

How the loop works step by step:
1. `reason_node` decides "call Loki next" → sets `next_tool = ToolName.LOKI`
2. Router sees `next_tool is not None` → routes to `execute_tool`
3. `execute_tool_node` runs Loki → resets `next_tool = None`, increments `step_number`
4. Router fires again after `execute_tool` → loops to `reason`
5. `reason_node` decides "call Git next" → sets `next_tool = ToolName.GIT_BLAME`
6. Repeats until `reason_node` sets `next_tool = None`
7. Router sees `next_tool is None` → routes to `synthesize`
8. Pipeline ends

### Running the Graph

```python
# orchestrator/agent.py:174
async def investigate(self, alert: AlertPayload) -> RCAReport:
    initial_state: AgentState = {
        "investigation_id": self.investigation_id,
        "alert": alert,
        "classification_result": None,
        "investigation_trace": [],
        "tool_results": {},
        "tools_called": [],
        "step_number": 2,    # step 1 = classify, tool loop starts at 2
        "next_tool": None,
        "report": None,
        "error": None,
    }

    final_state = await self._graph.ainvoke(initial_state)
    report = final_state.get("report")
    return report
```

---

## 4. Confidence-Based LLM Logic

### The Problem

Pattern matching is fast and free but fails on novel/unusual errors.
LLM is smart but slow and costs money.
Solution: use patterns first, only call LLM when patterns are uncertain.

### Decision Tree

```
Alert arrives
    ↓
Pattern matching always runs first (fast, free)
    ↓
top confidence >= 40%?
    YES → use pattern result, SKIP LLM  (cost saving)
    NO  → LLM enabled + configured?
              YES → call LLM, combine both results
              NO  → use pattern result anyway (best we can do)
```

### Where It's Decided (classifier/llm_classifier.py:59)

```python
async def classify(self, alert: AlertPayload) -> ClassificationResult:

    # Step 1: Always run pattern matching first
    pattern_result = await self.pattern_classifier.classify(alert)
    top_confidence = pattern_result.top_hypotheses[0].confidence_percentage

    # Step 2: Confidence gate
    if top_confidence < self.llm_threshold and self.llm_client:
        # Pattern uncertain (< 40%) AND LLM is available → use LLM
        llm_result = await self._classify_with_llm(alert)
        return self._combine_results(pattern_result, llm_result)
    else:
        # Pattern confident enough → skip LLM, return pattern result
        return pattern_result
```

`self.llm_threshold = 40.0` — if pattern is >= 40% confident, LLM is never called.
Common known errors (DB down, DNS fail) always hit >= 40% → LLM never called for them.

### Calling the LLM

```python
async def _classify_with_llm(self, alert):
    error_messages = [e.error_message for e in alert.errors]

    system_prompt = SREPrompts.classification_system_prompt()
    user_prompt = SREPrompts.classification_prompt(error_messages)

    # HTTP call to your internal LLM server
    response = await self.llm_client.complete(
        prompt=user_prompt,
        system_prompt=system_prompt,
        response_format="json"    # LLM responds in JSON
    )

    # Parse JSON response
    llm_data = json.loads(response.content)
    # → {"category": "db_connectivity", "confidence": 78.0, "reasoning": "..."}
```

### Combining Results (the smart part)

```python
def _combine_results(self, pattern_result, llm_result):
    llm_hypothesis = llm_result.top_hypotheses[0]
    pattern_category = pattern_result.top_hypotheses[0].category

    if llm_hypothesis.category == pattern_category:
        # Both methods agree → boost confidence by 10% (two signals = more certain)
        combined_confidence = min(llm_hypothesis.confidence_percentage + 10.0, 100.0)
        agreement_note = "Both pattern matching and LLM agree."
    else:
        # They disagree → trust LLM (it saw full context), note the disagreement
        combined_confidence = llm_hypothesis.confidence_percentage
        agreement_note = (
            f"LLM suggests {llm_hypothesis.category.value} "
            f"while patterns suggested {pattern_category.value}. "
            f"Using LLM result."
        )
```

### Where the Classifier is Chosen (orchestrator/agent.py:__init__)

```python
def __init__(self, investigation_id: str):
    if (settings.LLM_ENABLED       # LLM_ENABLED=true in .env
        and LLM_AVAILABLE           # langchain imported OK
        and settings.LLM_API_KEY    # API key set
        and settings.LLM_BASE_URL): # endpoint URL set
        llm_config = LLMConfig(...)
        self.classifier = LLMEnhancedClassifier(llm_config)  # hybrid
    else:
        self.classifier = ClassificationEngine()              # patterns only
```

Full decision:
```
LLM_ENABLED=false (or missing config)
    → ClassificationEngine (patterns only, always)

LLM_ENABLED=true (full config set)
    → LLMEnhancedClassifier
         pattern confidence >= 40% → use patterns, skip LLM
         pattern confidence <  40% → call LLM, combine results
```

---

## 5. Full Pipeline Visualised

```
                    ┌──────────────────────────────────┐
                    │        AgentOrchestrator          │
                    │      (LangGraph state machine)    │
                    └─────────────┬────────────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │      classify_node       │
                     │                          │
                     │  Pattern matching runs   │
                     │  confidence = 35%   ────►│ LLM_ENABLED? YES → call LLM
                     │  confidence = 87%   ────►│ LLM skipped (cost saving)
                     └────────────┬────────────┘
                                  │ ClassificationResult
                     ┌────────────▼────────────┐
                     │       reason_node        │
                     │  "call Loki next"        │
                     │  next_tool = LOKI        │
                     └────────────┬────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │    execute_tool_node     │
                     │  loki.execute(alert)     │
                     │  tool_results["loki"]=.. │
                     └────────────┬────────────┘
                                  │ loops back to reason
                     ┌────────────▼────────────┐
                     │       reason_node        │
                     │  "enough evidence"       │
                     │  next_tool = None        │
                     └────────────┬────────────┘
                                  │ routes to synthesize
                     ┌────────────▼────────────┐
                     │     synthesize_node      │
                     │  builds RCAReport        │
                     └────────────┬────────────┘
                                  │
                              RCAReport
                                  │
                     (if code fix found, background)
                                  │
                     ┌────────────▼────────────┐
                     │   RemediationAgent       │
                     │  branch → fix → test     │
                     │  → push                  │
                     └─────────────────────────┘
```

---

## 8. `_synthesize_node` — Deep Dive

The **final node** in the LangGraph pipeline. By the time it runs, all tools have collected
their evidence. Its job: look at everything and produce the final RCA report.

### What the node does (orchestrator/agent.py:320)

```python
async def _synthesize_node(self, state: AgentState) -> dict:

    # Step 1 — Determine root cause from all evidence
    root_cause, category, confidence_level, is_code_change = (
        await self.synthesis_engine.synthesize_root_cause(
            classification=state["classification_result"],
            tool_results=state["tool_results"],
        )
    )

    # Step 2 — Which categories were ruled out and why
    ruled_out = self.synthesis_engine.build_ruled_out_categories(...)

    # Step 3 — Extract code changes from git results
    code_changes = self.synthesis_engine.extract_code_changes(...)

    # Step 4 — Extract raw log evidence
    log_evidence = self.synthesis_engine.extract_log_evidence(...)

    # Step 5 — Generate fix recommendations
    possible_fixes = self.fixes_generator.generate_fixes(...)

    # Step 6 — Assemble the final RCAReport
    report = RCAReport(root_cause=root_cause, ...)

    return {"report": report}   # written into AgentState → graph ends
```

### Two synthesis engines — which one runs

```
LLM_ENABLED=false → SynthesisEngine       (rule-based, always works)
LLM_ENABLED=true  → LLMSynthesisEngine    (LLM first, rule-based fallback)
```

`LLMSynthesisEngine` inherits from `SynthesisEngine` — gets all methods for free,
only overrides `synthesize_root_cause`.

---

### Step 1 — `synthesize_root_cause` (reasoning/synthesis.py)

Analyzes all tool results into simple summary dicts:

```python
loki_evidence = _analyze_loki_evidence()
# → {has_evidence, stack_trace_count, slow_query_count, error_count}

git_evidence  = _analyze_git_evidence()
# → {has_commits, commit_count, high_churn_count, jira_key_count}

jira_evidence = _analyze_jira_evidence()
# → {has_tickets, ticket_count, risk_flagged_count}

is_code_change = git_evidence["has_commits"] and git_evidence["commit_count"] > 0
```

**Rule-based root cause text** — each piece of evidence adds a sentence:

```python
parts = [f"Root cause identified as {category.value}."]

if loki_evidence["has_evidence"]:
    parts.append(f"Log analysis found {error_count} error occurrences.")

if is_code_change:
    parts.append(f"Git identified {commit_count} recent code change(s)...")

if jira_evidence["risk_flagged_count"] > 0:
    parts.append(f"Jira flagged {risk_flagged_count} risky ticket(s)...")
```

**LLM root cause text** — sends real enriched data (actual log lines, commit messages,
ticket summaries) to LLM for a natural language explanation. Falls back to rule-based
if LLM call fails.

**Confidence boosting** — classifier gave a base %. Evidence from tools boosts it:

```
base confidence (from classifier):          65%
+ Loki found stack traces:                 +10%
+ Loki found slow queries:                  +5%
+ Git found commits:                        +5%
+ Git found high-churn files:               +5%
+ Jira flagged risky ticket (hotfix):      +10%
─────────────────────────────────────────
final confidence:                          100% (capped)
```

Converted to a level:
```
< 40%  → LOW
< 70%  → MEDIUM
< 85%  → HIGH
≥ 85%  → CONFIRMED
```

---

### Step 2 — `build_ruled_out_categories`

Shows reasoning — why the other 7 categories were dismissed.
Appears in the RCA report so humans can see the full reasoning, not just the answer.

```python
# For hypotheses 2 and 3 from classification
for hypothesis in classification.top_hypotheses[1:]:
    ruled_out.append(RuledOutCategory(
        category=category,
        reason="No strong evidence found for dns_failure",
        evidence="Logs did not contain patterns matching dns_failure"
    ))

# Also rule out all categories not in top 3
for category in all_8_categories:
    if category not in top_3:
        ruled_out.append(...)
```

---

### Step 3 — `extract_code_changes`

Pulls structured commit data from Git results with risk flag detection:

```python
for commit in commits[:10]:
    risk_flags = []
    if "hotfix"    in commit["message"].lower(): risk_flags.append("hotfix")
    if "emergency" in commit["message"].lower(): risk_flags.append("emergency")

    # Also pull risk labels from Jira for this ticket
    jira_ticket = extract_jira_key(commit["message"])   # e.g. "PAY-1234"
    if jira_ticket in jira_data:
        risk_flags.extend(jira_data[jira_ticket]["risk_flags"])

    code_changes.append(CodeChange(
        commit_hash=..., author=..., message=...,
        files_changed=[...], jira_ticket=jira_ticket, risk_flags=risk_flags
    ))
```

The most recent commit (`code_changes[0]`) becomes the revert candidate in fixes.

---

### Step 4 — `extract_log_evidence`

Packages raw Loki data into the report:

```python
return LogEvidence(
    correlation_id=corr_id,
    stack_traces=data["stack_traces"],
    key_log_lines=data["key_log_lines"][:20],   # limited to 20 lines
    slow_queries=data["slow_queries"],
    total_error_count=data["total_error_count"]
)
```

This is what you see in the "Log Evidence" section of the dashboard.

---

### Step 5 — `generate_fixes` (report/fixes.py)

Produces a prioritised action list. Has hardcoded templates for all 8 categories.
Special rule: **if a code change was found, revert is always Priority 1**.

```python
if is_code_change and code_changes:
    fixes.append(PossibleFix(
        priority=1,
        action=f"Revert commit {code_changes[0].commit_hash} by {code_changes[0].author}",
        ...
    ))

# All template fixes shift down by 1 if revert was added
for template in FIX_TEMPLATES[root_cause_category]:
    priority = template["priority"] + (1 if is_code_change else 0)
    fixes.append(PossibleFix(priority=priority, ...))

# Always last: monitoring improvement
fixes.append(PossibleFix(priority=len(fixes)+1, action="Enhance monitoring..."))

fixes.sort(key=lambda f: f.priority)
```

Example output for `db_connectivity` with a code change:
```
Priority 1: Revert commit abc123 by john.doe          ← dynamic
Priority 2: Check database server health and restart  ← template (was 1, shifted)
Priority 3: Increase connection pool size             ← template (was 2, shifted)
Priority 4: Review and optimize slow queries
Priority 5: Implement connection pooling health checks
Priority 6: Enhance monitoring and alerting           ← always last
```

---

### Full synthesize_node flow

```
state["tool_results"]  +  state["classification_result"]
            │
            ▼
   synthesize_root_cause()
            │
            ├── _analyze_loki_evidence()   counts: stack traces, errors, slow queries
            ├── _analyze_git_evidence()    counts: commits, high-churn files
            ├── _analyze_jira_evidence()   counts: tickets, risk flags
            │
            ├── LLM available?
            │       YES → LLM call with real log lines + commits + ticket summaries
            │       NO / fails → rule-based template sentences (silent fallback)
            │
            └── _calculate_final_confidence()
                    base% + boosts from each tool → capped at 100%
                    → LOW / MEDIUM / HIGH / CONFIRMED
            │
            ▼
   build_ruled_out_categories()   why the other 7 categories don't apply
            │
            ▼
   extract_code_changes()         commits + risk flags (hotfix, emergency, jira labels)
            │
            ▼
   extract_log_evidence()         stack traces, log lines, slow queries
            │
            ▼
   generate_fixes()
            ├── code change? → Priority 1 = Revert commit {hash}
            ├── category templates → Priority 2, 3, 4... (shifted if revert added)
            └── always → "Enhance monitoring" as final fix
            │
            ▼
        RCAReport assembled → written to AgentState → graph hits END
```

---

## 6. Key Files Quick Reference

| File | What it does |
|------|-------------|
| `orchestrator/agent.py` | LangGraph state machine, full pipeline coordinator |
| `llm/client.py` | LangChain wrapper, sends prompts to custom LLM endpoint |
| `classifier/engine.py` | Pattern-based classifier (regex scoring across 8 categories) |
| `classifier/llm_classifier.py` | Hybrid: patterns first, LLM if confidence < 40% |
| `classifier/patterns.py` | Regex patterns and weights for each failure category |
| `llm/prompts.py` | Prompt templates (system prompt, classification prompt) |
| `config.py` | All settings — reads from .env via pydantic-settings |
| `reasoning/engine.py` | Decides which tool to call next |
| `reasoning/synthesis.py` | Combines tool results into root cause |

---

## 7. Configuration That Controls LLM Behaviour

```bash
# .env

LLM_ENABLED=true                              # master switch
LLM_BASE_URL=http://internal-llm.company/v1   # your LLM server (required)
LLM_API_KEY=your-bearer-token                 # auth token
LLM_MODEL=Qwen2.5-Coder-32B-Instruct         # model name
LLM_CONFIDENCE_THRESHOLD=40.0                 # call LLM if pattern < this %
LLM_MAX_TOKENS=2048
LLM_TEMPERATURE=0.0                           # 0 = deterministic (good for prod)
LLM_TIMEOUT=30                                # seconds before giving up
```
