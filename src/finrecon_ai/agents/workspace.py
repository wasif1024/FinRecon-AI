"""Where an agent's files live: one LangSmith sandbox per case, or the run's own state.

A case's agents share one workspace: the investigation agent's working papers are there for the resolution agent,
and for the next round if a manager returns the case. Every run re-seeds the versioned operating manual and
playbooks (prompt_registry `PromptSpec.files`), which the agents may read but not write.

The sandbox is a filesystem, not a place to run code. It is wrapped in `FilesOnly`, which passes file operations
through and has no `execute`, so Deep Agents never offers a shell (and the harness profile removes `execute` as a
second guard). It also lets the read-only rules on the manual and playbooks apply: Deep Agents does not support
filesystem permissions on a backend that can run commands.

Sandboxes follow Deep-Sales-Assistant: named by thread (`finrecon-case-<case_id>`), reused when ready, restarted
when stopped, created on first use; they stop when idle and are deleted some days after stopping, so an abandoned
case costs nothing. `finrecon sandboxes list|stop` manages them.
"""

import time
import uuid
from typing import Any, Protocol

from deepagents.backends.protocol import BackendProtocol

from finrecon_ai.log import get_logger

log = get_logger(__name__)

SANDBOX_PREFIX = "finrecon-case-"


class Workspaces(Protocol):
    def for_case(self, case_id: uuid.UUID) -> BackendProtocol | None:
        """The case's workspace backend; None means the run's own state (nothing kept after the run)."""
        ...


class FilesOnly(BackendProtocol):
    """A sandbox's files without its command execution."""

    def __init__(self, sandbox: BackendProtocol):
        self._sandbox = sandbox

    def ls(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.ls(*args, **kwargs)

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.read(*args, **kwargs)

    def grep(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.grep(*args, **kwargs)

    def glob(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.glob(*args, **kwargs)

    def write(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.write(*args, **kwargs)

    def edit(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.edit(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        return self._sandbox.delete(*args, **kwargs)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return self._sandbox.upload_files(files)

    def download_files(self, paths: list[str]) -> Any:
        return self._sandbox.download_files(paths)


class StateWorkspaces:
    """No persistent workspace: files live in the agent run's state. Tests, and where sandboxes are disabled."""

    def for_case(self, case_id: uuid.UUID) -> None:
        return None


class LangSmithWorkspaces:
    def __init__(self, client: Any = None, *, idle_ttl_seconds: int = 600,
                 delete_after_stop_seconds: int = 7 * 24 * 3600, ready_timeout: int = 60):
        _trust_certifi_if_no_system_cas()
        if client is None:
            from langsmith.sandbox import SandboxClient

            from finrecon_ai.config import get_settings

            # The key from settings (.env), not only the environment: that holds it only while tracing is on.
            settings = get_settings()
            client = SandboxClient(api_key=settings.langsmith_api_key, api_endpoint=settings.langsmith_endpoint)
        self.client = client
        self.idle_ttl_seconds = idle_ttl_seconds
        self.delete_after_stop_seconds = delete_after_stop_seconds
        self.ready_timeout = ready_timeout

    def for_case(self, case_id: uuid.UUID) -> BackendProtocol:
        from deepagents.backends.langsmith import LangSmithSandbox

        return FilesOnly(LangSmithSandbox(self._get_or_create(sandbox_name(case_id))))

    def _get_or_create(self, name: str) -> Any:
        started = time.perf_counter()
        existing = self._find(name)
        if existing is not None:
            status = getattr(existing, "status", "ready")
            if status == "ready":
                sandbox = existing
            elif status == "stopped":
                sandbox = self.client.start_sandbox(name, timeout=self.ready_timeout)
            else:
                sandbox = self.client.wait_for_sandbox(name, timeout=self.ready_timeout)
            log.info("workspace.sandbox_reused", sandbox=name, was=status,
                     duration_ms=round((time.perf_counter() - started) * 1000, 1))
            return sandbox
        try:
            # Both limits keep an abandoned case from costing anything.
            sandbox = self.client.create_sandbox(name=name, idle_ttl_seconds=self.idle_ttl_seconds,
                                                 delete_after_stop_seconds=self.delete_after_stop_seconds,
                                                 timeout=self.ready_timeout)
        except Exception:
            raced = self._find(name)  # another run of this case's agents created it after we looked
            if raced is None:
                raise
            return raced
        log.info("workspace.sandbox_created", sandbox=name, duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return sandbox

    def case_sandboxes(self) -> list[Any]:
        return [s for s in self.client.list_sandboxes() if s.name.startswith(SANDBOX_PREFIX)]

    def stop_running(self) -> list[str]:
        """Stop every running case sandbox now instead of waiting for the idle timeout. Files are kept."""
        stopped = []
        for sandbox in self.case_sandboxes():
            if getattr(sandbox, "status", None) == "ready":
                self.client.stop_sandbox(sandbox.name)
                stopped.append(sandbox.name)
        return stopped

    def _find(self, name: str) -> Any:
        return next((s for s in self.client.list_sandboxes() if s.name == name), None)


def _trust_certifi_if_no_system_cas() -> None:
    """The sandbox's file operations run over a WebSocket that uses Python's default TLS trust store. Python from
    python.org on macOS ships without one, so every connection fails certificate checks; point it at certifi's
    bundle, which the HTTP clients already use. A configured SSL_CERT_FILE, or a system trust store, is left alone."""
    import os
    import ssl

    if os.environ.get("SSL_CERT_FILE") or ssl.get_default_verify_paths().cafile:
        return
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()


def sandbox_name(case_id: uuid.UUID) -> str:
    return f"{SANDBOX_PREFIX}{case_id}"


def seed(backend: BackendProtocol, files: dict[str, str]) -> None:
    """Write the versioned manual and playbooks into a persistent workspace, replacing any earlier copy."""
    results = backend.upload_files([(path, text.encode()) for path, text in sorted(files.items())])
    failed = [f"{path}: {r.error}" for (path, _), r in zip(sorted(files.items()), results, strict=True) if r.error]
    if failed:
        raise RuntimeError(f"could not seed the workspace: {'; '.join(failed)}")


def default_workspaces() -> Workspaces:
    from finrecon_ai.config import get_settings

    settings = get_settings()
    if settings.agent_workspace == "langsmith_sandbox":
        return LangSmithWorkspaces(idle_ttl_seconds=settings.sandbox_idle_ttl_seconds,
                                   delete_after_stop_seconds=settings.sandbox_delete_after_stop_seconds)
    return StateWorkspaces()
