"""Unit and integration tests for the common runtime launcher and run.sh."""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import launch
from launch import (
    ConfigurationError,
    Launcher,
    LauncherConfig,
    is_loopback_host,
    is_meili_healthy,
    is_ollama_healthy,
    parse_bool,
)

ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CATALOG = ROOT / "examples" / "minimal" / "CATALOG.md"
RUN_SH = ROOT / "foundation" / "tools" / "knowledge-ui" / "run.sh"


class MockHealthServer:
    """Lightweight HTTP server to simulate Meilisearch and Ollama endpoints."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes = routes
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def start(self) -> str:
        routes = self.routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, b"Not Found"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                status, body = routes.get(self.path, (404, b"Not Found"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # Silence stderr in tests

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class TestLauncherConfig(unittest.TestCase):
    def test_parse_bool(self):
        for val in ("1", "true", "TRUE", "yes", "YES", "on", "ON"):
            self.assertTrue(parse_bool("FLAG", val))
        for val in ("0", "false", "FALSE", "no", "NO", "off", "OFF", ""):
            self.assertFalse(parse_bool("FLAG", val))
        self.assertFalse(parse_bool("FLAG", None, default=False))
        self.assertTrue(parse_bool("FLAG", None, default=True))

        with self.assertRaises(ConfigurationError) as ctx:
            parse_bool("FLAG", "unrecognized")
        self.assertIn("Accepted values", str(ctx.exception))

    def test_is_loopback_host(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("127.0.1.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("0.0.0.0"))

        self.assertFalse(is_loopback_host("192.168.1.1"))
        self.assertFalse(is_loopback_host("100.64.0.1"))
        self.assertFalse(is_loopback_host("remote.server.internal"))
        self.assertFalse(is_loopback_host(None))

    def test_missing_catalog_fails(self):
        env = {}
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("neither FEDERATION_CATALOG nor KNOWLEDGE_CATALOG is set", str(ctx.exception))

    def test_nonexistent_catalog_fails(self):
        env = {"FEDERATION_CATALOG": "/path/to/nonexistent/CATALOG.md"}
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("does not exist", str(ctx.exception))

    def test_federation_catalog_takes_precedence_over_knowledge_catalog(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_CATALOG": "/path/to/invalid/CATALOG.md",
        }
        cfg = LauncherConfig.from_env(env)
        self.assertEqual(cfg.catalog_path, MINIMAL_CATALOG.resolve())

    def test_auto_index_requires_meili_url(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_AUTO_INDEX": "1",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("KNOWLEDGE_AUTO_INDEX requires MEILI_URL", str(ctx.exception))

    def test_manage_meili_requires_meili_url(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_MEILI": "1",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("KNOWLEDGE_MANAGE_MEILI requires MEILI_URL", str(ctx.exception))

    def test_manage_meili_rejects_remote_url(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_MEILI": "1",
            "MEILI_URL": "http://192.168.1.100:7700",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("Managed native services must be local", str(ctx.exception))

    def test_manage_ollama_rejects_remote_host(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_OLLAMA": "1",
            "OLLAMA_HOST": "10.0.0.5:11434",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("Cannot manage remote Ollama target", str(ctx.exception))

    def test_manage_ollama_rejects_remote_embed_url(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_OLLAMA": "1",
            "OLLAMA_EMBED_URL": "http://example.com:11434/api/embed",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("Cannot manage remote Ollama", str(ctx.exception))

    def test_invalid_ui_port(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "UI_PORT": "invalid_port",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("Invalid UI_PORT", str(ctx.exception))

    def test_live_marimo_checks_dependency(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_ENABLE_LIVE_MARIMO": "1",
        }
        with patch("importlib.util.find_spec", return_value=None):
            with self.assertRaises(ConfigurationError) as ctx:
                LauncherConfig.from_env(env)
            self.assertIn("optional dependency 'marimo' is not installed", str(ctx.exception))


class TestLauncherExecution(unittest.TestCase):
    def test_is_healthy_probes(self):
        server = MockHealthServer({
            "/health": (200, b'{"status":"available"}'),
            "/api/tags": (200, b'{"models":[]}'),
        })
        base_url = server.start()
        try:
            self.assertTrue(is_meili_healthy(base_url))
            self.assertTrue(is_meili_healthy(base_url, api_key="secret"))
            self.assertFalse(is_meili_healthy("http://127.0.0.1:0"))

            parsed = urlparse(base_url)
            self.assertTrue(is_ollama_healthy(f"127.0.0.1:{parsed.port}"))
            self.assertTrue(is_ollama_healthy(base_url))
            self.assertFalse(is_ollama_healthy("127.0.0.1:0"))
        finally:
            server.stop()

    def test_external_meili_reuse_and_non_termination(self):
        server = MockHealthServer({"/health": (200, b'{"status":"available"}')})
        base_url = server.start()
        try:
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": base_url,
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)
            launcher.start_meili()

            # The service was already running, so launcher owns nothing
            self.assertEqual(len(launcher.owned_services), 0)
            launcher.cleanup()

            # External service must still be alive!
            self.assertTrue(is_meili_healthy(base_url))
        finally:
            server.stop()

    def test_owned_service_lifecycle_and_cleanup(self):
        # Create a mock native service script that listens on a port
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_bin = tmp_path / "mock_meili"
            mock_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import http.server, sys\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        self.send_response(200)\n"
                "        self.end_headers()\n"
                "        self.wfile.write(b'{\"status\":\"available\"}')\n"
                "    def log_message(self, *a): pass\n"
                "s = http.server.HTTPServer(('127.0.0.1', int(sys.argv[4].split(':')[1])), H)\n"
                "s.serve_forever()\n",
                encoding="utf-8",
            )
            mock_bin.chmod(0o755)

            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

            url = f"http://127.0.0.1:{port}"
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": url,
                "MEILI_BIN": str(mock_bin),
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            launcher.start_meili()
            self.assertEqual(len(launcher.owned_services), 1)
            proc = launcher.owned_services[0]
            self.assertIsNone(proc.poll())
            self.assertTrue(is_meili_healthy(url))

            # Cleanup must terminate owned service
            launcher.cleanup()
            self.assertIsNotNone(proc.poll())
            self.assertFalse(is_meili_healthy(url))

    def test_meili_startup_failure_reports_and_cleans_up(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Binary that exits immediately with failure
            failing_bin = tmp_path / "failing_bin"
            failing_bin.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            failing_bin.chmod(0o755)

            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": "http://127.0.0.1:17899",
                "MEILI_BIN": str(failing_bin),
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            with self.assertRaises(RuntimeError) as ctx:
                launcher.start_meili()
            self.assertIn("exited prematurely with code 42", str(ctx.exception))

    def test_ollama_missing_binary_warns_and_continues(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_OLLAMA": "1",
            "OLLAMA_BIN": "/nonexistent/path/to/ollama",
        }
        cfg = LauncherConfig.from_env(env)
        launcher = Launcher(cfg)

        import io
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            ready = launcher.start_ollama()
        self.assertFalse(ready)
        self.assertIn("Warning: ollama not found", captured_stderr.getvalue())

    def test_warmup_fallback_on_error(self):
        # Warmup endpoint that returns 500
        server = MockHealthServer({"/api/embed": (500, b'{"error":"model error"}')})
        base_url = server.start()
        try:
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": f"{base_url}/api/embed",
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            import io
            captured_stderr = io.StringIO()
            with patch("sys.stderr", captured_stderr):
                launcher.warmup()
            self.assertIn("warmup failed", captured_stderr.getvalue())
            self.assertIn("500", captured_stderr.getvalue())
            self.assertIn("knowledge-ui will start with lexical article search", captured_stderr.getvalue())
        finally:
            server.stop()

    def test_warmup_success(self):
        server = MockHealthServer({"/api/embed": (200, b'{"embedding":[0.1, 0.2]}')})
        base_url = server.start()
        try:
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": f"{base_url}/api/embed",
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            import io
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                launcher.warmup()
            self.assertIn("preloaded (keep_alive=-1)", captured_stdout.getvalue())
        finally:
            server.stop()


class TestRunScript(unittest.TestCase):
    def test_run_sh_help(self):
        res = subprocess.run(["bash", str(RUN_SH), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Usage: run.sh [--env-file FILE]", res.stdout)

    def test_run_sh_unknown_argument(self):
        res = subprocess.run(["bash", str(RUN_SH), "--unrecognized"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("unrecognized argument", res.stderr)

    def test_run_sh_env_file_missing_value(self):
        res = subprocess.run(["bash", str(RUN_SH), "--env-file"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("requires a file path argument", res.stderr)

    def test_run_sh_env_file_not_found(self):
        res = subprocess.run(["bash", str(RUN_SH), "--env-file", "nonexistent.env"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("env file not found", res.stderr)

    def test_run_sh_resolves_env_file_relative_to_caller_cwd_and_preserves_spaces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work_dir = tmp_path / "caller dir"
            work_dir.mkdir()
            env_file = work_dir / "my config.env"
            env_file.write_text("UI_PORT=7776\n", encoding="utf-8")

            # Mock uv to check passed arguments
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            uv_log = tmp_path / "uv.log"
            mock_uv = bin_dir / "uv"
            mock_uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" > "{uv_log}"\n', encoding="utf-8")
            mock_uv.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            res = subprocess.run(
                ["bash", str(RUN_SH), "--env-file", "my config.env"],
                cwd=str(work_dir),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            logged = uv_log.read_text(encoding="utf-8")
            self.assertIn("--env-file", logged)
            self.assertIn(str(env_file), logged)
            self.assertIn("launch.py", logged)


if __name__ == "__main__":
    unittest.main()
