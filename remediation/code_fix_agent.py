"""
Code Fix Agent - Multi-turn agentic loop using LLM API with tool use.

Supports Anthropic (Claude) and any OpenAI-compatible endpoint (custom/company models).
Set LLM_PROVIDER=custom and LLM_BASE_URL=http://your-model/v1 to use your own model.

Instead of a one-shot LLM prompt, this runs a proper agentic loop:
  1. Agent reads files, understands the error
  2. Agent writes the fix
  3. Agent runs tests, sees output
  4. If tests fail, agent iterates with a refined fix
  5. Stops on success or max_iterations

Tools exposed to the agent (sandboxed to repo_path):
  - read_file(path)           → file contents
  - write_file(path, content) → write/overwrite a file
  - list_directory(path)      → directory listing
  - run_tests()               → runs the detected test command (no arbitrary shell)
"""

import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Tool definitions — Anthropic format (input_schema)
_TOOLS_ANTHROPIC = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file from the repo root"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the repository with new content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file from the repo root"},
                "content": {"type": "string", "description": "Full content to write to the file"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from repo root (use '.' for root)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "run_tests",
        "description": "Run the project test suite. Returns test output. Call this after writing your fix.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    }
]

# Tool definitions — OpenAI / custom format (function.parameters)
_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file from the repo root"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file in the repository with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file from the repo root"},
                    "content": {"type": "string", "description": "Full content to write to the file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at a path in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from repo root (use '.' for root)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the project test suite. Returns test output. Call this after writing your fix.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]


class CodeFixAgent:
    """
    Multi-turn agentic code fixer with tool use.

    Supports Anthropic (Claude) and OpenAI-compatible endpoints (custom/company models).
    Reads the codebase, applies fixes, runs tests, and iterates until
    tests pass or max_iterations is reached.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        repo_path: Path,
        test_command: str,
        test_timeout_seconds: int = 300,
        max_iterations: int = 5,
        provider: str = "anthropic",       # "anthropic" | "openai" | "custom"
        base_url: Optional[str] = None,    # Required for "custom" provider
    ):
        self.api_key = api_key
        self.model = model
        self.repo_path = repo_path
        self.test_command = test_command
        self.test_timeout_seconds = test_timeout_seconds
        self.max_iterations = max_iterations
        self.provider = provider.lower()
        # Determine API URL
        if self.provider == "anthropic":
            self.api_url = "https://api.anthropic.com/v1/messages"
        elif self.provider == "openai":
            self.api_url = "https://api.openai.com/v1/chat/completions"
        else:  # custom / company model
            if not base_url:
                raise ValueError("base_url is required for provider='custom'")
            self.api_url = base_url.rstrip("/") + "/chat/completions"
        self._files_written: list[str] = []

    async def run(
        self,
        root_cause: str,
        stack_traces: list[str],
        files_changed: list[str],
        test_runtime: str,
    ) -> Tuple[bool, str, int]:
        """
        Run the agentic fix loop.

        Args:
            root_cause: Root cause description from RCA
            stack_traces: Stack traces from log evidence
            files_changed: Files touched by the suspect commit
            test_runtime: Detected runtime (e.g. "python", "spring_boot")

        Returns:
            Tuple of (success, fix_description, iterations_used)
        """
        system_prompt = (
            "You are an expert software engineer performing an automated code fix. "
            "You have been given an RCA (Root Cause Analysis) report identifying a code issue. "
            "Your goal: read the relevant source files, apply the minimal fix, "
            "run the tests to verify, and iterate if needed. "
            "Be precise and conservative — change only what is necessary to fix the identified issue. "
            "Always run tests after applying a fix."
        )

        stack_trace_text = "\n".join(stack_traces[:3]) if stack_traces else "No stack traces available"
        files_text = "\n".join(files_changed[:10]) if files_changed else "No specific files identified"

        initial_message = (
            f"## RCA Context\n\n"
            f"**Root cause**: {root_cause}\n\n"
            f"**Test runtime**: {test_runtime} — test command: `{self.test_command}`\n\n"
            f"**Stack traces**:\n```\n{stack_trace_text}\n```\n\n"
            f"**Files likely involved**:\n{files_text}\n\n"
            f"Please:\n"
            f"1. Read the relevant source files\n"
            f"2. Apply the minimal fix to address the root cause\n"
            f"3. Run the tests to verify your fix\n"
            f"4. If tests fail, analyze the output and refine your fix\n"
        )

        messages = [{"role": "user", "content": initial_message}]
        iteration = 0
        fix_description = "No fix applied"

        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"CodeFixAgent iteration {iteration}/{self.max_iterations}")

            response = await self._call_api(messages, system_prompt)
            if response is None:
                return False, "LLM API call failed", iteration

            # Normalise response into provider-agnostic shape:
            #   tool_uses = [{id, name, input}]
            #   text       = plain text summary (if any)
            #   stop       = True when agent is done
            tool_uses, text, stop = self._parse_response(response)

            # Append assistant turn to history
            messages.append({"role": "assistant", "content": response["raw_content"]})

            if not tool_uses:
                fix_description = text[:500] if text else "Fix applied"
                tests_passed = len(self._files_written) > 0
                return tests_passed, fix_description, iteration

            # Execute all tool calls
            tests_passed_this_round = False
            tool_result_messages = []

            for tool_use in tool_uses:
                result_content, tests_passed_this_round = await self._execute_tool(
                    tool_use["name"], tool_use["input"], tests_passed_this_round
                )
                tool_result_messages.append(
                    self._format_tool_result(tool_use["id"], tool_use["name"], result_content)
                )

            # Append tool results — format differs by provider
            if self.provider == "anthropic":
                messages.append({"role": "user", "content": tool_result_messages})
            else:
                # OpenAI: each tool result is a separate message with role="tool"
                messages.extend(tool_result_messages)

            if stop:
                fix_description = text[:500] if text else "Fix applied"
                return tests_passed_this_round, fix_description, iteration

        # Exhausted iterations
        logger.warning(f"CodeFixAgent exhausted {self.max_iterations} iterations without success")
        return False, f"Fix attempted but tests did not pass after {self.max_iterations} iterations", iteration

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        current_tests_passed: bool
    ) -> Tuple[str, bool]:
        """Execute a single tool call. Returns (result_text, tests_passed)."""
        try:
            if tool_name == "read_file":
                return await self._tool_read_file(tool_input.get("path", "")), current_tests_passed

            elif tool_name == "write_file":
                return await self._tool_write_file(
                    tool_input.get("path", ""),
                    tool_input.get("content", "")
                ), current_tests_passed

            elif tool_name == "list_directory":
                return self._tool_list_directory(tool_input.get("path", ".")), current_tests_passed

            elif tool_name == "run_tests":
                output, passed = await self._tool_run_tests()
                return output, passed

            else:
                return f"Unknown tool: {tool_name}", current_tests_passed

        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}", exc_info=True)
            return f"Tool error: {str(e)}", current_tests_passed

    async def _tool_read_file(self, relative_path: str) -> str:
        file_path = self._safe_path(relative_path)
        if file_path is None:
            return f"Error: Path '{relative_path}' is outside the repository"
        if not file_path.exists():
            return f"File not found: {relative_path}"
        content = file_path.read_text(errors="replace")
        if len(content) > 10000:
            content = content[:10000] + f"\n... (truncated, {len(content)} total chars)"
        return content

    async def _tool_write_file(self, relative_path: str, content: str) -> str:
        file_path = self._safe_path(relative_path)
        if file_path is None:
            return f"Error: Path '{relative_path}' is outside the repository"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        self._files_written.append(relative_path)
        logger.info(f"Agent wrote file: {relative_path}")
        return f"Successfully wrote {len(content)} chars to {relative_path}"

    def _tool_list_directory(self, relative_path: str) -> str:
        dir_path = self._safe_path(relative_path)
        if dir_path is None:
            return f"Error: Path '{relative_path}' is outside the repository"
        if not dir_path.exists():
            return f"Directory not found: {relative_path}"
        if not dir_path.is_dir():
            return f"Not a directory: {relative_path}"
        entries = []
        for entry in sorted(dir_path.iterdir()):
            prefix = "📁 " if entry.is_dir() else "📄 "
            entries.append(f"{prefix}{entry.name}")
        return "\n".join(entries) if entries else "(empty directory)"

    async def _tool_run_tests(self) -> Tuple[str, bool]:
        logger.info(f"Agent running tests: {self.test_command}")
        args = self.test_command.split()
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.test_timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return f"Tests timed out after {self.test_timeout_seconds}s", False

            output = stdout.decode(errors="replace")
            output_tail = output[-3000:] if len(output) > 3000 else output
            passed = process.returncode == 0

            status = "PASSED ✓" if passed else "FAILED ✗"
            return f"Tests {status}\n\n{output_tail}", passed

        except Exception as e:
            return f"Test execution error: {str(e)}", False

    def _safe_path(self, relative_path: str) -> Optional[Path]:
        """Resolve path safely within repo_path (prevent path traversal)."""
        try:
            resolved = (self.repo_path / relative_path).resolve()
            if not str(resolved).startswith(str(self.repo_path.resolve())):
                return None
            return resolved
        except Exception:
            return None

    def _parse_response(self, response: dict) -> Tuple[list, str, bool]:
        """
        Normalise provider-specific response into (tool_uses, text, stop).

        tool_uses: list of {id, name, input}
        text:      plain-text content from the assistant (if any)
        stop:      True when the agent signals it's done (no more tool calls needed)
        """
        if self.provider == "anthropic":
            content = response.get("raw_content", [])
            tool_uses = [
                {"id": b["id"], "name": b["name"], "input": b.get("input", {})}
                for b in content if b.get("type") == "tool_use"
            ]
            text = " ".join(b["text"] for b in content if b.get("type") == "text")
            stop = response.get("stop_reason") == "end_turn"
            return tool_uses, text, stop
        else:
            # OpenAI / custom
            message = response.get("raw_content", {})
            tool_calls = message.get("tool_calls") or []
            tool_uses = [
                {
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"].get("arguments", "{}")),
                }
                for tc in tool_calls
            ]
            text = message.get("content") or ""
            stop = response.get("finish_reason") in ("stop", "length") and not tool_calls
            return tool_uses, text, stop

    def _format_tool_result(self, tool_id: str, tool_name: str, content: str) -> dict:
        """Format a tool result message for the correct provider."""
        if self.provider == "anthropic":
            return {"type": "tool_result", "tool_use_id": tool_id, "content": content}
        else:
            # OpenAI / custom: tool result is a standalone message
            return {"role": "tool", "tool_call_id": tool_id, "name": tool_name, "content": content}

    async def _call_api(self, messages: list, system_prompt: str) -> Optional[dict]:
        """Call the configured LLM API with tool use enabled."""
        if self.provider == "anthropic":
            return await self._call_anthropic(messages, system_prompt)
        else:
            return await self._call_openai_compatible(messages, system_prompt)

    async def _call_anthropic(self, messages: list, system_prompt: str) -> Optional[dict]:
        """Call Anthropic API."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "system": system_prompt,
            "tools": _TOOLS_ANTHROPIC,
            "messages": messages,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=120), ssl=False,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Anthropic API error {resp.status}: {await resp.text()}")
                        return None
                    data = await resp.json()
                    return {
                        "raw_content": data.get("content", []),
                        "stop_reason": data.get("stop_reason"),
                    }
        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}", exc_info=True)
            return None

    async def _call_openai_compatible(self, messages: list, system_prompt: str) -> Optional[dict]:
        """Call OpenAI or any OpenAI-compatible endpoint (custom/company model)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Prepend system message
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "tools": _TOOLS_OPENAI,
            "tool_choice": "auto",
            "messages": full_messages,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=120), ssl=False,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Custom LLM API error {resp.status}: {await resp.text()}")
                        return None
                    data = await resp.json()
                    message = data["choices"][0]["message"]
                    return {
                        "raw_content": message,
                        "finish_reason": data["choices"][0].get("finish_reason"),
                    }
        except Exception as e:
            logger.error(f"Custom LLM API call failed: {e}", exc_info=True)
            return None

    def stage_and_commit(self) -> None:
        """Stage all written files and commit (synchronous, call after async run)."""
        import subprocess
        if not self._files_written:
            return
        subprocess.run(
            ["git", "-C", str(self.repo_path), "add"] + self._files_written,
            check=True
        )
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit",
             "-m", "fix: automated code fix by SRE remediation agent [skip ci]"],
            check=True
        )
