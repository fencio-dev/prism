#!/usr/bin/env python3
"""
Prism CLI — manage local Prism services.

Usage:
  prism start      Start all services
  prism stop       Stop all services
  prism status     Show service health
  prism logs       Tail service logs
  prism tenant     Show current tenant ID
  prism policies   List installed policies
"""

import os
import signal
import subprocess
import socket
import shutil
from pathlib import Path
from typing import Optional

import typer
import httpx
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv, set_key, dotenv_values

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
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
        return dict(dotenv_values(ENV_FILE))
    return {}


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


@app.command()
def update(
    branch: str = typer.Option("main", "--branch", help="Branch to update from"),
    restart: bool = typer.Option(True, "--restart/--no-restart", help="Restart Prism after successful update"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned steps without making changes"),
):
    """Update Prism from git, reinstall, and optionally restart."""
    if not PRISM_HOME.exists():
        console.print(f"[red]Prism home not found at {PRISM_HOME}. Run the installer first.[/red]")
        raise typer.Exit(1)

    for tool in ["git", "make", "uv", "cargo", "go"]:
        if shutil.which(tool) is None:
            console.print(f"[red]Missing required tool: {tool}[/red]")
            raise typer.Exit(1)

    git_check = subprocess.run(
        ["git", "-C", str(PRISM_HOME), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if git_check.returncode != 0 or git_check.stdout.strip() != "true":
        console.print(f"[red]{PRISM_HOME} is not a git repository.[/red]")
        raise typer.Exit(1)

    current_branch_proc = subprocess.run(
        ["git", "-C", str(PRISM_HOME), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if current_branch_proc.returncode != 0:
        console.print("[red]Detached HEAD detected. Check out your update branch and retry.[/red]")
        raise typer.Exit(1)

    current_branch = current_branch_proc.stdout.strip()
    if current_branch != branch:
        console.print(
            f"[red]Current branch is '{current_branch}', expected '{branch}'. Switch branches and retry.[/red]"
        )
        raise typer.Exit(1)

    status_proc = subprocess.run(
        ["git", "-C", str(PRISM_HOME), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if status_proc.returncode != 0:
        console.print("[red]Failed to inspect git status.[/red]")
        raise typer.Exit(1)
    if status_proc.stdout.strip():
        console.print("[red]Working tree is not clean. Commit or stash changes and retry.[/red]")
        raise typer.Exit(1)

    running_before = _http_ok(_prism_url() + "/health") or (LOG_DIR / "run-all.pid").exists()

    steps = [
        ["git", "-C", str(PRISM_HOME), "fetch", "origin", branch],
        ["git", "-C", str(PRISM_HOME), "pull", "--ff-only", "origin", branch],
        ["make", "install"],
    ]

    if dry_run:
        console.print("[bold blue]Dry run: planned update steps[/bold blue]")
        for cmd in steps:
            console.print(f"[dim]- {' '.join(cmd)}[/dim]")
        if restart and running_before:
            console.print("[dim]- prism stop[/dim]")
            console.print("[dim]- prism start[/dim]")
        elif restart:
            console.print("[dim]- restart skipped (Prism not currently running)[/dim]")
        else:
            console.print("[dim]- restart disabled (--no-restart)[/dim]")
        return

    console.print(f"[bold blue]Updating Prism from '{branch}'...[/bold blue]")

    for cmd in steps[:2]:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"[red]Update failed: {' '.join(cmd)}[/red]")
            if result.stderr.strip():
                console.print(f"[dim]{result.stderr.strip()}[/dim]")
            raise typer.Exit(1)

    console.print("[bold blue]Reinstalling Prism...[/bold blue]")
    install_result = subprocess.run(steps[2], cwd=str(PRISM_HOME), capture_output=True, text=True)
    if install_result.returncode != 0:
        console.print("[red]Install failed: make install[/red]")
        if install_result.stderr.strip():
            console.print(f"[dim]{install_result.stderr.strip()}[/dim]")
        raise typer.Exit(1)

    if restart and running_before:
        console.print("[bold blue]Restarting Prism services...[/bold blue]")
        stop()
        start()
    elif restart:
        console.print("[yellow]Prism was not running; skipping restart.[/yellow]")
    else:
        console.print("[yellow]Restart skipped (--no-restart).[/yellow]")

    console.print("[bold green]Prism update complete.[/bold green]")
    console.print(f"[dim]Run prism status to verify health. Logs: {LOG_DIR}[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

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
