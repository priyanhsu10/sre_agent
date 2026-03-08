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
