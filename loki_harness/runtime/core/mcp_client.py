"""Loki MCP stdio transport — improved with background reader and stderr drain.

Improvements over v1:
- Background reader thread: non-blocking stdout via dedicated daemon thread + queue
- Notification handling: dispatches incoming JSON-RPC notifications
- Stderr drain: dedicated thread prevents pipe buffer blocking
- Restart/backoff: exponential backoff with max retries
- Timeout: deadline-based with configurable per-request timeout
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class McpStdioServerConfig:
    server_name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    restart_max_retries: int = 3
    restart_backoff_base: float = 1.0


# ---------------------------------------------------------------------------
# Notification handler type
# ---------------------------------------------------------------------------

NotificationHandler = Callable[[str, dict[str, Any]], None]


# ---------------------------------------------------------------------------
# McpStdioClient with background reader
# ---------------------------------------------------------------------------


class McpStdioClient:
    """MCP stdio transport with background reader, notification support, and restart."""

    def __init__(
        self,
        config: McpStdioServerConfig,
        on_notification: NotificationHandler | None = None,
    ):
        self.config = config
        self._on_notification = on_notification
        self._next_id = 1
        self._lock = threading.Lock()
        self._response_queue: Queue[dict[str, Any]] = Queue()
        self.process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._running = False
        self._restart_count = 0

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def ensure_started(self) -> None:
        with self._lock:
            if self._running and self.process is not None and self.process.poll() is None:
                return
            self._do_start_locked()

    def close(self) -> None:
        with self._lock:
            self._do_close_locked()

    def _do_start_locked(self) -> None:
        merged_env = os.environ.copy()
        merged_env.update(self.config.env)
        self.process = subprocess.Popen(
            [self.config.command, *self.config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=merged_env,
        )
        self._running = True
        self._next_id = 1
        self._response_queue = Queue()

        # Start background stdout reader
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name=f"mcp-reader-{self.config.server_name}",
        )
        self._reader_thread.start()

        # Start stderr drain
        self._stderr_thread = threading.Thread(
            target=self._stderr_drain,
            daemon=True,
            name=f"mcp-stderr-{self.config.server_name}",
        )
        self._stderr_thread.start()

        # Initialize MCP session
        self._initialize_locked()

    def _do_close_locked(self) -> None:
        self._running = False
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        self._reader_thread = None
        self._stderr_thread = None

    def restart(self) -> None:
        """Restart the MCP server with exponential backoff."""
        with self._lock:
            self._do_close_locked()
            if self._restart_count >= self.config.restart_max_retries:
                raise RuntimeError(
                    f"MCP server {self.config.server_name} exceeded max restarts "
                    f"({self.config.restart_max_retries})"
                )
            backoff = self.config.restart_backoff_base * (2 ** self._restart_count)
            logger.info(
                "MCP server %s restarting (attempt %d/%d, backoff %.1fs)",
                self.config.server_name,
                self._restart_count + 1,
                self.config.restart_max_retries,
                backoff,
            )
            time.sleep(backoff)
            self._restart_count += 1
            self._do_start_locked()

    # -------------------------------------------------------------------
    # Background threads
    # -------------------------------------------------------------------

    def _reader_loop(self) -> None:
        """Background thread: read MCP messages from stdout and enqueue them."""
        try:
            while self._running:
                process = self.process
                if process is None or process.stdout is None:
                    time.sleep(0.01)
                    continue
                try:
                    message = self._read_one_message(process)
                    if message is None:
                        time.sleep(0.01)
                        continue
                    # Check if it's a notification (no id field)
                    if "id" not in message and "method" in message:
                        self._handle_notification(message)
                    else:
                        self._response_queue.put(message)
                except Exception:
                    if self._running:
                        logger.debug("MCP reader error for %s", self.config.server_name, exc_info=True)
                    time.sleep(0.05)
        except Exception:
            logger.debug("MCP reader thread exiting for %s", self.config.server_name, exc_info=True)

    def _read_one_message(self, process: subprocess.Popen[bytes]) -> dict[str, Any] | None:
        """Read one MCP message from stdout. Uses blocking readline in a daemon thread."""
        if process.stdout is None:
            return None

        # Read headers
        headers: dict[str, str] = {}
        while True:
            line = process.stdout.readline()
            if not line:
                return None
            stripped = line.decode("ascii", errors="replace").strip()
            if not stripped:
                break
            key, _, value = stripped.partition(":")
            headers[key.lower()] = value.strip()

        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None

        raw = process.stdout.read(length)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _stderr_drain(self) -> None:
        """Background thread: drain stderr via non-blocking read to prevent pipe blocking."""
        try:
            while self._running:
                process = self.process
                if process is None or process.stderr is None:
                    time.sleep(0.1)
                    continue
                try:
                    # Set stderr to non-blocking to avoid hanging on read()
                    fd = process.stderr.fileno()
                    os.set_blocking(fd, False)
                    chunk = process.stderr.read(4096)
                    # Restore blocking for other consumers
                    os.set_blocking(fd, True)
                    if chunk:
                        logger.debug(
                            "MCP stderr [%s]: %s",
                            self.config.server_name,
                            chunk.decode("utf-8", errors="replace")[:200],
                        )
                    else:
                        time.sleep(0.1)
                except BlockingIOError:
                    time.sleep(0.1)
                except Exception:
                    time.sleep(0.1)
        except Exception:
            logger.debug("MCP stderr drain exiting for %s", self.config.server_name, exc_info=True)

    # -------------------------------------------------------------------
    # Notification handling
    # -------------------------------------------------------------------

    def _handle_notification(self, message: dict[str, Any]) -> None:
        """Dispatch an incoming JSON-RPC notification."""
        method = message.get("method", "")
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        logger.debug("MCP notification [%s]: %s", self.config.server_name, method)
        if self._on_notification is not None:
            try:
                self._on_notification(method, params)
            except Exception:
                logger.debug("Notification handler error", exc_info=True)

    # -------------------------------------------------------------------
    # MCP protocol
    # -------------------------------------------------------------------

    def _initialize_locked(self) -> None:
        result = self._request_locked(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "loki-runtime", "version": "0.2"},
            },
        )
        if "error" in result:
            error_msg = result.get("error", "unknown")
            logger.error("MCP initialize failed for %s: %s", self.config.server_name, error_msg)
            self._do_close_locked()
            raise RuntimeError(
                f"MCP server {self.config.server_name} initialize failed: {error_msg}"
            )
        self._write_locked({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })

    def list_tools(self) -> list[dict[str, Any]]:
        response = self.request("tools/list", {})
        tools = response.get("tools", response.get("result", {}).get("tools", []))
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ensure_started()
        with self._lock:
            return self._request_locked(method, params)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.ensure_started()
        with self._lock:
            self._write_locked({"jsonrpc": "2.0", "method": method, "params": params})

    # -------------------------------------------------------------------
    # Low-level I/O
    # -------------------------------------------------------------------

    def _request_locked(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write_locked({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })

        deadline = time.monotonic() + self.config.timeout_seconds
        requeued_count = 0
        max_requeued = 100
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = self._response_queue.get(timeout=min(remaining, 1.0))
            except Empty:
                continue

            if message.get("id") != request_id:
                # Guard against infinite requeue loop
                requeued_count += 1
                if requeued_count > max_requeued:
                    logger.warning("MCP too many unexpected messages for %s", self.config.server_name)
                    return {"error": "Too many unexpected MCP messages"}
                self._response_queue.put(message)
                continue
            if "error" in message:
                return {"error": message["error"]}
            result = message.get("result", {})
            return result if isinstance(result, dict) else {"result": result}

        return {"error": f"MCP request timed out: {method}"}

    def _write_locked(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("MCP stdio stdin is closed")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        process.stdin.write(header + body)
        process.stdin.flush()


# ---------------------------------------------------------------------------
# Session manager (unchanged API)
# ---------------------------------------------------------------------------


class McpSessionManager:
    """Per-run MCP connection pool with long-lived stdio clients."""

    def __init__(self):
        self._clients: dict[str, McpStdioClient] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get_client(self, config: McpStdioServerConfig) -> McpStdioClient:
        with self._lock:
            client = self._clients.get(config.server_name)
            if client is None:
                client = McpStdioClient(config)
                self._clients[config.server_name] = client
            return client

    def list_tools(self, config: McpStdioServerConfig) -> list[dict[str, Any]]:
        tools = self.get_client(config).list_tools()
        with self._lock:
            self._tool_cache[config.server_name] = tools
        return tools

    def get_cached_tools(self, server_name: str) -> list[dict[str, Any]] | None:
        with self._lock:
            tools = self._tool_cache.get(server_name)
            return list(tools) if tools is not None else None

    def call_tool(self, config: McpStdioServerConfig, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.get_client(config).call_tool(name, arguments)

    def refresh_tools(self, config: McpStdioServerConfig) -> list[dict[str, Any]]:
        return self.list_tools(config)

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._tool_cache.clear()
        for client in clients:
            client.close()


def with_mcp_stdio_client(config: McpStdioServerConfig, callback):
    client = McpStdioClient(config)
    try:
        client.ensure_started()
        return callback(client)
    finally:
        client.close()
