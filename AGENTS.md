# AGENTS.md

Guidance for coding agents working in this repository.

## Project Snapshot

- Type: Python Home Assistant custom integration.
- Main package: `custom_components/xiaomi_doorbell_m3u8_archiver`.
- Test suite: `tests/` (pytest, mostly async scenarios wrapped with `asyncio.run`).
- Architecture: core domain/ports/policies/orchestration, plus adapter implementations.

## Environment and Setup

This repo includes a minimal `pyproject.toml` for Ruff lint/format config and
`mise.toml` for uv-managed local development tasks.
There is still no `requirements*.txt` or `tox.ini`.
Use a plain Python environment and install the minimum tools directly.

Suggested setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip pytest pytest-asyncio ruff homeassistant
```

Mise/uv setup:

```bash
mise trust
mise run setup
```

Ruff is the standard lint/format tool in this repository.

## Build, Lint, and Test Commands

### Build / Smoke Checks

There is no packaging/build pipeline in this repository.
Use bytecode compilation as a fast syntax/import smoke check:

```bash
python -m compileall custom_components tests
```

Mise task:

```bash
mise run build
```

### Test Commands (pytest)

Run full suite:

```bash
python -m pytest -q
```

Mise tasks:

```bash
mise run test
mise run test:unit
mise run test:integration
```

Run one file:

```bash
python -m pytest -q tests/test_orchestrator_happy_path.py
```

Run a single test (node id):

```bash
python -m pytest -q tests/test_orchestrator_happy_path.py::test_orchestrator_happy_path
```

Run tests matching expression:

```bash
python -m pytest -q -k motion_state
```

Run with stop-on-first-failure during debugging:

```bash
python -m pytest -q -x
```

### Lint / Format / Type Check

Format code:

```bash
python -m ruff format .
```

Lint code:

```bash
python -m ruff check .
```

Lint with autofix:

```bash
python -m ruff check . --fix
```

Mise tasks:

```bash
mise run format
mise run lint
```

Type checking is not configured (no mypy/pyright config in repo).

Recommended local verification order before handing off changes:

1. `python -m ruff format .`
2. `python -m ruff check .`
3. `python -m pytest -q`

## Repository-Specific Rules

### Cursor / Copilot Rules

Checked locations:

- `.cursor/rules/`
- `.cursorrules`
- `.github/copilot-instructions.md`

Result: none of these files currently exist in this repository.

If these rule files are added later, treat them as higher-priority constraints and update this document.

## Code Style Guidelines

Follow existing code patterns; avoid introducing unrelated style churn.

### Imports

- Keep `from __future__ import annotations` near the top of modules.
- Order imports as: stdlib, third-party/Home Assistant, then local package imports.
- Prefer explicit imports over wildcard imports.
- Use `TYPE_CHECKING` guarded imports for Home Assistant typing-only dependencies when appropriate.

### Formatting and Layout

- Use 4-space indentation and keep lines readable (roughly Black-compatible width).
- Keep module-level docstrings concise and present in core modules.
- Favor small, focused methods/functions over large mixed-responsibility blocks.
- Use trailing commas in multiline literals/calls when it improves diffs.

### Typing

- Type annotate all new functions/methods, including return types.
- Prefer built-in generics and PEP 604 unions (`list[str]`, `str | None`).
- Use dataclasses with `slots=True` for domain/value objects where practical.
- Use `Protocol` interfaces for ports/adapter boundaries.
- Use `Any` only at external boundaries (for example Home Assistant runtime objects).

### Naming Conventions

- `snake_case` for variables, functions, and module names.
- `PascalCase` for classes.
- `UPPER_SNAKE_CASE` for constants in `const.py`.
- Keep domain terms aligned with existing vocabulary: job, artifact, source, processor, storage, retention policy.

### Async and Concurrency

- Prefer `async`/`await` end-to-end in orchestration and adapters.
- Use `asyncio.to_thread` for blocking file/network helpers.
- Keep queue-worker semantics explicit (`fetch_queue`, `process_queue`, graceful stop sentinels).
- Ensure cancellation paths clean up subprocesses and temp resources.

### Error Handling

- Raise specific exceptions for direct validation failures (for example `ValueError`, `FileNotFoundError`).
- At orchestration boundaries, catch broad exceptions only when required for retries/final status transitions.
- Preserve useful context in error messages (command mode, stderr tail, job id, path).
- Keep cleanup best-effort and non-fatal; do not let cleanup failures hide primary outcomes.

### Architecture Boundaries

- Keep `core/` independent from concrete adapter implementations.
- Do not import `adapters` from files inside `core/`.
- Put integration-specific wiring in `ha_integration/` and adapter packages.
- Use ports (`core/ports`) to connect orchestration with implementations.

### Home Assistant Integration Patterns

- Follow HA async method naming (`async_setup`, `async_setup_entry`, etc.).
- Store runtime objects under `hass.data[DOMAIN]` using existing keys/patterns.
- Register and unregister services carefully to avoid duplicate registration.

### Tests

- Use pytest with clear test names describing behavior.
- Existing tests often define `async def scenario()` then call `asyncio.run(scenario())`; keep consistency unless intentionally refactoring suite-wide.
- Prefer deterministic tests with injected fakes/mocks over real network/process dependencies.
- Add/adjust tests for behavioral changes before or alongside implementation changes.

## Change Management Expectations for Agents

- Make minimal, targeted edits.
- Do not introduce new dependencies unless necessary and justified.
- Do not rewrite unrelated files just to satisfy personal style preferences.
- Update docs/tests when behavior or public service contracts change.
- If you add toolchain files (lint/type/format), document the exact commands here.
