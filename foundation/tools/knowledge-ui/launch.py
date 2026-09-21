"""Common runtime launcher for Knowledge Browser UI and local backends."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import ipaddress
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
CORE_DIR = Path(__file__).resolve().parents[2] / "integrations" / "federation-core"

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from federation_core.core import parse_catalog, resolve_catalog_path


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
    """Check if the given host is a loopback address using ipaddress and localhost check."""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback
    except ValueError:
        return False


def check_managed_loopback_host(hostname: str | None, name: str, original: str) -> None:
    """Ensure host for a managed native target is strictly IPv4 loopback or localhost."""
    if not hostname:
        raise ConfigurationError(f"{name} must specify a valid host ({original!r}).")
    h = hostname.strip().lower()
    if ":" in h or h.startswith("[") or h.endswith("]"):
        raise ConfigurationError(
            f"Managed native services do not support IPv6 addresses: {original!r}. "
            "Use IPv4 loopback (127.0.0.1) or localhost."
        )
    if not is_loopback_host(h):
        raise ConfigurationError(
            f"Cannot manage remote {name} target: {original!r}. "
            "Managed native services must be local (loopback)."
        )


def validate_url(
    url_str: str,
    name: str,
    *,
    require_http_only: bool = False,
    disallow_credentials: bool = True,
    disallow_path: bool = False,
    expected_path: str | None = None,
    disallow_query_fragment: bool = True,
    allow_zero_port: bool = False,
) -> ParseResult:
    """Parse and validate URL, wrapping any parse or port errors in ConfigurationError."""
    if not url_str or not url_str.strip():
        raise ConfigurationError(f"{name} cannot be empty.")
    url_str = url_str.strip()
    try:
        parsed = urlparse(url_str)
    except Exception as exc:
        raise ConfigurationError(f"Invalid URL for {name}: {url_str!r} ({exc})")

    if not parsed.scheme:
        raise ConfigurationError(f"Invalid URL for {name}: {url_str!r}. Scheme and host:port required.")

    scheme_lower = parsed.scheme.lower()
    if require_http_only:
        if scheme_lower != "http":
            raise ConfigurationError(
                f"{name} requires HTTP scheme (got {parsed.scheme!r} in {url_str!r})."
            )
    else:
        if scheme_lower not in ("http", "https"):
            raise ConfigurationError(
                f"{name} requires HTTP or HTTPS scheme (got {parsed.scheme!r} in {url_str!r})."
            )

    if not parsed.netloc:
        raise ConfigurationError(f"Invalid URL for {name}: {url_str!r}. Scheme and host:port required.")

    if disallow_credentials and (parsed.username or parsed.password):
        raise ConfigurationError(
            f"{name} cannot contain authentication credentials: {url_str!r}"
        )

    if disallow_path and parsed.path not in ("", "/"):
        raise ConfigurationError(
            f"{name} cannot contain a path component: {url_str!r}"
        )

    if expected_path is not None:
        norm_path = parsed.path.rstrip("/")
        expected_norm = expected_path.rstrip("/")
        if norm_path != expected_norm:
            raise ConfigurationError(
                f"{name} must have path {expected_path!r} (got {parsed.path!r} in {url_str!r})."
            )

    if disallow_query_fragment and (parsed.query or parsed.fragment):
        raise ConfigurationError(
            f"{name} cannot contain query or fragment: {url_str!r}"
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"Invalid port in {name}: {url_str!r} ({exc})")

    if port is not None:
        min_port = 0 if allow_zero_port else 1
        if not (min_port <= port <= 65535):
            raise ConfigurationError(f"Port in {name} out of range ({port}): {url_str!r}")

    return parsed


def resolve_and_validate_catalog(raw_catalog: str | None) -> Path:
    """Validate catalog selection using Core parse_catalog with strict checks."""
    if not raw_catalog or not raw_catalog.strip():
        raise ConfigurationError(
            "Missing catalog configuration: neither FEDERATION_CATALOG nor "
            "KNOWLEDGE_CATALOG is set. Set FEDERATION_CATALOG to the path of CATALOG.md."
        )
    try:
        catalog_path = resolve_catalog_path(raw_catalog.strip())
    except Exception as exc:
        raise ConfigurationError(f"Invalid catalog configuration: {exc}")

    if not catalog_path.exists() or not catalog_path.is_file():
        raise ConfigurationError(
            f"Configured catalog path does not exist or is not a file: {catalog_path}"
        )

    try:
        parse_catalog(catalog_path, strict=True)
    except Exception as exc:
        raise ConfigurationError(f"Catalog validation failed: {exc}")

    return catalog_path


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
    meili_probe_url: str | None
    meili_bind_addr: str | None
    meili_bin: str
    meili_db_path: Path | None
    meili_api_key: str | None
    meili_master_key: str | None
    manage_ollama: bool
    ollama_bin: str
    ollama_host: str | None
    ollama_embed_url: str | None
    ollama_models: str | None
    warmup_embedding: bool
    ollama_embed_model: str
    ollama_warmup_url: str | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LauncherConfig:
        """Pure configuration validation before any mutation, directory creation or subprocess spawn."""
        if env is None:
            env = dict(os.environ)

        # 1. Catalog validation (pure, no directory creation)
        raw_catalog = env.get("FEDERATION_CATALOG") or env.get("KNOWLEDGE_CATALOG")
        catalog_path = resolve_and_validate_catalog(raw_catalog)

        # 2. Boolean switches
        manage_meili = parse_bool("KNOWLEDGE_MANAGE_MEILI", env.get("KNOWLEDGE_MANAGE_MEILI"), False)
        manage_ollama = parse_bool("KNOWLEDGE_MANAGE_OLLAMA", env.get("KNOWLEDGE_MANAGE_OLLAMA"), False)
        warmup_embedding = parse_bool("KNOWLEDGE_WARMUP_EMBEDDING", env.get("KNOWLEDGE_WARMUP_EMBEDDING"), False)
        auto_index = parse_bool("KNOWLEDGE_AUTO_INDEX", env.get("KNOWLEDGE_AUTO_INDEX"), False)
        enable_live_marimo = parse_bool("KNOWLEDGE_ENABLE_LIVE_MARIMO", env.get("KNOWLEDGE_ENABLE_LIVE_MARIMO"), False)

        # 3. UI binding & server numeric configuration
        ui_host_raw = env.get("UI_HOST", "127.0.0.1")
        if not ui_host_raw or not ui_host_raw.strip():
            raise ConfigurationError("UI_HOST must be a non-empty string.")
        ui_host = ui_host_raw.strip()

        ui_port_str = env.get("UI_PORT", "7776").strip()
        try:
            ui_port = int(ui_port_str)
            if not (1 <= ui_port <= 65535):
                raise ValueError()
        except ValueError:
            raise ConfigurationError(f"Invalid UI_PORT: {ui_port_str!r}. Must be an integer between 1 and 65535.")

        watch_val = env.get("KNOWLEDGE_WATCH_INTERVAL")
        if watch_val is not None:
            try:
                w = float(watch_val.strip())
                if w <= 0:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ConfigurationError(f"Invalid KNOWLEDGE_WATCH_INTERVAL: {watch_val!r}. Must be a positive number.")

        marimo_start_str = env.get("KNOWLEDGE_MARIMO_PORT_START", "7780").strip()
        try:
            marimo_start = int(marimo_start_str)
            if not (1 <= marimo_start <= 65535):
                raise ValueError()
        except ValueError:
            raise ConfigurationError(f"Invalid KNOWLEDGE_MARIMO_PORT_START: {marimo_start_str!r}.")

        marimo_end_str = env.get("KNOWLEDGE_MARIMO_PORT_END", "7879").strip()
        try:
            marimo_end = int(marimo_end_str)
            if not (1 <= marimo_end <= 65535):
                raise ValueError()
        except ValueError:
            raise ConfigurationError(f"Invalid KNOWLEDGE_MARIMO_PORT_END: {marimo_end_str!r}.")

        if marimo_start > marimo_end:
            raise ConfigurationError(
                f"KNOWLEDGE_MARIMO_PORT_START ({marimo_start}) must be <= KNOWLEDGE_MARIMO_PORT_END ({marimo_end})."
            )

        marimo_idle_str = env.get("KNOWLEDGE_MARIMO_IDLE_SECONDS", "3600").strip()
        try:
            marimo_idle = int(marimo_idle_str)
            if marimo_idle <= 0:
                raise ValueError()
        except ValueError:
            raise ConfigurationError(f"Invalid KNOWLEDGE_MARIMO_IDLE_SECONDS: {marimo_idle_str!r}. Must be a positive integer.")

        # Data directory path resolution without mkdir
        data_dir_raw = env.get("KNOWLEDGE_DATA_DIR")
        data_dir = Path(data_dir_raw).expanduser().resolve() if data_dir_raw else (REPO_DIR / ".data").resolve()

        # 4. Live marimo dependency check
        marimo_host = env.get("KNOWLEDGE_MARIMO_HOST") or ui_host
        if enable_live_marimo:
            if importlib.util.find_spec("marimo") is None:
                raise ConfigurationError(
                    "Live notebook execution is enabled (KNOWLEDGE_ENABLE_LIVE_MARIMO=1), "
                    "but optional dependency 'marimo' is not installed. "
                    "Install with 'uv sync --locked --extra notebook'."
                )

        # 5. Meilisearch configuration & loopback validation
        meili_url_raw = env.get("MEILI_URL")
        meili_url = meili_url_raw.strip() if meili_url_raw else None
        meili_probe_url = None
        meili_bind_addr = None

        if meili_url:
            if manage_meili:
                parsed_meili = validate_url(
                    meili_url,
                    "MEILI_URL",
                    require_http_only=True,
                    disallow_credentials=True,
                    disallow_path=True,
                    disallow_query_fragment=True,
                )
                check_managed_loopback_host(parsed_meili.hostname, "MEILI_URL", meili_url)
                meili_port = parsed_meili.port if parsed_meili.port is not None else 80
                meili_bind_addr = f"{parsed_meili.hostname}:{meili_port}"
                meili_probe_url = f"http://{meili_bind_addr}"
            else:
                validate_url(
                    meili_url,
                    "MEILI_URL",
                    allow_zero_port=True,
                )
                meili_probe_url = meili_url
        else:
            if manage_meili:
                raise ConfigurationError("KNOWLEDGE_MANAGE_MEILI requires MEILI_URL to be explicitly configured.")
            if auto_index:
                raise ConfigurationError("KNOWLEDGE_AUTO_INDEX requires MEILI_URL to be set.")

        meili_bin = env.get("MEILI_BIN", "meilisearch").strip()
        meili_db_raw = env.get("MEILI_DB_PATH")
        meili_db_path = Path(meili_db_raw).expanduser().resolve() if meili_db_raw else (data_dir / "meili_data")
        meili_api_key = env.get("MEILI_API_KEY")
        meili_master_key = env.get("MEILI_MASTER_KEY")

        # 6. Ollama configuration & loopback validation
        ollama_bin = env.get("OLLAMA_BIN", "ollama").strip()
        ollama_host_raw = env.get("OLLAMA_HOST", "").strip() or None
        ollama_embed_url = env.get("OLLAMA_EMBED_URL", "").strip() or None
        ollama_models = env.get("OLLAMA_MODELS", "").strip() or None
        ollama_host = None

        if manage_ollama:
            if not ollama_host_raw and not ollama_embed_url:
                raise ConfigurationError(
                    "KNOWLEDGE_MANAGE_OLLAMA requires OLLAMA_HOST or OLLAMA_EMBED_URL to be configured."
                )

            if ollama_host_raw:
                if "://" in ollama_host_raw:
                    parsed_host = validate_url(
                        ollama_host_raw,
                        "OLLAMA_HOST",
                        require_http_only=True,
                        disallow_credentials=True,
                        disallow_path=True,
                        disallow_query_fragment=True,
                    )
                    check_managed_loopback_host(parsed_host.hostname, "Ollama", ollama_host_raw)
                    host_port = parsed_host.port if parsed_host.port is not None else 11434
                    ollama_host = f"{parsed_host.hostname}:{host_port}"
                else:
                    if ollama_host_raw.startswith("[") or ollama_host_raw.count(":") > 1:
                        raise ConfigurationError(
                            f"Managed native services do not support IPv6 addresses: {ollama_host_raw!r}. "
                            "Use IPv4 loopback (127.0.0.1) or localhost."
                        )
                    if ":" in ollama_host_raw:
                        host_name, port_part = ollama_host_raw.split(":", 1)
                        try:
                            host_port = int(port_part)
                            if not (1 <= host_port <= 65535):
                                raise ValueError()
                        except ValueError:
                            raise ConfigurationError(f"Invalid port in OLLAMA_HOST: {ollama_host_raw!r}")
                    else:
                        host_name = ollama_host_raw
                        host_port = 11434
                    check_managed_loopback_host(host_name, "Ollama", ollama_host_raw)
                    ollama_host = f"{host_name}:{host_port}"
            else:
                assert ollama_embed_url is not None
                parsed_embed = validate_url(
                    ollama_embed_url,
                    "OLLAMA_EMBED_URL",
                    require_http_only=True,
                    disallow_credentials=True,
                    expected_path="/api/embed",
                    disallow_query_fragment=True,
                )
                check_managed_loopback_host(parsed_embed.hostname, "OLLAMA_EMBED_URL", ollama_embed_url)
                embed_port = parsed_embed.port if parsed_embed.port is not None else 80
                ollama_host = f"{parsed_embed.hostname}:{embed_port}"

            # Check for contradiction between managed Ollama host and embed URL
            if ollama_embed_url:
                parsed_embed = validate_url(
                    ollama_embed_url,
                    "OLLAMA_EMBED_URL",
                    require_http_only=True,
                    disallow_credentials=True,
                    expected_path="/api/embed",
                    disallow_query_fragment=True,
                )
                check_managed_loopback_host(parsed_embed.hostname, "OLLAMA_EMBED_URL", ollama_embed_url)
                embed_port = parsed_embed.port if parsed_embed.port is not None else 80
                host_name, host_port_str = ollama_host.split(":", 1)
                if parsed_embed.hostname != host_name or embed_port != int(host_port_str):
                    raise ConfigurationError(
                        f"Contradictory Ollama configuration: OLLAMA_EMBED_URL endpoint "
                        f"({parsed_embed.hostname}:{embed_port}) does not match managed OLLAMA_HOST ({ollama_host})."
                    )
        else:
            # Preserve externally managed remote embedding URL
            ollama_host = ollama_host_raw
            if ollama_embed_url:
                validate_url(ollama_embed_url, "OLLAMA_EMBED_URL", allow_zero_port=True)

        # 7. Warmup endpoint & model validation
        ollama_embed_model = env.get("OLLAMA_EMBED_MODEL", "bge-m3").strip()
        ollama_warmup_url = env.get("OLLAMA_WARMUP_URL", "").strip() or None

        if warmup_embedding:
            if not ollama_embed_model:
                raise ConfigurationError("OLLAMA_EMBED_MODEL cannot be empty when KNOWLEDGE_WARMUP_EMBEDDING is enabled.")

            if not ollama_warmup_url:
                if ollama_embed_url:
                    ollama_warmup_url = ollama_embed_url
                elif ollama_host:
                    ollama_warmup_url = f"http://{ollama_host}/api/embed"
                else:
                    raise ConfigurationError(
                        "KNOWLEDGE_WARMUP_EMBEDDING requires an explicit endpoint: "
                        "set OLLAMA_WARMUP_URL, OLLAMA_EMBED_URL, or OLLAMA_HOST."
                    )

            if manage_ollama:
                parsed_warmup = validate_url(
                    ollama_warmup_url,
                    "OLLAMA_WARMUP_URL",
                    require_http_only=True,
                    disallow_credentials=True,
                    expected_path="/api/embed",
                    disallow_query_fragment=True,
                )
                check_managed_loopback_host(parsed_warmup.hostname, "OLLAMA_WARMUP_URL", ollama_warmup_url)
                warmup_port = parsed_warmup.port if parsed_warmup.port is not None else 80
                host_name, host_port_str = ollama_host.split(":", 1)
                if parsed_warmup.hostname != host_name or warmup_port != int(host_port_str):
                    raise ConfigurationError(
                        f"Contradictory Ollama configuration: OLLAMA_WARMUP_URL endpoint "
                        f"({parsed_warmup.hostname}:{warmup_port}) does not match managed OLLAMA_HOST ({ollama_host})."
                    )
            else:
                validate_url(
                    ollama_warmup_url,
                    "OLLAMA_WARMUP_URL",
                    expected_path="/api/embed",
                    allow_zero_port=True,
                )

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
            meili_probe_url=meili_probe_url,
            meili_bind_addr=meili_bind_addr,
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


def is_process_group_active(pgid: int) -> bool:
    """Check if at least one non-zombie process belongs to the process group pgid."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True

    # On Linux, inspect /proc to ignore zombie processes
    if os.path.exists("/proc"):
        try:
            with os.scandir("/proc") as it:
                for entry in it:
                    if entry.name.isdigit():
                        try:
                            with open(f"/proc/{entry.name}/stat", "r") as f:
                                content = f.read()
                            _, rest = content.rsplit(") ", 1)
                            fields = rest.split()
                            state = fields[0]
                            pgrp = int(fields[2])
                            if pgrp == pgid and state != "Z":
                                return True
                        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
                            continue
            return False
        except Exception:
            return True
    return True


def terminate_process_group(
    proc: subprocess.Popen | None = None,
    pgid: int | None = None,
    grace_period: float = 1.0,
) -> None:
    """Terminate an owned process group with a bounded grace period, then SIGKILL and reap leader."""
    if pgid is None and proc is not None:
        pgid = getattr(proc, "pid", None)
    if pgid is None:
        return

    # 1. Send SIGTERM to the process group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass

    # 2. Bounded grace period waiting for group processes to exit
    deadline = time.monotonic() + grace_period
    while time.monotonic() < deadline:
        if not is_process_group_active(pgid):
            break
        time.sleep(0.05)

    # 3. If group still has active members (e.g. stubborn grandchild), SIGKILL the group
    if is_process_group_active(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    # 4. Reap the leader process if known
    if proc is not None:
        try:
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except (ProcessLookupError, OSError):
                pass


class Launcher:
    """Orchestrates readiness checks, managed backends, warmup, and UI lifecycle."""

    def __init__(self, config: LauncherConfig):
        self.config = config
        self.owned_services: list[subprocess.Popen] = []
        self.ui_process: subprocess.Popen | None = None
        self._cleaned_up = False

    def _terminate_process_group(self, proc: subprocess.Popen, timeout: float = 2.0) -> None:
        """Terminate and reap the process group, even if leader has already exited."""
        terminate_process_group(proc, grace_period=min(timeout, 2.0))

    def cleanup(self) -> None:
        """Terminate UI and owned process groups. External reused services are never touched."""
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self.ui_process is not None:
            self._terminate_process_group(self.ui_process)
            self.ui_process = None

        for proc in reversed(self.owned_services):
            self._terminate_process_group(proc)
        self.owned_services.clear()

    def start_meili(self) -> None:
        url = self.config.meili_probe_url
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

        addr = self.config.meili_bind_addr
        cmd = [
            bin_path,
            "--db-path", str(self.config.meili_db_path),
            "--http-addr", addr,
            "--no-analytics",
            "--experimental-allowed-ip-networks", "127.0.0.1/32",
        ]

        meili_env = os.environ.copy()
        if self.config.meili_master_key:
            meili_env["MEILI_MASTER_KEY"] = self.config.meili_master_key

        print(f"Starting Meilisearch at {addr}...")
        log_file = log_path.open("ab")
        try:
            proc = subprocess.Popen(
                cmd,
                env=meili_env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.owned_services.append(proc)
        finally:
            log_file.close()

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
            if proc in self.owned_services:
                self._terminate_process_group(proc)
                self.owned_services.remove(proc)
            raise RuntimeError(
                f"Meilisearch failed to become ready within 20s at {url}. "
                f"Check log at {log_path}"
            )
        print("Meilisearch ready.")

    def start_ollama(self) -> bool:
        host = self.config.ollama_host
        assert host is not None
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

        env = os.environ.copy()
        env["OLLAMA_HOST"] = host
        if self.config.ollama_models:
            env["OLLAMA_MODELS"] = self.config.ollama_models

        print(f"Starting Ollama at {host}...")
        log_file = log_path.open("ab")
        try:
            proc = subprocess.Popen(
                [bin_path, "serve"],
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.owned_services.append(proc)
        except OSError as exc:
            print(f"Warning: Failed to start Ollama ({exc}) — hybrid search unavailable", file=sys.stderr)
            return False
        finally:
            log_file.close()

        print(f"Ollama PID: {proc.pid}")

        deadline = time.monotonic() + 20.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(
                    f"Warning: Ollama process exited prematurely with code {proc.returncode} — hybrid search unavailable",
                    file=sys.stderr,
                )
                if proc in self.owned_services:
                    self._terminate_process_group(proc)
                    self.owned_services.remove(proc)
                return False
            if is_ollama_healthy(host):
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            print(f"Warning: Ollama failed to become ready within 20s at {host} — hybrid search unavailable", file=sys.stderr)
            if proc in self.owned_services:
                self._terminate_process_group(proc)
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
            # 1. Directory creation happens here at actual runtime launch
            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            if self.config.manage_meili and self.config.meili_db_path:
                self.config.meili_db_path.mkdir(parents=True, exist_ok=True)

            # 2. Meilisearch startup (required if enabled)
            if self.config.manage_meili:
                self.start_meili()

            # 3. Ollama startup (optional if enabled)
            ollama_ready = False
            if self.config.manage_ollama:
                ollama_ready = self.start_ollama()

            # 4. Embedding warmup (optional if enabled)
            if self.config.warmup_embedding:
                if self.config.manage_ollama:
                    if ollama_ready:
                        self.warmup()
                    else:
                        print(f"Warning: Ollama unavailable — skipping {self.config.ollama_embed_model} warmup", file=sys.stderr)
                else:
                    # External backend: send warmup request directly without probing tags
                    self.warmup()

            # 5. Build isolated child environment for UI without mutating parent process
            ui_env = os.environ.copy()
            ui_env["FEDERATION_CATALOG"] = str(self.config.catalog_path)
            ui_env["UI_HOST"] = self.config.ui_host
            ui_env["UI_PORT"] = str(self.config.ui_port)
            ui_env["KNOWLEDGE_DATA_DIR"] = str(self.config.data_dir)
            ui_env["KNOWLEDGE_AUTO_INDEX"] = "1" if self.config.auto_index else "0"
            ui_env["KNOWLEDGE_ENABLE_LIVE_MARIMO"] = "1" if self.config.enable_live_marimo else "0"
            ui_env["KNOWLEDGE_MARIMO_HOST"] = self.config.marimo_host
            if self.config.meili_url:
                ui_env["MEILI_URL"] = self.config.meili_url
            if self.config.meili_api_key:
                ui_env["MEILI_API_KEY"] = self.config.meili_api_key

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
            self.ui_process = subprocess.Popen(
                ui_cmd,
                cwd=str(UI_DIR),
                env=ui_env,
                start_new_session=True,
            )
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
