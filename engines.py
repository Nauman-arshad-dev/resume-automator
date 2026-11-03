"""
Ways to call Claude, behind one function: run(config, system, content) -> (result, usage).

* "code": the Claude Code CLI in headless mode (`claude -p`). Uses the Claude
  Pro/Max subscription Claude Code is signed in with, so no API credits and no
  copy-paste. The CLI is found on PATH or inside the VS Code / Cursor extension.
* "api":  the Anthropic API with an API key (billed per use).

Both return the reply JSON already validated against the schema (by default main.RESPONSE_SCHEMA).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic

import main

CLI_TIMEOUT_SECONDS = 900


class EngineError(Exception):
    """A user-facing explanation of why Claude couldn't produce a resume."""


def find_claude_cli() -> str | None:
    """Path to the Claude Code CLI: $CLAUDE_CLI, PATH, then the copy bundled with the editor extension."""
    if (env := os.environ.get("CLAUDE_CLI")) and Path(env).is_file():
        return env
    for name in ("claude", "claude.exe"):
        if found := shutil.which(name):
            return found
    home = Path.home()
    candidates = [p for pattern in (
        ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*",
        ".vscode-insiders/extensions/anthropic.claude-code-*/resources/native-binary/claude*",
        ".cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude*",
        ".windsurf/extensions/anthropic.claude-code-*/resources/native-binary/claude*",
    ) for p in home.glob(pattern) if p.is_file()]
    candidates += [p for p in (home / ".local/bin/claude", home / ".claude/local/claude") if p.is_file()]
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None


def run(config: dict, system: str, content: str, schema: dict | None = None) -> tuple[dict, str]:
    """config: {"engine": "code"|"api", "model_cli", "model_api", "effort", "api_key", "cli_path"}.

    schema: the JSON shape of the reply (default: the tailored-resume response).
    """
    schema = schema or main.RESPONSE_SCHEMA
    if config["engine"] == "code":
        return _run_claude_code(config, system, content, schema)
    return _run_api(config, system, content, schema)


def _run_claude_code(config: dict, system: str, content: str, schema: dict) -> tuple[dict, str]:
    cli = config.get("cli_path") or find_claude_cli()
    if not cli:
        raise EngineError("Claude Code was not found. Install it from https://claude.com/claude-code, "
                          "sign in once, or choose another connection in the sidebar.")
    cmd = [
        cli, "-p",
        "--output-format", "json",
        "--model", config.get("model_cli", "sonnet"),
        "--effort", config.get("effort", "medium"),
        "--system-prompt", system,
        "--json-schema", json.dumps(schema, separators=(",", ":")),
        "--tools", "",                      # a pure writing task: no file or shell access
        "--no-session-persistence",
        "--strict-mcp-config",              # don't start the user's MCP servers
    ]
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)      # make sure the subscription login is used, not API billing
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, input=content, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=CLI_TIMEOUT_SECONDS, env=env, cwd=tempfile.gettempdir(),  # no project CLAUDE.md
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise EngineError(f"Claude Code didn't answer within {CLI_TIMEOUT_SECONDS // 60} minutes. Try again, "
                          "or pick a faster model / lower effort in the sidebar.") from None
    except OSError as e:
        raise EngineError(f"Could not start Claude Code ({cli}): {e}") from None

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        details = (proc.stderr or proc.stdout).strip()[-600:]
        if any(w in details.lower() for w in ("log in", "login", "not logged", "authenticate")):
            raise EngineError("Claude Code isn't signed in. Open a terminal, run `claude`, and sign in "
                              "with your Claude account, then try again.") from None
        raise EngineError(f"Claude Code failed: {details or 'no output'}") from None

    if out.get("is_error"):
        message = str(out.get("result") or out.get("subtype") or "unknown error")
        if "limit" in message.lower():
            raise EngineError(f"You've reached your Claude plan's usage limit: {message}")
        raise EngineError(f"Claude Code reported an error: {message}")

    result = out.get("structured_output")
    if not isinstance(result, dict):  # fall back to reading JSON out of the text answer
        text = str(out.get("result", ""))
        try:
            result = (main.parse_claude_reply(text) if schema is main.RESPONSE_SCHEMA
                      else main._load_json(text[text.find("{"):text.rfind("}") + 1]))
        except (main.TailorError, json.JSONDecodeError) as e:
            raise EngineError(f"Claude's answer could not be read: {e}") from None
    usage = f"Claude Code · {config.get('model_cli', 'sonnet')} · {time.time() - started:.0f}s · your Claude plan"
    return main.normalize(result, schema), usage


def _run_api(config: dict, system: str, content: str, schema: dict) -> tuple[dict, str]:
    if not config.get("api_key"):
        raise EngineError("Add your Anthropic API key in the sidebar.")
    try:
        return main._call_claude(system, content, config["api_key"], config["model_api"], config["effort"], schema)
    except main.TailorError as e:
        raise EngineError(str(e)) from None
    except anthropic.AuthenticationError:
        raise EngineError("Invalid API key. Check the key in the sidebar.") from None
    except anthropic.RateLimitError:
        raise EngineError("Rate limited by the API. Wait a minute and try again.") from None
    except anthropic.APIStatusError as e:
        if "credit balance" in str(e.message).lower():
            raise EngineError("Your Anthropic API account is out of credits. Add credits at "
                              "https://console.anthropic.com/settings/billing, or switch to Claude Code "
                              "(your subscription) in the sidebar.") from None
        raise EngineError(f"API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise EngineError("Could not reach the Anthropic API. Check your internet connection.") from None
