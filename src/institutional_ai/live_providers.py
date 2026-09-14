"""Text-only CLI adapters. No shared sessions, fallback providers or application retries."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .execution import ExecutionError, run_subprocess
from .models import Usage
from .providers import FleetDemoProvider, Provider, ProviderResult, WorkRequest, validate_result


def _prompt(request: WorkRequest) -> str:
    return (
        "You are the assigned institutional specialist. Use only the supplied evidence. "
        "Do not use tools, access files, request permissions, or invent experiments/results. "
        "Return exactly one JSON object matching ProviderResult; no markdown fences. "
        "Preserve assigned worker/project/task/report identities. Only populate the field for "
        "this action: PLAN=role_requests; ANALYSE=report and artifact_markdown; REVIEW=review; "
        "REVISE=report and artifact_markdown; SYNTHESIZE=director_report. "
        "For REVISE use a new report id, increment version, set supersedes_report_id and status "
        "REVISED (or DISPUTED). For SYNTHESIZE cite ALL supplied report and review ids and keep "
        "disagreements explicit. Keep prose concise. Omit optional generated ids/timestamps "
        "except when identifying a revision. Never invent artifact ids. Do not report usage "
        "or provider/model metadata; the host records those. PLAN may request only the "
        "allowlisted OperationsResearcher, with capabilities ['fleet optimisation'], "
        "jurisdiction ['operations_research'], related_tasks containing your task id, no tools.\n"
        "ProviderResult schema:\n" + json.dumps(ProviderResult.model_json_schema())
        + "\nWorkRequest:\n" + request.model_dump_json()
    )


def _object(text: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("CLI output must be a JSON object")
    return value


class ClaudeCliProvider:
    name = "claude"

    def preflight(self) -> None:
        if not shutil.which("claude"):
            raise ExecutionError("Claude CLI is missing; install and log in before running")

    def run(self, request: WorkRequest) -> ProviderResult:
        self.preflight()
        if not request.worker.model or request.worker.model == "fleet-demo-v1":
            raise ExecutionError("live workers require an explicit CLI model")
        with tempfile.TemporaryDirectory(prefix="institutional-claude-") as directory:
            output, _ = run_subprocess([
                "claude", "--print", "--output-format", "json", "--tools", "",
                "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence", "--permission-mode", "dontAsk",
                "--permission-prompts", "none", "--model", request.worker.model,
            ], _prompt(request), cwd=Path(directory))
        envelope = _object(output)
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise ExecutionError("Claude did not return a successful result")
        result = ProviderResult.model_validate(_object(envelope["result"]))
        validate_result(request, result)
        model_usage = envelope.get("modelUsage", {})
        if len(model_usage) != 1:
            raise ExecutionError("Claude did not identify exactly one model; refusing ambiguous routing")
        result.provider = self.name
        result.model = next(iter(model_usage))
        result.model_source = "cli.modelUsage"
        usage = envelope.get("usage", {})
        result.usage = Usage(input_tokens=usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0))
        return result


class CodexCliProvider:
    name = "codex"

    def preflight(self) -> None:
        if not shutil.which("codex"):
            raise ExecutionError("Codex CLI is missing; install and log in before running")
        # The vetted denial hook must not grant trust to unrelated user hooks.
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        if (home / "hooks.json").exists():
            raise ExecutionError("Codex user hooks.json exists; text-only adapter refuses external hooks")

    def run(self, request: WorkRequest) -> ProviderResult:
        self.preflight()
        if not request.worker.model or request.worker.model == "fleet-demo-v1":
            raise ExecutionError("live workers require an explicit CLI model")
        with tempfile.TemporaryDirectory(prefix="institutional-codex-") as directory:
            cwd = Path(directory)
            version, _ = run_subprocess(["codex", "--version"], cwd=cwd, timeout=5)
            match = re.fullmatch(r"codex-cli (\d+)\.(\d+)\.(\d+)\s*", version)
            if not match or tuple(map(int, match.groups())) < (0, 154, 0):
                raise ExecutionError("text-only Codex adapter requires CLI >= 0.154.0 with denial hooks")
            # Native hook denial covers residual tools such as apply_patch. The read-only sandbox
            # is an additional boundary; it is not treated as a tool-disable switch.
            command = ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                       "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                       "--dangerously-bypass-hook-trust", "--model", request.worker.model]
            settings = [
                'approval_policy="never"', 'web_search="disabled"', 'mcp_servers={}',
                'project_doc_max_bytes=0', 'tools.update_plan.enabled=false',
                'model_providers.openai.request_max_retries=0',
                'model_providers.openai.stream_max_retries=0',
                'hooks.PreToolUse=[{matcher=".*",hooks=[{type="command",command="exit 2",timeout=1}]}]',
            ]
            for setting in settings:
                command.extend(["-c", setting])
            for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent",
                            "multi_agent_v2", "browser_use", "browser_use_external", "in_app_browser",
                            "image_generation", "view_image", "js_repl", "memories", "skill_search",
                            "sleep_tool", "tool_suggest", "unbounded_connection_retries"):
                command.extend(["--disable", feature])
            command.extend(["--enable", "hooks", "--enable", "skip_host_skill_discovery", "-"])
            output, _ = run_subprocess(command, _prompt(request), cwd=cwd)
        events = [_object(line) for line in output.splitlines() if line.strip()]
        messages: list[str] = []
        usage: dict[str, Any] | None = None
        for event in events:
            if event.get("type") in {"error", "turn.failed"}:
                raise ExecutionError("Codex turn failed")
            item = event.get("item", {})
            if item and item.get("type") not in {"agent_message", "reasoning"}:
                raise ExecutionError("Codex attempted a tool call in text-only mode")
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                messages.append(item["text"])
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
        if len(messages) != 1 or usage is None:
            raise ExecutionError("Codex must return one final message and a completed turn")
        result = ProviderResult.model_validate(_object(messages[0]))
        validate_result(request, result)
        result.provider = self.name
        # Exec JSONL does not currently attest a resolved model id. Record the explicit selection,
        # never describe it as server-attested; there is no application fallback or model router.
        result.model = request.worker.model
        result.model_source = "explicit_cli_argument_not_server_attested"
        result.usage = Usage(input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0))
        return result


class ProviderDispatcher:
    name = "dispatch"

    def __init__(self, adapters: dict[str, Provider] | None = None):
        self.adapters = adapters or {"deterministic": FleetDemoProvider(),
                                     "claude": ClaudeCliProvider(), "codex": CodexCliProvider()}

    def preflight(self) -> None:
        pass  # No live CLI discovery or authentication in deterministic mode.

    def select(self, request: WorkRequest) -> Provider:
        try:
            return self.adapters[request.worker.provider]
        except KeyError as exc:
            raise ExecutionError(f"unknown provider {request.worker.provider!r}") from exc

    def run(self, request: WorkRequest) -> ProviderResult:
        return self.select(request).run(request)
