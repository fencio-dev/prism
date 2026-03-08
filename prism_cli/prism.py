#!/usr/bin/env python3
"""
Prism CLI — manage local Prism services.

Usage:
  prism start      Start all services
  prism stop       Stop all services
  prism status     Show service health
  prism logs       Tail service logs
  prism config     Update GOOGLE_API_KEY interactively
  prism tenant     Show current tenant ID
  prism policies   List installed policies
"""

import os
import sys
import signal
import subprocess
import socket
from pathlib import Path
from typing import Optional

import typer
import httpx
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from dotenv import load_dotenv, set_key

# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────

PRISM_HOME = Path(os.environ.get("PRISM_HOME", Path.home() / ".prism"))
ENV_FILE = PRISM_HOME / ".env"
LOG_DIR = PRISM_HOME / "data" / "logs"

GATEWAY_PORT_CANDIDATES = [47000, 47001, 47002]
GRPC_PORT_CANDIDATES    = [50051, 50052, 50053]


def _prism_port() -> int:
    return int(_load_env().get("PRISM_PORT", "47000"))


def _data_plane_port() -> int:
    return int(_load_env().get("DATA_PLANE_PORT", "50051"))


def _prism_url() -> str:
    return f"http://localhost:{_prism_port()}"


def find_free_port(candidates: list[int]) -> int:
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    raise RuntimeError(f"All preferred ports are busy: {candidates}")

app = typer.Typer(help="Prism — local LLM security policy enforcement", add_completion=False)
console = Console()


def _load_env() -> dict:
    """Load .env and return as dict."""
    env: dict = {}
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    return env


def _tenant_id() -> str:
    env = _load_env()
    return env.get("TENANT_ID", "local-dev-user")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("localhost", port)) == 0


def _http_ok(url: str, headers: Optional[dict] = None) -> bool:
    try:
        r = httpx.get(url, headers=headers or {}, timeout=3)
        return r.status_code < 500
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def start():
    """Start all Prism services in the background."""
    if not PRISM_HOME.exists():
        console.print(f"[red]Prism home not found at {PRISM_HOME}. Run the installer first.[/red]")
        raise typer.Exit(1)

    # Guard against double-start
    current_url = _prism_url()
    if _http_ok(current_url + "/health"):
        console.print(f"[yellow]Prism already running at {current_url}[/yellow]")
        return

    # Discover free ports
    prism_port = find_free_port(GATEWAY_PORT_CANDIDATES)
    grpc_port = find_free_port(GRPC_PORT_CANDIDATES)
    _write_env_key("PRISM_PORT", str(prism_port))
    _write_env_key("DATA_PLANE_PORT", str(grpc_port))

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "run-all.log"

    console.print("[bold blue]Starting Prism services...[/bold blue]")

    env = os.environ.copy()
    prism_env = _load_env()
    env.update(prism_env)
    env["PRISM_PORT"] = str(prism_port)
    env["DATA_PLANE_PORT"] = str(grpc_port)

    proc = subprocess.Popen(
        ["bash", "-c", "make run-all"],
        cwd=str(PRISM_HOME),
        env=env,
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    pid_file = LOG_DIR / "run-all.pid"
    pid_file.write_text(str(proc.pid))

    gateway_url = f"http://localhost:{prism_port}"
    console.print(f"[green]Services starting (PID {proc.pid})[/green]")
    console.print(f"[dim]Gateway: {gateway_url} | gRPC port: {grpc_port}[/dim]")
    console.print(f"[dim]Logs: {log_file}[/dim]")
    console.print("\nRun [bold]prism status[/bold] to check readiness.")


@app.command()
def stop():
    """Stop all running Prism services."""
    stopped_any = False

    # Kill by PID file
    pid_file = LOG_DIR / "run-all.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            pid_file.unlink()
            console.print(f"[green]Sent SIGTERM to process group {pid}[/green]")
            stopped_any = True
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    # Kill processes holding our known ports
    prism_port = _prism_port()
    grpc_port = _data_plane_port()
    ports = {prism_port: "Prism Gateway", grpc_port: "Data Plane (gRPC)"}
    for port, name in ports.items():
        if _port_open(port):
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split()
            for p in pids:
                try:
                    os.kill(int(p), signal.SIGTERM)
                    console.print(f"[green]Stopped {name} (PID {p})[/green]")
                    stopped_any = True
                except (ProcessLookupError, ValueError):
                    pass

    if not stopped_any:
        console.print("[yellow]No running Prism services found.[/yellow]")
    else:
        console.print("[bold green]Done.[/bold green]")


@app.command()
def status():
    """Show health status of all Prism services."""
    table = Table(title="Prism Service Status", show_header=True, header_style="bold")
    table.add_column("Service", style="bold")
    table.add_column("Port")
    table.add_column("Status")
    table.add_column("Endpoint")

    gateway_url = _prism_url()
    grpc_port = _data_plane_port()

    gateway_ok = _http_ok(gateway_url + "/health")
    table.add_row(
        "Prism Gateway",
        str(_prism_port()),
        "[green]healthy[/green]" if gateway_ok else "[red]unreachable[/red]",
        f"{gateway_url}/health",
    )

    data_ok = _port_open(grpc_port)
    table.add_row(
        "Data Plane (gRPC)",
        str(grpc_port),
        "[green]listening[/green]" if data_ok else "[red]unreachable[/red]",
        f"localhost:{grpc_port}",
    )

    console.print(table)

    if not any([gateway_ok, data_ok]):
        console.print("\n[yellow]No services are running. Use [bold]prism start[/bold] to start them.[/yellow]")


@app.command()
def logs(
    service: Optional[str] = typer.Argument(None, help="Service name: mgmt, data, mcp (default: all)"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Tail service logs."""
    log_map = {
        "mgmt": LOG_DIR / "management-plane.log",
        "data": LOG_DIR / "data-plane.log",
        "mcp":  LOG_DIR / "mcp-server.log",
    }

    if service:
        if service not in log_map:
            console.print(f"[red]Unknown service '{service}'. Choose from: mgmt, data, mcp[/red]")
            raise typer.Exit(1)
        targets = {service: log_map[service]}
    else:
        targets = log_map

    for name, path in targets.items():
        if not path.exists():
            console.print(f"[yellow]No log file for {name} at {path}[/yellow]")
            continue
        console.print(f"\n[bold blue]── {name} ({path}) ──[/bold blue]")
        result = subprocess.run(["tail", f"-n{lines}", str(path)], capture_output=True, text=True)
        console.print(result.stdout)


@app.command()
def config():
    """Interactive TUI to update GOOGLE_API_KEY."""
    console.print("[bold]Prism Configuration[/bold]\n")

    env = _load_env()
    current_key = env.get("GOOGLE_API_KEY", "")
    masked = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) > 12 else ("(not set)" if not current_key else "(set)")
    console.print(f"Current GOOGLE_API_KEY: [dim]{masked}[/dim]")
    console.print("Get a key at: https://aistudio.google.com/app/apikey\n")

    while True:
        new_key = Prompt.ask("Enter new GOOGLE_API_KEY (leave blank to keep current)")
        if not new_key:
            if not current_key:
                console.print("[yellow]No key set. Please provide a valid API key.[/yellow]")
                continue
            console.print("[dim]Keeping existing key.[/dim]")
            return

        console.print("[dim]Validating key...[/dim]")
        if _validate_google_key(new_key):
            console.print("[green]Key is valid.[/green]")
            _write_env_key("GOOGLE_API_KEY", new_key)
            # Also sync to management_plane/.env
            mgmt_env = PRISM_HOME / "management_plane" / ".env"
            if mgmt_env.exists():
                set_key(str(mgmt_env), "GOOGLE_API_KEY", new_key)
            console.print("[bold green]GOOGLE_API_KEY updated.[/bold green]")
            return
        else:
            console.print("[red]Key validation failed (could not reach Gemini API or key is invalid). Try again.[/red]")


@app.command()
def tenant():
    """Show the current tenant ID."""
    tid = _tenant_id()
    console.print(f"[bold]Tenant ID:[/bold] {tid}")


@app.command()
def policies():
    """List installed policies from the Management Plane."""
    tid = _tenant_id()
    url = f"{_prism_url()}/api/v2/policies"
    headers = {"X-Tenant-Id": tid}

    console.print(f"[dim]Fetching policies from {url} (tenant: {tid})[/dim]\n")

    try:
        r = httpx.get(url, headers=headers, timeout=10)
    except Exception as e:
        console.print(f"[red]Could not reach Management Plane: {e}[/red]")
        console.print(f"[yellow]Is Prism running? Try: prism start[/yellow]")
        raise typer.Exit(1)

    if r.status_code != 200:
        console.print(f"[red]HTTP {r.status_code}: {r.text}[/red]")
        raise typer.Exit(1)

    data = r.json()
    items = data if isinstance(data, list) else data.get("policies", data.get("items", []))

    if not items:
        console.print("[yellow]No policies installed.[/yellow]")
        return

    table = Table(title=f"Policies (tenant: {tid})", show_header=True, header_style="bold")
    # Build columns from first item's keys
    if items and isinstance(items[0], dict):
        for col in items[0].keys():
            table.add_column(str(col))
        for item in items:
            table.add_row(*[str(v) for v in item.values()])
    else:
        table.add_column("Policy")
        for item in items:
            table.add_row(str(item))

    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _validate_google_key(key: str) -> bool:
    """Return True if the Gemini API accepts the key."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    try:
        r = httpx.get(url, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def _write_env_key(key: str, value: str) -> None:
    """Write or update a key in PRISM_HOME/.env."""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ENV_FILE.exists():
        set_key(str(ENV_FILE), key, value)
    else:
        with open(ENV_FILE, "w") as f:
            f.write(f"{key}={value}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
