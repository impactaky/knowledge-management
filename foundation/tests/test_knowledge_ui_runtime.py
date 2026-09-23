"""Launch the real run.sh against a synthetic Catalog and probe its HTTP endpoint."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from federation_support import FOUNDATION, ROOT

RUN_SCRIPT = FOUNDATION / "tools" / "knowledge-ui" / "run.sh"


class KnowledgeUiRuntimeTest(unittest.TestCase):
    def test_run_script_reaches_real_http_endpoint(self) -> None:
        if shutil.which("uv") is None:
            self.skipTest("uv is not available in the test environment")
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            try:
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
            except PermissionError as exc:
                self.skipTest(f"local sockets are unavailable in this sandbox: {exc}")

            # UI startup schedules reindexing: an HTTP fixture must never use
            # the user's canonical Catalog or the live entries/chunks indexes.
            catalog = fixture / "CATALOG.md"
            pkg = fixture / "pkg"
            pkg.mkdir()
            (pkg / "INDEX.md").write_text("# Synthetic\n", encoding="utf-8")
            catalog.write_text("# Test Catalog\n## パッケージ\n- [synthetic](pkg/INDEX.md) — Synthetic\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(fixture / "home"),
                    "UV_CACHE_DIR": str(fixture / "uv-cache"),
                    "UI_HOST": "127.0.0.1",
                    "UI_PORT": str(port),
                    "FEDERATION_CATALOG": str(catalog),
                    "MEILI_URL": "http://127.0.0.1:0",
                    "KNOWLEDGE_MANAGE_MEILI": "0",
                    "KNOWLEDGE_MANAGE_OLLAMA": "0",
                    "KNOWLEDGE_WARMUP_EMBEDDING": "0",
                    "KNOWLEDGE_AUTO_INDEX": "0",
                    "KNOWLEDGE_ENABLE_LIVE_MARIMO": "0",
                }
            )
            process = subprocess.Popen(
                ["bash", str(RUN_SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 15
                response_body = ""
                last_error: Exception | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                            response_body = response.read().decode("utf-8")
                        break
                    except (URLError, TimeoutError) as exc:
                        last_error = exc
                        time.sleep(0.1)
                if not response_body:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    stdout, stderr = process.communicate()
                    self.fail(
                        f"knowledge-ui did not become reachable: {last_error}\n"
                        f"stdout={stdout}\nstderr={stderr}"
                    )
                self.assertIn("<title>Knowledge Browser</title>", response_body)
                with urlopen(f"http://127.0.0.1:{port}/api/tree", timeout=1) as response:
                    tree_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual([p["name"] for p in tree_payload.get("packages", [])], ["synthetic"])
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
