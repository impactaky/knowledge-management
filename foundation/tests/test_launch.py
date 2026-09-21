"""Unit and integration tests for the common runtime launcher and run.sh."""

from __future__ import annotations

import http.server
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CATALOG = ROOT / "examples" / "minimal" / "CATALOG.md"
RUN_SH = ROOT / "foundation" / "tools" / "knowledge-ui" / "run.sh"
UI_DIR = ROOT / "foundation" / "tools" / "knowledge-ui"
LAUNCH_PY = UI_DIR / "launch.py"
CORE_DIR = ROOT / "foundation" / "integrations" / "federation-core"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

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

with patch.dict(os.environ, {"FEDERATION_CATALOG": str(MINIMAL_CATALOG)}):
    import server



def get_free_port() -> int:
    """Reserve and return an unbound loopback port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockHealthServer:
    """Lightweight HTTP server to simulate Meilisearch and Ollama endpoints."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes = routes
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0
        self.received_posts: list[tuple[str, bytes]] = []

    def start(self) -> str:
        routes = self.routes
        received_posts = self.received_posts

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, b"Not Found"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                post_body = self.rfile.read(content_len) if content_len > 0 else b""
                received_posts.append((self.path, post_body))
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
        # Valid loopback hosts
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("127.0.1.1"))
        self.assertTrue(is_loopback_host("127.10.20.30"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("[::1]"))

        # Non-loopback addresses and domain names must be rejected
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("127.attacker.example"))
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

    def test_malformed_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad_catalog = tmp_path / "CATALOG.md"
            bad_catalog.write_text("# Not a valid catalog\nNo packages section\n", encoding="utf-8")
            env = {"FEDERATION_CATALOG": str(bad_catalog)}
            with self.assertRaises(ConfigurationError) as ctx:
                LauncherConfig.from_env(env)
            self.assertIn("Catalog validation failed", str(ctx.exception))

    def test_duplicate_packages_in_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad_catalog = tmp_path / "CATALOG.md"
            pkg_dir = tmp_path / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "INDEX.md").write_text("# Pkg\n", encoding="utf-8")
            bad_catalog.write_text(
                "# Dup Catalog\n## Packages\n"
                "- [pkg1](pkg/INDEX.md) — First\n"
                "- [pkg1](pkg/INDEX.md) — Duplicate\n",
                encoding="utf-8",
            )
            env = {"FEDERATION_CATALOG": str(bad_catalog)}
            with self.assertRaises(ConfigurationError) as ctx:
                LauncherConfig.from_env(env)
            self.assertIn("duplicate package", str(ctx.exception))

    def test_config_validation_is_pure_and_leaves_no_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "runtime_data"
            db_dir = tmp_path / "meili_db"
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_DATA_DIR": str(data_dir),
                "MEILI_DB_PATH": str(db_dir),
                "UI_PORT": "invalid_port_number",
            }
            with self.assertRaises(ConfigurationError):
                LauncherConfig.from_env(env)

            # Verification: neither data_dir nor db_dir must be created on validation failure!
            self.assertFalse(data_dir.exists())
            self.assertFalse(db_dir.exists())

    def test_empty_ui_host_fails(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "UI_HOST": "   ",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("UI_HOST must be a non-empty string", str(ctx.exception))

    def test_invalid_server_numeric_configs_fail_early(self):
        base_env = {"FEDERATION_CATALOG": str(MINIMAL_CATALOG)}

        # Invalid UI_PORT
        with self.assertRaises(ConfigurationError):
            LauncherConfig.from_env({**base_env, "UI_PORT": "70000"})

        # Invalid KNOWLEDGE_WATCH_INTERVAL
        with self.assertRaises(ConfigurationError):
            LauncherConfig.from_env({**base_env, "KNOWLEDGE_WATCH_INTERVAL": "0"})
        with self.assertRaises(ConfigurationError):
            LauncherConfig.from_env({**base_env, "KNOWLEDGE_WATCH_INTERVAL": "not_a_num"})

        # Invalid MARIMO ports (start > end)
        with self.assertRaises(ConfigurationError):
            LauncherConfig.from_env({
                **base_env,
                "KNOWLEDGE_MARIMO_PORT_START": "7900",
                "KNOWLEDGE_MARIMO_PORT_END": "7800",
            })

        # Invalid KNOWLEDGE_MARIMO_IDLE_SECONDS
        with self.assertRaises(ConfigurationError):
            LauncherConfig.from_env({**base_env, "KNOWLEDGE_MARIMO_IDLE_SECONDS": "-10"})

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

    def test_manage_meili_validates_url_scheme_and_rejects_remote(self):
        base = {"FEDERATION_CATALOG": str(MINIMAL_CATALOG), "KNOWLEDGE_MANAGE_MEILI": "1"}

        # HTTPS rejected for local managed native binary
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "MEILI_URL": "https://127.0.0.1:7700"})
        self.assertIn("requires HTTP scheme", str(ctx.exception))

        # Credentials rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "MEILI_URL": "http://user:pass@127.0.0.1:7700"})
        self.assertIn("cannot contain authentication credentials", str(ctx.exception))

        # Path component rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "MEILI_URL": "http://127.0.0.1:7700/v1"})
        self.assertIn("cannot contain a path component", str(ctx.exception))

        # Remote target rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "MEILI_URL": "http://192.168.1.100:7700"})
        self.assertIn("Managed native services must be local", str(ctx.exception))

    def test_manage_meili_default_port_matches_probe_and_bind(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_MEILI": "1",
            "MEILI_URL": "http://127.0.0.1",  # No port specified
        }
        cfg = LauncherConfig.from_env(env)
        self.assertEqual(cfg.meili_bind_addr, "127.0.0.1:80")
        self.assertEqual(cfg.meili_probe_url, "http://127.0.0.1:80")

    def test_manage_ollama_rejects_remote_and_contradictory_endpoints(self):
        base = {"FEDERATION_CATALOG": str(MINIMAL_CATALOG), "KNOWLEDGE_MANAGE_OLLAMA": "1"}

        # Remote host rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "OLLAMA_HOST": "10.0.0.5:11434"})
        self.assertIn("Cannot manage remote Ollama target", str(ctx.exception))

        # Contradictory OLLAMA_HOST vs OLLAMA_EMBED_URL port
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_EMBED_URL": "http://127.0.0.1:11435/api/embed",
            })
        self.assertIn("Contradictory Ollama configuration", str(ctx.exception))

        # Contradictory warmup URL
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "OLLAMA_HOST": "127.0.0.1:11434",
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": "http://127.0.0.1:19999/api/embed",
            })
        self.assertIn("Contradictory Ollama configuration", str(ctx.exception))

    def test_warmup_alone_without_endpoint_fails(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_WARMUP_EMBEDDING": "1",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("requires an explicit endpoint", str(ctx.exception))

    def test_warmup_empty_model_fails(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_WARMUP_EMBEDDING": "1",
            "OLLAMA_WARMUP_URL": "http://127.0.0.1:11434/api/embed",
            "OLLAMA_EMBED_MODEL": "   ",
        }
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env(env)
        self.assertIn("OLLAMA_EMBED_MODEL cannot be empty", str(ctx.exception))

    def test_preserve_externally_managed_remote_embedding_url(self):
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_OLLAMA": "0",
            "OLLAMA_EMBED_URL": "https://remote-gpu.company.internal/api/embed",
        }
        cfg = LauncherConfig.from_env(env)
        self.assertEqual(cfg.ollama_embed_url, "https://remote-gpu.company.internal/api/embed")

    def test_endpoint_ipv6_and_malformed_url_validation(self):
        base = {"FEDERATION_CATALOG": str(MINIMAL_CATALOG)}

        # Managed Meili IPv6 explicitly rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "KNOWLEDGE_MANAGE_MEILI": "1", "MEILI_URL": "http://[::1]:7700"})
        self.assertIn("Managed native services do not support IPv6", str(ctx.exception))

        # Managed Ollama IPv6 explicitly rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "KNOWLEDGE_MANAGE_OLLAMA": "1", "OLLAMA_HOST": "[::1]:11434"})
        self.assertIn("Managed native services do not support IPv6", str(ctx.exception))
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "KNOWLEDGE_MANAGE_OLLAMA": "1", "OLLAMA_HOST": "::1:11434"})
        self.assertIn("Managed native services do not support IPv6", str(ctx.exception))

        # Nonnumeric / invalid port in OLLAMA_HOST
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "KNOWLEDGE_MANAGE_OLLAMA": "1", "OLLAMA_HOST": "127.0.0.1:invalid"})
        self.assertIn("Invalid port in OLLAMA_HOST", str(ctx.exception))

        # Managed Ollama host with HTTPS embed URL rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "KNOWLEDGE_MANAGE_OLLAMA": "1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_EMBED_URL": "https://127.0.0.1:11434/api/embed",
            })
        self.assertIn("requires HTTP scheme", str(ctx.exception))

        # Managed Ollama host with wrong path embed URL rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "KNOWLEDGE_MANAGE_OLLAMA": "1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_EMBED_URL": "http://127.0.0.1:11434/wrong/path",
            })
        self.assertIn("must have path '/api/embed'", str(ctx.exception))

        # External warmup URL with non-http scheme (file://) rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": "file:///etc/passwd",
            })
        self.assertIn("requires HTTP or HTTPS scheme", str(ctx.exception))

        # External warmup URL with wrong path rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": "http://127.0.0.1:11434/not/embed",
            })
        self.assertIn("must have path '/api/embed'", str(ctx.exception))

        # KNOWLEDGE_WATCH_INTERVAL="" rejected
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({**base, "KNOWLEDGE_WATCH_INTERVAL": ""})
        self.assertIn("Invalid KNOWLEDGE_WATCH_INTERVAL", str(ctx.exception))

        # MEILI_URL=http://127.0.0.1:0 usable as disabled external test target
        cfg = LauncherConfig.from_env({**base, "KNOWLEDGE_MANAGE_MEILI": "0", "MEILI_URL": "http://127.0.0.1:0"})
        self.assertEqual(cfg.meili_url, "http://127.0.0.1:0")

        # Derived embed URL with omitted port defaults to 80 (not 11434) and contradicts 11434
        with self.assertRaises(ConfigurationError) as ctx:
            LauncherConfig.from_env({
                **base,
                "KNOWLEDGE_MANAGE_OLLAMA": "1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_EMBED_URL": "http://127.0.0.1/api/embed",
            })
        self.assertIn("Contradictory Ollama configuration", str(ctx.exception))


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
            self.assertFalse(is_meili_healthy(f"http://127.0.0.1:{get_free_port()}"))

            parsed = urlparse(base_url)
            self.assertTrue(is_ollama_healthy(f"127.0.0.1:{parsed.port}"))
            self.assertTrue(is_ollama_healthy(base_url))
            self.assertFalse(is_ollama_healthy(f"127.0.0.1:{get_free_port()}"))
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

            self.assertEqual(len(launcher.owned_services), 0)
            launcher.cleanup()

            # External reused service must still be alive!
            self.assertTrue(is_meili_healthy(base_url))
        finally:
            server.stop()

    def test_process_group_cleanup_and_reaps_grandchildren(self):
        """Verify that terminating an owned service cleans up its process group and grandchildren."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            grandchild_pid_file = tmp_path / "grandchild.pid"
            mock_bin = tmp_path / "spawner"
            mock_bin_code = f"""#!/usr/bin/env python3
import http.server, os, subprocess, sys, time

p = subprocess.Popen([
    sys.executable, "-c",
    "import os, time; open('{grandchild_pid_file}', 'w').write(str(os.getpid())); time.sleep(100)"
])

args = sys.argv[1:]
addr = "127.0.0.1:7700"
if "--http-addr" in args:
    addr = args[args.index("--http-addr") + 1]
host, port_str = addr.rsplit(":", 1)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{{"status":"available"}}')
    def log_message(self, *a):
        pass

s = http.server.HTTPServer((host, int(port_str)), H)
s.serve_forever()
"""
            mock_bin.write_text(mock_bin_code, encoding="utf-8")
            mock_bin.chmod(0o755)

            port = get_free_port()
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

            # Wait for grandchild PID file
            for _ in range(50):
                if grandchild_pid_file.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(grandchild_pid_file.exists())
            grandchild_pid = int(grandchild_pid_file.read_text().strip())

            # Verify both are running
            self.assertIsNone(proc.poll())
            os.kill(grandchild_pid, 0)  # Should not raise

            # Terminate via cleanup
            launcher.cleanup()

            # Verify leader is terminated
            self.assertIsNotNone(proc.poll())

            # Verify grandchild was terminated by process group kill!
            deadline = time.monotonic() + 5.0
            grandchild_dead = False
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                    try:
                        with open(f"/proc/{grandchild_pid}/status") as f:
                            status_txt = f.read()
                            if "State:\tZ (zombie)" in status_txt or "State: Z" in status_txt:
                                grandchild_dead = True
                                break
                    except (FileNotFoundError, ProcessLookupError):
                        grandchild_dead = True
                        break
                    time.sleep(0.1)
                except ProcessLookupError:
                    grandchild_dead = True
                    break
            self.assertTrue(grandchild_dead, "Grandchild survived process group termination!")

    def test_stubborn_grandchild_reaped_on_cleanup(self):
        """Verify that a stubborn grandchild ignoring SIGTERM is killed via SIGKILL when leader exits."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            grandchild_pid_file = tmp_path / "stubborn.pid"
            mock_bin = tmp_path / "stubborn_spawner"
            mock_bin_code = f"""#!/usr/bin/env python3
import http.server, os, signal, subprocess, sys, time

# Grandchild explicitly ignores SIGTERM!
p = subprocess.Popen([
    sys.executable, "-c",
    "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open('{grandchild_pid_file}', 'w').write(str(os.getpid())); time.sleep(100)"
])

args = sys.argv[1:]
addr = "127.0.0.1:7700"
if "--http-addr" in args:
    addr = args[args.index("--http-addr") + 1]
host, port_str = addr.rsplit(":", 1)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{{"status":"available"}}')
    def log_message(self, *a):
        pass

s = http.server.HTTPServer((host, int(port_str)), H)
s.serve_forever()
"""
            mock_bin.write_text(mock_bin_code, encoding="utf-8")
            mock_bin.chmod(0o755)

            port = get_free_port()
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": f"http://127.0.0.1:{port}",
                "MEILI_BIN": str(mock_bin),
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            launcher.start_meili()
            self.assertEqual(len(launcher.owned_services), 1)

            for _ in range(50):
                if grandchild_pid_file.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(grandchild_pid_file.exists())
            grandchild_pid = int(grandchild_pid_file.read_text().strip())

            # Verify running
            os.kill(grandchild_pid, 0)

            # Cleanup must wait grace period, send SIGKILL and terminate stubborn grandchild
            launcher.cleanup()

            deadline = time.monotonic() + 5.0
            grandchild_dead = False
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                    try:
                        with open(f"/proc/{grandchild_pid}/status") as f:
                            status_txt = f.read()
                            if "State:\tZ (zombie)" in status_txt or "State: Z" in status_txt:
                                grandchild_dead = True
                                break
                    except (FileNotFoundError, ProcessLookupError):
                        grandchild_dead = True
                        break
                    time.sleep(0.1)
                except ProcessLookupError:
                    grandchild_dead = True
                    break
            self.assertTrue(grandchild_dead, "Stubborn grandchild survived SIGKILL cleanup!")

    def test_server_shutdown_cleans_up_stubborn_marimo_session(self):
        """Verify server shutdown event cleans up process group of live marimo session with stubborn child."""
        import server
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            grandchild_pid_file = tmp_path / "marimo_stubborn.pid"
            child_script = (
                "import os, signal, subprocess, sys, time; "
                f"c = subprocess.Popen([sys.executable, '-c', 'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(\"{grandchild_pid_file}\", \"w\").write(str(os.getpid())); time.sleep(100)']); "
                "time.sleep(100)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", child_script],
                start_new_session=True,
            )
            for _ in range(50):
                if grandchild_pid_file.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(grandchild_pid_file.exists())
            grandchild_pid = int(grandchild_pid_file.read_text().strip())

            server.LIVE_MARIMO_PROCESSES["test_stubborn"] = {"process": proc, "last_used": time.time()}

            server.shutdown_event()

            self.assertEqual(len(server.LIVE_MARIMO_PROCESSES), 0)
            self.assertIsNotNone(proc.poll())

            deadline = time.monotonic() + 5.0
            grandchild_dead = False
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                    try:
                        with open(f"/proc/{grandchild_pid}/status") as f:
                            status_txt = f.read()
                            if "State:\tZ (zombie)" in status_txt or "State: Z" in status_txt:
                                grandchild_dead = True
                                break
                    except (FileNotFoundError, ProcessLookupError):
                        grandchild_dead = True
                        break
                    time.sleep(0.1)
                except ProcessLookupError:
                    grandchild_dead = True
                    break
            self.assertTrue(grandchild_dead, "Grandchild survived server shutdown cleanup!")

    def test_idle_marimo_cleanup_when_leader_exited(self):
        """Verify marimo cleanup handles already-exited leaders and cleans up lingering process group."""
        import server
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"], start_new_session=True)
        proc.wait()
        server.LIVE_MARIMO_PROCESSES["test_exited"] = {"process": proc, "last_used": time.time() - 9999}

        launch.terminate_process_group(proc, grace_period=0.5)
        server.LIVE_MARIMO_PROCESSES.pop("test_exited", None)
        self.assertNotIn("test_exited", server.LIVE_MARIMO_PROCESSES)

    def test_readiness_timeout_cleans_up_process(self):
        """Verify that when a managed service fails to become ready within timeout, owned process is cleaned up."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            unhealthy_bin = tmp_path / "unhealthy_bin"
            unhealthy_code = """#!/usr/bin/env python3
import http.server, sys
args = sys.argv[1:]
addr = args[args.index("--http-addr") + 1] if "--http-addr" in args else "127.0.0.1:7700"
host, port_str = addr.rsplit(":", 1)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer((host, int(port_str)), H).serve_forever()
"""
            unhealthy_bin.write_text(unhealthy_code, encoding="utf-8")
            unhealthy_bin.chmod(0o755)

            port = get_free_port()
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": f"http://127.0.0.1:{port}",
                "MEILI_BIN": str(unhealthy_bin),
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            start_time = time.monotonic()
            def mock_monotonic():
                nonlocal start_time
                start_time += 10.0
                return start_time

            with patch("time.monotonic", side_effect=mock_monotonic):
                with self.assertRaises(RuntimeError) as ctx:
                    launcher.start_meili()

            self.assertIn("failed to become ready", str(ctx.exception))
            self.assertEqual(len(launcher.owned_services), 0)

    def test_warmup_success(self):
        server = MockHealthServer({"/api/embed": (200, b'{"embeddings":[[0.1, 0.2]]}')})
        base_url = server.start()
        try:
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": f"{base_url}/api/embed",
                "OLLAMA_EMBED_MODEL": "bge-m3",
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                launcher.warmup()
            self.assertIn("preloaded (keep_alive=-1)", captured_stdout.getvalue())

            self.assertEqual(len(server.received_posts), 1)
            path, body_bytes = server.received_posts[0]
            self.assertEqual(path, "/api/embed")
            payload = json.loads(body_bytes.decode("utf-8"))
            self.assertEqual(payload.get("model"), "bge-m3")
            self.assertEqual(payload.get("input"), "knowledge federation search warmup")
            self.assertEqual(payload.get("keep_alive"), -1)
        finally:
            server.stop()

    def test_warmup_fallback_on_error(self):
        server = MockHealthServer({"/api/embed": (500, b'{"error":"gpu out of memory"}')})
        base_url = server.start()
        try:
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_WARMUP_EMBEDDING": "1",
                "OLLAMA_WARMUP_URL": f"{base_url}/api/embed",
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            captured_stderr = io.StringIO()
            with patch("sys.stderr", captured_stderr):
                launcher.warmup()
            output = captured_stderr.getvalue()
            self.assertTrue("warmup returned status 500" in output or "warmup failed" in output)
            self.assertIn("knowledge-ui will start with lexical article search", output)
        finally:
            server.stop()

    def test_meili_startup_failure_cleans_up_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            failing_bin = tmp_path / "failing_bin"
            failing_bin.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            failing_bin.chmod(0o755)

            port = get_free_port()
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": f"http://127.0.0.1:{port}",
                "MEILI_BIN": str(failing_bin),
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            with self.assertRaises(RuntimeError) as ctx:
                launcher.run()
            self.assertIn("exited prematurely with code 42", str(ctx.exception))
            # Verify cleanup was called and no services remained owned
            self.assertEqual(len(launcher.owned_services), 0)

    def test_ollama_missing_binary_uses_temp_port_and_warns(self):
        temp_port = get_free_port()
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "KNOWLEDGE_MANAGE_OLLAMA": "1",
            "OLLAMA_HOST": f"127.0.0.1:{temp_port}",
            "OLLAMA_BIN": "/nonexistent/path/to/ollama",
        }
        cfg = LauncherConfig.from_env(env)
        launcher = Launcher(cfg)

        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            ready = launcher.start_ollama()
        self.assertFalse(ready)
        self.assertIn("Warning: ollama not found", captured_stderr.getvalue())

    def test_ollama_popen_oserror_warns_and_continues(self):
        temp_port = get_free_port()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_OLLAMA": "1",
                "OLLAMA_HOST": f"127.0.0.1:{temp_port}",
                "OLLAMA_BIN": "ollama",
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            captured_stderr = io.StringIO()
            with patch("shutil.which", return_value="/usr/bin/ollama"):
                with patch("subprocess.Popen", side_effect=OSError("Exec format error")):
                    with patch("sys.stderr", captured_stderr):
                        ready = launcher.start_ollama()

            self.assertFalse(ready)
            self.assertIn("Warning: Failed to start Ollama", captured_stderr.getvalue())

    def test_master_key_passed_in_env_not_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            port = get_free_port()
            env = {
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": f"http://127.0.0.1:{port}",
                "MEILI_MASTER_KEY": "super-secret-master-key",
                "MEILI_BIN": "meilisearch",
                "KNOWLEDGE_DATA_DIR": str(tmp_path),
            }
            cfg = LauncherConfig.from_env(env)
            launcher = Launcher(cfg)

            observed_cmd = None
            observed_env = None

            def mock_popen(cmd, env=None, **kwargs):
                nonlocal observed_cmd, observed_env
                observed_cmd = cmd
                observed_env = env
                m = unittest.mock.MagicMock()
                m.pid = 99999
                m.poll.return_value = None
                return m

            with patch("shutil.which", return_value="/usr/bin/meilisearch"):
                with patch("subprocess.Popen", side_effect=mock_popen):
                    with patch("launch.is_meili_healthy", side_effect=[False, True]):
                        launcher.start_meili()

            self.assertNotIn("--master-key", observed_cmd)
            self.assertNotIn("super-secret-master-key", observed_cmd)
            self.assertEqual(observed_env.get("MEILI_MASTER_KEY"), "super-secret-master-key")

    def test_child_environment_normalization_and_non_mutation(self):
        """Verify that UI child environment receives normalized '1'/'0' and parent os.environ is untouched."""
        env = {
            "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
            "MEILI_URL": "http://127.0.0.1:7700",
            "KNOWLEDGE_AUTO_INDEX": "true",
            "KNOWLEDGE_ENABLE_LIVE_MARIMO": "yes",
        }
        with patch("importlib.util.find_spec", return_value=unittest.mock.MagicMock()):
            cfg = LauncherConfig.from_env(env)
        launcher = Launcher(cfg)

        observed_env = None

        def mock_popen(cmd, env=None, **kwargs):
            nonlocal observed_env
            observed_env = env
            m = unittest.mock.MagicMock()
            m.pid = 88888
            m.wait.return_value = 0
            m.poll.return_value = 0
            return m

        # Ensure parent os.environ doesn't have these
        parent_before = dict(os.environ)

        with patch("launch.terminate_process_group"), patch("subprocess.Popen", side_effect=mock_popen):
            exit_code = launcher.run()
            self.assertEqual(exit_code, 0)

        # Child environment must receive normalized '1'
        self.assertEqual(observed_env.get("KNOWLEDGE_AUTO_INDEX"), "1")
        self.assertEqual(observed_env.get("KNOWLEDGE_ENABLE_LIVE_MARIMO"), "1")

        # Parent environment must not be mutated
        self.assertEqual(os.environ.get("KNOWLEDGE_AUTO_INDEX"), parent_before.get("KNOWLEDGE_AUTO_INDEX"))

    def test_real_launcher_subprocess_sigterm_reaps_owned_backends_while_waiting_for_ui(self):
        """Regression test for signal arrival while launcher is blocked in UI Popen.wait().
        Verifies that launcher exits cleanly, terminates owned backend process groups,
        and leaves reused external services untouched.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            meili_pid_file = tmp_path / "managed_meili.pid"
            fake_meili = tmp_path / "fake_meilisearch"
            fake_meili_code = f"""#!/usr/bin/env python3
import http.server, os, sys
with open('{meili_pid_file}', 'w') as f:
    f.write(str(os.getpid()))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{{"status":"available"}}')
    def log_message(self, *a): pass
addr = sys.argv[sys.argv.index("--http-addr") + 1] if "--http-addr" in sys.argv else "127.0.0.1:7700"
host, port_str = addr.rsplit(":", 1)
s = http.server.HTTPServer((host, int(port_str)), H)
s.serve_forever()
"""
            fake_meili.write_text(fake_meili_code, encoding="utf-8")
            fake_meili.chmod(0o755)

            # 1. Start an external reused service to verify it remains untouched
            external_port = get_free_port()
            fake_external = tmp_path / "fake_external.py"
            fake_external.write_text(f"""#!/usr/bin/env python3
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{{}}')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', {external_port}), H).serve_forever()
""", encoding="utf-8")
            external_proc = subprocess.Popen([sys.executable, str(fake_external)])

            # Wait for external service to be responsive
            deadline = time.monotonic() + 5.0
            external_ready = False
            while time.monotonic() < deadline:
                try:
                    with urlopen(f"http://127.0.0.1:{external_port}", timeout=0.2) as r:
                        if r.status == 200:
                            external_ready = True
                            break
                except Exception:
                    time.sleep(0.05)
            self.assertTrue(external_ready, "External service failed to start")

            # 2. Configure launcher environment for managed meili + UI
            meili_port = get_free_port()
            ui_port = get_free_port()
            launcher_env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("FEDERATION_", "KNOWLEDGE_", "MEILI_", "OLLAMA_", "UI_"))
            }
            launcher_env.update({
                "FEDERATION_CATALOG": str(MINIMAL_CATALOG),
                "KNOWLEDGE_MANAGE_MEILI": "1",
                "MEILI_URL": f"http://127.0.0.1:{meili_port}",
                "MEILI_BIN": str(fake_meili),
                "KNOWLEDGE_DATA_DIR": str(tmp_path / "runtime_data"),
                "UI_HOST": "127.0.0.1",
                "UI_PORT": str(ui_port),
            })

            launcher_proc = subprocess.Popen(
                [sys.executable, str(LAUNCH_PY)],
                cwd=str(UI_DIR),
                env=launcher_env,
            )

            try:
                # 3. Wait for UI to become fully responsive (/api/tree)
                deadline = time.monotonic() + 15.0
                ui_ready = False
                while time.monotonic() < deadline:
                    if launcher_proc.poll() is not None:
                        break
                    try:
                        req = Request(f"http://127.0.0.1:{ui_port}/api/tree")
                        with urlopen(req, timeout=0.5) as resp:
                            if resp.status == 200:
                                ui_ready = True
                                break
                    except Exception:
                        time.sleep(0.1)

                self.assertTrue(ui_ready, f"UI failed to start: exit code {launcher_proc.poll()}")
                self.assertIsNone(launcher_proc.poll())

                # Wait for managed backend PID file
                for _ in range(50):
                    if meili_pid_file.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(meili_pid_file.exists())
                managed_pid = int(meili_pid_file.read_text().strip())

                # Verify managed backend is running
                os.kill(managed_pid, 0)

                # 4. Send SIGTERM to the launcher while it is waiting on the UI process
                launcher_proc.terminate()

                # 5. Assert launcher exits within bound
                launcher_exit = launcher_proc.wait(timeout=10.0)
                self.assertIsNotNone(launcher_exit)

                # 6. Assert owned managed backend died
                deadline = time.monotonic() + 5.0
                backend_dead = False
                while time.monotonic() < deadline:
                    try:
                        os.kill(managed_pid, 0)
                        try:
                            with open(f"/proc/{managed_pid}/status") as f:
                                status_txt = f.read()
                                if "State:\tZ" in status_txt or "State: Z" in status_txt:
                                    backend_dead = True
                                    break
                        except (FileNotFoundError, ProcessLookupError):
                            backend_dead = True
                            break
                        time.sleep(0.1)
                    except ProcessLookupError:
                        backend_dead = True
                        break
                self.assertTrue(backend_dead, f"Managed backend PID {managed_pid} survived launcher SIGTERM!")

                # 7. Assert external reused service was NOT touched
                self.assertIsNone(external_proc.poll())
                os.kill(external_proc.pid, 0)

            finally:
                # Ensure all test processes are cleaned up
                if launcher_proc.poll() is None:
                    launcher_proc.kill()
                    launcher_proc.wait()
                if external_proc.poll() is None:
                    external_proc.kill()
                    external_proc.wait()
                if meili_pid_file.exists():
                    try:
                        pid = int(meili_pid_file.read_text().strip())
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass


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

    def test_run_sh_escapes_spaces_and_preserves_exact_argv_boundaries(self):
        for name in (
            "my config.env",
            "one\\name.env",
            "one\\ name.env",
            "tab\tname.env",
            "cr\rname.env",
            "lf\nname.env",
            "crlf\r\nname.env",
        ):
            with tempfile.TemporaryDirectory(prefix="test spaces ") as tmp:
                tmp_path = Path(tmp)
                work_dir = tmp_path / "caller dir"
                work_dir.mkdir()
                env_file = work_dir / name
                env_file.write_text("UI_PORT=7776\n", encoding="utf-8")

                bin_dir = tmp_path / "bin"
                bin_dir.mkdir()
                argv_log = tmp_path / "uv_argv.json"

                # Mock uv script dumping exact sys.argv[1:] as JSON
                mock_uv = bin_dir / "uv"
                mock_uv.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, sys\n"
                    f"with open('{argv_log}', 'w') as f:\n"
                    "    json.dump(sys.argv[1:], f)\n",
                    encoding="utf-8",
                )
                mock_uv.chmod(0o755)

                env = os.environ.copy()
                env["PATH"] = f"{bin_dir}:{env['PATH']}"
                res = subprocess.run(
                    ["bash", str(RUN_SH), "--env-file", name],
                    cwd=str(work_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 0, f"Failed for {name!r}: {res.stderr}")
                args = json.loads(argv_log.read_text(encoding="utf-8"))

                self.assertIn("--env-file", args)
                idx = args.index("--env-file")
                expected_escaped = (
                    str(env_file)
                    .replace("\\", "\\\\")
                    .replace(" ", "\\ ")
                    .replace("\t", "\\\t")
                    .replace("\r", "\\\r")
                    .replace("\n", "\\\n")
                )
                self.assertEqual(args[idx + 1], expected_escaped)
                self.assertEqual(args[-2:], ["python", "launch.py"])


if __name__ == "__main__":
    unittest.main()
