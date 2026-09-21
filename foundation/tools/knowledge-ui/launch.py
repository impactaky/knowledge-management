"""Common runtime launcher for Knowledge Browser UI and local backends."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

UI_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(__file__).resolve().parents[3]


class ConfigurationError(Exception):
    """Raised when configuration validation fails before service startup."""


def parse_bool(name: str, val: str | None, default: bool = False) -> bool:
    """Parse accepted boolean values consistently: 1/0, true/false, yes/no, on/off."""
    if val is None:
        return default
    s = val.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("", "0", "false", "no", "off"):
        return False
    raise ConfigurationError(
        f"Invalid boolean value for {name}: {val!r}. "
        "Accepted values: 1/0, true/false, yes/no, on/off."
    )


def is_loopback_host(host: str | None) -> bool:
    """Check if the given host address is a local loopback interface."""
    if not host:
        return False
    h = host.strip().lower()
    if h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    if h.startswith("127."):
        return True
    return False


def is_meili_healthy(url: str, api_key: str | None = None, timeout: float = 1.0) -> bool:
    """Probe Meilisearch readiness endpoint."""
    target = url.rstrip("/") + "/health"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(target, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_ollama_healthy(host_or_url: str, timeout: float = 1.0) -> bool:
    """Probe Ollama readiness endpoint."""
    if "://" not in host_or_url:
        target = f"http://{host_or_url}/api/tags"
    else:
        target = host_or_url.rstrip("/") + "/api/tags"
    req = Request(target)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


@dataclass
class LauncherConfig:
    catalog_path: Path
    ui_host: str
    ui_port: int
    data_dir: Path
    auto_index: bool
    enable_live_marimo: bool
    marimo_host: str
    manage_meili: bool
    meili_url: str | None
    meili_bin: str
    meili_db_path: Path | None
    meili_api_key: str | None
    meili_master_key: str | None
    manage_ollama: bool
    ollama_bin: str
    ollama_host: str
    ollama_embed_url: str | None
    ollama_models: str | None
    warmup_embedding: bool
    ollama_embed_model: str
    ollama_warmup_url: str | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LauncherConfig:
        if env is None:
            env = dict(os.environ)

        # 1. Catalog validation (FEDERATION_CATALOG takes precedence over KNOWLEDGE_CATALOG)
        raw_catalog = env.get("FEDERATION_CATALOG") or env.get("KNOWLEDGE_CATALOG")
        if not raw_catalog or not raw_catalog.strip():
            raise ConfigurationError(
                "Missing catalog configuration: neither FEDERATION_CATALOG nor "
                "KNOWLEDGE_CATALOG is set. Set FEDERATION_CATALOG to the path of CATALOG.md."
            )
        catalog_path = Path(raw_catalog.strip()).expanduser().resolve()
        if not catalog_path.exists() or not catalog_path.is_file():
            raise ConfigurationError(
                f"Configured catalog path does not exist or is not a file: {catalog_path}"
            )
        try:
            catalog_path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(f"Cannot read configured catalog at {catalog_path}: {exc}")

        # 2. Boolean switches
        manage_meili = parse_bool("KNOWLEDGE_MANAGE_MEILI", env.get("KNOWLEDGE_MANAGE_MEILI"), False)
        manage_ollama = parse_bool("KNOWLEDGE_MANAGE_OLLAMA", env.get("KNOWLEDGE_MANAGE_OLLAMA"), False)
        warmup_embedding = parse_bool("KNOWLEDGE_WARMUP_EMBEDDING", env.get("KNOWLEDGE_WARMUP_EMBEDDING"), False)
        auto_index = parse_bool("KNOWLEDGE_AUTO_INDEX", env.get("KNOWLEDGE_AUTO_INDEX"), False)
        enable_live_marimo = parse_bool("KNOWLEDGE_ENABLE_LIVE_MARIMO", env.get("KNOWLEDGE_ENABLE_LIVE_MARIMO"), False)

        # 3. UI binding & data directory
        ui_host = env.get("UI_HOST", "127.0.0.1").strip()
        ui_port_str = env.get("UI_PORT", "7776").strip()
        try:
            ui_port = int(ui_port_str)
            if not (1 <= ui_port <= 65535):
                raise ValueError()
        except ValueError:
            raise ConfigurationError(f"Invalid UI_PORT: {ui_port_str!r}. Must be an integer between 1 and 65535.")

        data_dir_raw = env.get("KNOWLEDGE_DATA_DIR")
        if data_dir_raw:
            data_dir = Path(data_dir_raw).expanduser().resolve()
        else:
            data_dir = (REPO_DIR / ".data").resolve()
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(f"Failed to create runtime data directory {data_dir}: {exc}")

        # 4. Live marimo
        marimo_host = env.get("KNOWLEDGE_MARIMO_HOST") or ui_host
        if enable_live_marimo:
            if importlib.util.find_spec("marimo") is None:
                raise ConfigurationError(
                    "Live notebook execution is enabled (KNOWLEDGE_ENABLE_LIVE_MARIMO=1), "
                    "but optional dependency 'marimo' is not installed. "
                    "Install with 'uv sync --locked --extra notebook'."
                )

        # 5. Meilisearch configuration
        meili_url = env.get("MEILI_URL")
        if meili_url:
            meili_url = meili_url.strip()
            parsed_meili = urlparse(meili_url)
            if not parsed_meili.scheme or not parsed_meili.netloc:
                raise ConfigurationError(f"Invalid MEILI_URL: {meili_url!r}")
        else:
            meili_url = None

        if auto_index and not meili_url:
            raise ConfigurationError("KNOWLEDGE_AUTO_INDEX requires MEILI_URL to be set.")

        meili_bin = env.get("MEILI_BIN", "meilisearch").strip()
        meili_db_raw = env.get("MEILI_DB_PATH")
        meili_db_path = Path(meili_db_raw).expanduser().resolve() if meili_db_raw else (data_dir / "meili_data")
        meili_api_key = env.get("MEILI_API_KEY")
        meili_master_key = env.get("MEILI_MASTER_KEY")

        if manage_meili:
            if not meili_url:
                raise ConfigurationError("KNOWLEDGE_MANAGE_MEILI requires MEILI_URL to be explicitly configured.")
            parsed_meili = urlparse(meili_url)
            if not is_loopback_host(parsed_meili.hostname):
                raise ConfigurationError(
                    f"Cannot manage remote Meilisearch target: {meili_url}. "
                    "Managed native services must be local (loopback)."
                )

        # 6. Ollama configuration
        ollama_bin = env.get("OLLAMA_BIN", "ollama").strip()
        ollama_host = env.get("OLLAMA_HOST", "").strip()
        ollama_embed_url = env.get("OLLAMA_EMBED_URL", "").strip() or None
        ollama_models = env.get("OLLAMA_MODELS", "").strip() or None

        if not ollama_host:
            if ollama_embed_url:
                parsed_embed = urlparse(ollama_embed_url)
                ollama_host = f"{parsed_embed.hostname}:{parsed_embed.port or 11434}"
            else:
                ollama_host = "127.0.0.1:11434"

        host_part = ollama_host.split(":")[0]
        if manage_ollama:
            if not is_loopback_host(host_part):
                raise ConfigurationError(
                    f"Cannot manage remote Ollama target: {ollama_host}. "
                    "Managed native services must be local (loopback)."
                )
            if ollama_embed_url:
                parsed_embed = urlparse(ollama_embed_url)
                if not is_loopback_host(parsed_embed.hostname):
                    raise ConfigurationError(
                        f"Cannot manage remote Ollama embedding target: {ollama_embed_url}. "
                        "Managed native services must be local (loopback)."
                    )

        # 7. Warmup configuration
        ollama_embed_model = env.get("OLLAMA_EMBED_MODEL", "bge-m3").strip()
        ollama_warmup_url = env.get("OLLAMA_WARMUP_URL", "").strip() or None
        if not ollama_warmup_url and ollama_embed_url:
            ollama_warmup_url = ollama_embed_url
        elif not ollama_warmup_url:
            ollama_warmup_url = f"http://{ollama_host}/api/embed"

        return cls(
            catalog_path=catalog_path,
            ui_host=ui_host,
            ui_port=ui_port,
            data_dir=data_dir,
            auto_index=auto_index,
            enable_live_marimo=enable_live_marimo,
            marimo_host=marimo_host,
            manage_meili=manage_meili,
            meili_url=meili_url,
            meili_bin=meili_bin,
            meili_db_path=meili_db_path,
            meili_api_key=meili_api_key,
            meili_master_key=meili_master_key,
            manage_ollama=manage_ollama,
            ollama_bin=ollama_bin,
            ollama_host=ollama_host,
            ollama_embed_url=ollama_embed_url,
            ollama_models=ollama_models,
            warmup_embedding=warmup_embedding,
            ollama_embed_model=ollama_embed_model,
            ollama_warmup_url=ollama_warmup_url,
        )


class Launcher:
    """Orchestrates readiness checks, managed backends, warmup, and UI lifecycle."""

    def __init__(self, config: LauncherConfig):
        self.config = config
        self.owned_services: list[subprocess.Popen] = []
        self.ui_process: subprocess.Popen | None = None
        self._cleaned_up = False

    def _terminate_process(self, proc: subprocess.Popen, timeout: float = 5.0) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
        except Exception:
            pass

    def cleanup(self) -> None:
        """Terminate UI and owned services. Never terminates external reused processes."""
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self.ui_process is not None:
            self._terminate_process(self.ui_process)
            self.ui_process = None

        for proc in reversed(self.owned_services):
            self._terminate_process(proc)
        self.owned_services.clear()

    def start_meili(self) -> None:
        url = self.config.meili_url
        assert url is not None
        if is_meili_healthy(url, self.config.meili_api_key):
            parsed = urlparse(url)
            print(f"Meilisearch already running at {parsed.netloc}")
            return

        bin_path = shutil.which(self.config.meili_bin)
        if not bin_path:
            p = Path(self.config.meili_bin)
            if p.is_file() and os.access(p, os.X_OK):
                bin_path = str(p.resolve())
        if not bin_path:
            raise RuntimeError(f"Meilisearch binary not found or not executable at {self.config.meili_bin}")

        assert self.config.meili_db_path is not None
        self.config.meili_db_path.mkdir(parents=True, exist_ok=True)
        log_path = self.config.data_dir / "meilisearch.log"
        log_file = log_path.open("ab")

        parsed = urlparse(url)
        addr = f"{parsed.hostname}:{parsed.port or 7700}"

        cmd = [
            bin_path,
            "--db-path", str(self.config.meili_db_path),
            "--http-addr", addr,
            "--no-analytics",
            "--experimental-allowed-ip-networks", "127.0.0.1/32",
        ]
        if self.config.meili_master_key:
            cmd.extend(["--master-key", self.config.meili_master_key])

        print(f"Starting Meilisearch at {addr}...")
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()
        self.owned_services.append(proc)
        print(f"Meilisearch PID: {proc.pid}")

        deadline = time.monotonic() + 20.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Meilisearch process exited prematurely with code {proc.returncode}. "
                    f"Check log at {log_path}"
                )
            if is_meili_healthy(url, self.config.meili_api_key):
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            raise RuntimeError(
                f"Meilisearch failed to become ready within 20s at {url}. "
                f"Check log at {log_path}"
            )
        print("Meilisearch ready.")

    def start_ollama(self) -> bool:
        host = self.config.ollama_host
        if is_ollama_healthy(host):
            print(f"Ollama already running at {host}")
            return True

        bin_path = shutil.which(self.config.ollama_bin)
        if not bin_path:
            p = Path(self.config.ollama_bin)
            if p.is_file() and os.access(p, os.X_OK):
                bin_path = str(p.resolve())
        if not bin_path:
            print(f"Warning: ollama not found at {self.config.ollama_bin} — hybrid search unavailable", file=sys.stderr)
            return False

        log_path = self.config.data_dir / "ollama.log"
        log_file = log_path.open("ab")

        env = dict(os.environ)
        env["OLLAMA_HOST"] = host
        if self.config.ollama_models:
            env["OLLAMA_MODELS"] = self.config.ollama_models

        print(f"Starting Ollama at {host}...")
        proc = subprocess.Popen(
            [bin_path, "serve"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()
        self.owned_services.append(proc)
        print(f"Ollama PID: {proc.pid}")

        deadline = time.monotonic() + 20.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(f"Warning: Ollama process exited prematurely with code {proc.returncode} — hybrid search unavailable", file=sys.stderr)
                if proc in self.owned_services:
                    self.owned_services.remove(proc)
                return False
            if is_ollama_healthy(host):
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            print(f"Warning: Ollama failed to become ready within 20s at {host} — hybrid search unavailable", file=sys.stderr)
            self._terminate_process(proc)
            if proc in self.owned_services:
                self.owned_services.remove(proc)
            return False

        print("Ollama ready.")
        return True

    def warmup(self) -> None:
        url = self.config.ollama_warmup_url
        model = self.config.ollama_embed_model
        if not url:
            print(f"Warning: Ollama warmup URL could not be determined — skipping {model} warmup", file=sys.stderr)
            return

        payload = json.dumps({
            "model": model,
            "input": "knowledge federation search warmup",
            "keep_alive": -1,
        }).encode("utf-8")
        req = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=120.0) as resp:
                if resp.status == 200:
                    print(f"Ollama model {model} preloaded (keep_alive=-1).")
                else:
                    print(
                        f"Warning: {model} warmup returned status {resp.status} — "
                        "knowledge-ui will start with lexical article search and live entries",
                        file=sys.stderr,
                    )
        except Exception as exc:
            print(
                f"Warning: {model} warmup failed ({exc}) — "
                "knowledge-ui will start with lexical article search and live entries",
                file=sys.stderr,
            )

    def run(self) -> int:
        def sig_handler(signum, frame):
            self.cleanup()
            sys.exit(128 + signum)

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        try:
            # 1. Meilisearch startup (required if enabled)
            if self.config.manage_meili:
                self.start_meili()

            # 2. Ollama startup (optional if enabled)
            ollama_ready = False
            if self.config.manage_ollama:
                ollama_ready = self.start_ollama()
            elif self.config.warmup_embedding:
                ollama_ready = is_ollama_healthy(self.config.ollama_host)

            # 3. Embedding warmup (optional if enabled)
            if self.config.warmup_embedding:
                if ollama_ready or not self.config.manage_ollama:
                    self.warmup()
                else:
                    print(f"Warning: Ollama unavailable — skipping {self.config.ollama_embed_model} warmup", file=sys.stderr)

            # 4. Start UI
            os.environ["FEDERATION_CATALOG"] = str(self.config.catalog_path)
            os.environ["UI_HOST"] = self.config.ui_host
            os.environ["UI_PORT"] = str(self.config.ui_port)
            os.environ["KNOWLEDGE_DATA_DIR"] = str(self.config.data_dir)
            if self.config.meili_url:
                os.environ["MEILI_URL"] = self.config.meili_url

            ui_cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "server:app",
                "--app-dir",
                str(UI_DIR),
                "--host",
                self.config.ui_host,
                "--port",
                str(self.config.ui_port),
            ]
            print(f"Starting Knowledge UI at http://{self.config.ui_host}:{self.config.ui_port} ...")
            self.ui_process = subprocess.Popen(ui_cmd, cwd=str(UI_DIR))
            exit_code = self.ui_process.wait()
            self.cleanup()
            return exit_code
        except Exception:
            self.cleanup()
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="launch.py",
        description="Knowledge Browser common runtime launcher",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit without starting services.",
    )
    args = parser.parse_args(argv)

    try:
        config = LauncherConfig.from_env()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check_config:
        print(f"Configuration valid. Catalog: {config.catalog_path}")
        return 0

    launcher = Launcher(config)
    try:
        return launcher.run()
    except Exception as exc:
        print(f"Launcher error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
