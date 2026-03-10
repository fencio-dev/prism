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
import sys
import hashlib
import webbrowser
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


def _proxy_port() -> int:
    return 47100


def _proxy_api_port() -> int:
    return 47101


def _proxy_api_url() -> str:
    return f"http://localhost:{_proxy_api_port()}"


def _prism_url() -> str:
    return f"http://localhost:{_prism_port()}"


def find_free_port(candidates: list[int]) -> int:
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    raise RuntimeError(f"All preferred ports are busy: {candidates}")

app = typer.Typer(help="Prism — local LLM security policy enforcement", add_completion=False)
agents_app = typer.Typer(help="Manage agents registered with the Fencio Proxy.")
cert_app = typer.Typer(help="Manage the Fencio Proxy CA certificate.")
app.add_typer(agents_app, name="agents")
app.add_typer(cert_app, name="cert")
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
    console.print(f"[dim]Proxy UI: http://localhost:47102[/dim]")
    console.print(f"[dim]Logs: {log_file}[/dim]")
    console.print("\nRun [bold]prism status[/bold] to check readiness.")
    console.print("[dim]Next: [bold]prism status[/bold] → [bold]prism proxy[/bold] to set up network enforcement[/dim]")


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
        "Data Plane",
        str(grpc_port),
        "[green]listening[/green]" if data_ok else "[red]unreachable[/red]",
        "internal",
    )

    proxy_port = _proxy_port()
    proxy_ok = _port_open(proxy_port)
    table.add_row(
        "Fencio Proxy",
        str(proxy_port),
        "[green]listening[/green]" if proxy_ok else "[red]unreachable[/red]",
        f"localhost:{proxy_port}",
    )

    proxy_api_port = _proxy_api_port()
    proxy_api_ok = _port_open(proxy_api_port)
    table.add_row(
        "Proxy API",
        str(proxy_api_port),
        "[green]listening[/green]" if proxy_api_ok else "[red]unreachable[/red]",
        f"http://localhost:{proxy_api_port}",
    )

    console.print(table)

    if not any([gateway_ok, data_ok, proxy_ok, proxy_api_ok]):
        console.print("\n[yellow]No services are running. Use [bold]prism start[/bold] to start them.[/yellow]")


@app.command()
def proxy():
    """Show how to route agent traffic through the Fencio Proxy."""
    from rich.panel import Panel
    from rich.text import Text

    proxy_port = _proxy_port()
    proxy_api_port = _proxy_api_port()

    console.print(Panel.fit(
        f"[bold]Forward proxy:[/bold]  localhost:{proxy_port}\n"
        f"[bold]Proxy API:[/bold]      http://localhost:{proxy_api_port}",
        title="Fencio Proxy — Network-Level Enforcement",
        border_style="blue",
    ))

    console.print("\n[bold]Step 1 — Route your agent's traffic through the proxy[/bold]")
    console.print("\nSet these environment variables before running your agent:\n")
    console.print(f"  [green]export HTTP_PROXY=http://localhost:{proxy_port}[/green]")
    console.print(f"  [green]export HTTPS_PROXY=http://localhost:{proxy_port}[/green]")

    console.print("\n[bold]Step 2 — Register your agent[/bold]")
    console.print("\n  [green]prism agents create \"my-agent\"[/green]")
    console.print("\n  Copy the [bold]agent_id[/bold] and [bold]api_key[/bold] from the output. Both headers are required on every request:")
    console.print("\n  [green]X-Fencio-Agent-ID: <agent_id>[/green]   — identifies the agent")
    console.print("  [green]X-Fencio-API-Key: <api_key>[/green]    — authenticates the agent to the proxy")
    console.print("\n[dim]Requests missing either header are dropped with 403.[/dim]")

    console.print("\n[bold]Step 3 — Watch enforcement decisions in real time[/bold]")
    console.print(f"\n  [cyan]prism logs proxy[/cyan]   — tail proxy enforcement logs")
    console.print(f"  [cyan]prism status[/cyan]       — check proxy health")

    console.print(Panel.fit(
        "[bold]Using LangChain or LangGraph?[/bold]\n\n"
        "A cleaner integration is available:\n\n"
        "  [green]pip install langchain-prism[/green]\n\n"
        "  [cyan]https://fencio.dev/docs/integrations/langchain[/cyan]\n\n"
        "This applies enforcement at the LLM callback level\n"
        "without configuring a network proxy.",
        border_style="dim",
    ))


@app.command()
def ui(
    open: bool = typer.Option(False, "--open", "-o", help="Open Prism Gateway in the browser"),
):
    """Show Fencio service URLs and optionally open the Prism Gateway."""
    from rich.panel import Panel

    prism_url = _prism_url()
    console.print(Panel.fit(
        f"[bold]Prism Gateway:[/bold]  {prism_url}\n"
        f"[bold]Proxy UI:[/bold]       http://localhost:47102",
        title="Fencio Service URLs",
        border_style="blue",
    ))

    if open:
        webbrowser.open(prism_url)


@app.command()
def logs(
    service: Optional[str] = typer.Argument(None, help="Service name: mgmt, management, data, data-plane, mcp, proxy (default: all)"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Tail service logs."""
    log_map = {
        "mgmt":        LOG_DIR / "management-plane.log",
        "management":  LOG_DIR / "management-plane.log",
        "data":        LOG_DIR / "data-plane.log",
        "data-plane":  LOG_DIR / "data-plane.log",
        "mcp":         LOG_DIR / "mcp-server.log",
        "proxy":       LOG_DIR / "proxy.log",
    }

    if service:
        if service not in log_map:
            console.print(f"[red]Unknown service '{service}'. Choose from: mgmt, management, data, data-plane, mcp, proxy[/red]")
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
def policies(
    agent: Optional[str] = typer.Option(None, "--agent", help="Filter by agent ID"),
):
    """Manage policies interactively. Run without flags for TUI, or use --agent to pre-filter."""
    import json as _json
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable, Header, Footer, Input, Label, Static
    from textual.containers import Vertical, Horizontal
    from textual.screen import ModalScreen
    from textual import events

    initial_agent_filter = agent

    class FilterScreen(ModalScreen):
        DEFAULT_CSS = """
        #dialog {
            padding: 1 2;
            width: 60;
            height: auto;
            border: thick $background 80%;
            background: $surface;
        }
        """

        def __init__(self, current: str) -> None:
            super().__init__()
            self._current = current

        def compose(self) -> ComposeResult:
            with Vertical(id="dialog"):
                yield Label("Filter by Agent ID (blank = all policies)")
                yield Input(placeholder="Agent ID", id="agent-input", value=self._current)

        def on_key(self, event: events.Key) -> None:
            if event.key == "enter":
                value = self.query_one("#agent-input", Input).value.strip()
                self.dismiss(value)
            elif event.key == "escape":
                self.dismiss(self._current)

    class PoliciesApp(App):
        TITLE = "Fencio — Policy Manager"

        DEFAULT_CSS = """
        DataTable {
            height: 1fr;
        }
        #detail-panel {
            height: 14;
            border: solid $panel;
            padding: 0 1;
            overflow-y: auto;
        }
        Footer {
            background: $panel;
        }
        """

        BINDINGS = [
            ("r", "refresh", "Refresh"),
            ("f", "filter", "Filter by agent"),
            ("q", "quit", "Quit"),
        ]

        def __init__(self, agent_filter: str) -> None:
            super().__init__()
            self._agent_filter = agent_filter
            self._policies: list[dict] = []

        def compose(self) -> ComposeResult:
            yield Header()
            yield DataTable(id="policies-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="detail-panel")
            yield Footer()

        def on_mount(self) -> None:
            tid = _tenant_id()
            self.sub_title = f"tenant: {tid}"
            self.load_policies()

        def _fetch_policies(self) -> list[dict]:
            tid = _tenant_id()
            url = f"{_prism_url()}/api/v2/policies"
            params = {}
            if self._agent_filter:
                params["agent_id"] = self._agent_filter
            try:
                r = httpx.get(url, headers={"X-Tenant-Id": tid}, params=params, timeout=10)
                data = r.json()
                return data if isinstance(data, list) else data.get("policies", data.get("items", []))
            except Exception:
                return []

        def load_policies(self) -> None:
            table = self.query_one("#policies-table", DataTable)
            table.clear(columns=True)
            table.add_columns("Name", "Status", "Agent ID", "Policy Type", "Priority", "Created")
            self._policies = self._fetch_policies()
            if not self._policies:
                self.query_one("#detail-panel", Static).update(
                    "[yellow]No policies found.[/yellow]" +
                    (f" (agent filter: {self._agent_filter})" if self._agent_filter else "")
                )
            for p in self._policies:
                created = str(p.get("created_at", "")).split("T")[0]
                table.add_row(
                    str(p.get("name", "")),
                    str(p.get("status", "")),
                    str(p.get("agent_id") or "—"),
                    str(p.get("policy_type", "")),
                    str(p.get("priority", "")),
                    created,
                )
            filter_label = f"  [dim]agent filter: {self._agent_filter}[/dim]" if self._agent_filter else ""
            self.sub_title = f"tenant: {_tenant_id()}{filter_label}  ({len(self._policies)} policies)"

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
            if not self._policies:
                return
            row_index = event.cursor_row
            if row_index >= len(self._policies):
                return
            p = self._policies[row_index]
            detail_fields = [
                ("Name", p.get("name", "")),
                ("Status", p.get("status", "")),
                ("Agent ID", p.get("agent_id") or "—"),
                ("Policy Type", p.get("policy_type", "")),
                ("Priority", p.get("priority", "")),
                ("Created", str(p.get("created_at", "")).split("T")[0]),
            ]
            # Append any complex fields (JSON blobs) at the bottom
            skip = {"name", "status", "agent_id", "policy_type", "priority", "created_at", "updated_at"}
            for k, v in p.items():
                if k not in skip:
                    if isinstance(v, (dict, list)):
                        detail_fields.append((k, _json.dumps(v, indent=2)))
                    else:
                        detail_fields.append((k, str(v) if v is not None else "—"))

            lines = []
            for label, value in detail_fields:
                if "\n" in str(value):
                    lines.append(f"[bold]{label}:[/bold]")
                    for line in str(value).splitlines():
                        lines.append(f"  {line}")
                else:
                    lines.append(f"[bold]{label}:[/bold]  {value}")
            self.query_one("#detail-panel", Static).update("\n".join(lines))

        def action_refresh(self) -> None:
            self.load_policies()
            self.notify("Policies refreshed")

        def action_filter(self) -> None:
            def on_dismiss(result: str) -> None:
                self._agent_filter = result
                self.load_policies()

            self.push_screen(FilterScreen(self._agent_filter or ""), on_dismiss)

    PoliciesApp(agent_filter=initial_agent_filter or "").run()


@agents_app.callback(invoke_without_command=True)
def agents(ctx: typer.Context):
    """Manage agents registered with the Fencio Proxy. Run without subcommand for interactive TUI."""
    if ctx.invoked_subcommand is None:
        from textual.app import App, ComposeResult
        from textual.widgets import DataTable, Header, Footer, Input, Label, Button, Static
        from textual.containers import Vertical, Horizontal
        from textual.screen import ModalScreen
        from textual import events

        class NewAgentScreen(ModalScreen):
            DEFAULT_CSS = """
            #dialog {
                padding: 1 2;
                width: 60;
                height: auto;
                border: thick $background 80%;
                background: $surface;
            }
            """

            def compose(self) -> ComposeResult:
                with Vertical(id="dialog"):
                    yield Label("Register New Agent")
                    yield Input(placeholder="Agent name", id="name-input")
                    yield Input(placeholder="Description (optional)", id="desc-input")
                    with Horizontal():
                        yield Button("Create", variant="primary", id="create-btn")
                        yield Button("Cancel", id="cancel-btn")

            def on_button_pressed(self, event: Button.Pressed) -> None:
                if event.button.id == "create-btn":
                    name = self.query_one("#name-input", Input).value.strip()
                    if not name:
                        self.query_one("#name-input", Input).focus()
                        return
                    desc = self.query_one("#desc-input", Input).value.strip()
                    try:
                        r = httpx.post(
                            f"{_proxy_api_url()}/api/admin/agents",
                            json={"agent_name": name, "description": desc},
                            timeout=5,
                        )
                        if r.status_code == 201:
                            data = r.json()
                            self.app.notify(
                                f"API key: {data['api_key']}",
                                timeout=15,
                            )
                        else:
                            self.app.notify(f"Error {r.status_code}: {r.text}")
                    except Exception as e:
                        self.app.notify(f"Cannot reach Proxy API: {e}")
                    self.dismiss(True)
                elif event.button.id == "cancel-btn":
                    self.dismiss(False)

            def on_key(self, event: events.Key) -> None:
                if event.key == "escape":
                    self.dismiss(False)

        class AgentsApp(App):
            TITLE = "Fencio Proxy — Agent Manager"

            DEFAULT_CSS = """
            DataTable {
                height: 1fr;
            }
            #policies-panel {
                height: 8;
                border: solid $panel;
                padding: 0 1;
                color: $text-muted;
            }
            Footer {
                background: $panel;
            }
            """

            BINDINGS = [
                ("n", "new_agent", "New agent"),
                ("d", "delete_agent", "Delete"),
                ("space", "toggle_agent", "Enable/Disable"),
                ("q", "quit", "Quit"),
            ]

            def compose(self) -> ComposeResult:
                yield Header()
                yield DataTable(id="agents-table", cursor_type="row", zebra_stripes=True)
                yield Static("", id="policies-panel")
                yield Footer()

            def on_mount(self) -> None:
                self.sub_title = f"Proxy API: {_proxy_api_url()}"
                self.load_agents()

            def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
                if not self._agents:
                    return
                row_index = event.cursor_row
                if row_index >= len(self._agents):
                    return
                agent = self._agents[row_index]
                agent_id = agent.get("agent_id", "")
                panel = self.query_one("#policies-panel", Static)
                tid = _tenant_id()
                try:
                    r = httpx.get(
                        f"{_prism_url()}/api/v2/policies",
                        params={"agent_id": agent_id},
                        headers={"X-Tenant-Id": tid},
                        timeout=5,
                    )
                    data = r.json()
                    items = data if isinstance(data, list) else data.get("policies", data.get("items", []))
                except Exception:
                    items = []
                if not items:
                    panel.update("No per-agent policies — tenant-wide policies apply.")
                else:
                    lines = [f"Policies for agent {agent_id}:"]
                    for p in items:
                        name = p.get("name", "")
                        status = p.get("status", "")
                        aid = p.get("agent_id") or "—"
                        lines.append(f"  • {name}  [{status}]  agent: {aid}")
                    panel.update("\n".join(lines))

            def load_agents(self) -> None:
                table = self.query_one("#agents-table", DataTable)
                table.clear(columns=True)
                table.add_columns("Agent ID", "Name", "Status", "Description", "Created")
                try:
                    r = httpx.get(f"{_proxy_api_url()}/api/admin/agents", timeout=5)
                    data = r.json()
                    self._agents: list[dict] = data.get("agents", [])
                except Exception:
                    self._agents = []
                    self.notify("Cannot reach Proxy API")
                    return
                for agent in self._agents:
                    status_text = "enabled" if agent.get("enabled") else "disabled"
                    created = agent.get("created_at", "").split("T")[0]
                    table.add_row(
                        agent.get("agent_id", ""),
                        agent.get("agent_name", ""),
                        status_text,
                        agent.get("description", ""),
                        created,
                    )

            def action_new_agent(self) -> None:
                def on_dismiss(result) -> None:
                    self.load_agents()

                self.push_screen(NewAgentScreen(), on_dismiss)

            def action_delete_agent(self) -> None:
                if not self._agents:
                    return
                table = self.query_one("#agents-table", DataTable)
                row_index = table.cursor_row
                agent = self._agents[row_index]
                agent_id = agent.get("agent_id", "")
                try:
                    r = httpx.delete(f"{_proxy_api_url()}/api/admin/agents/{agent_id}", timeout=5)
                    if r.status_code < 300:
                        self.notify("Agent deleted")
                    else:
                        self.notify(f"Error {r.status_code}: {r.text}")
                except Exception as e:
                    self.notify(str(e))
                self.load_agents()

            def action_toggle_agent(self) -> None:
                if not self._agents:
                    return
                table = self.query_one("#agents-table", DataTable)
                row_index = table.cursor_row
                agent = self._agents[row_index]
                agent_id = agent.get("agent_id", "")
                enabled = agent.get("enabled", False)
                action = "disable" if enabled else "enable"
                try:
                    r = httpx.post(
                        f"{_proxy_api_url()}/api/admin/agents/{agent_id}/{action}",
                        timeout=5,
                    )
                    if r.status_code < 300:
                        self.notify(f"Agent {action}d")
                    else:
                        self.notify(f"Error {r.status_code}: {r.text}")
                except Exception as e:
                    self.notify(str(e))
                self.load_agents()

        AgentsApp().run()


@agents_app.command("list")
def agents_list():
    """List all agents registered with the Fencio Proxy."""
    url = f"{_proxy_api_url()}/api/admin/agents"

    try:
        r = httpx.get(url, timeout=10)
    except Exception as e:
        console.print(f"[red]Could not reach Proxy API: {e}[/red]")
        console.print(f"[yellow]Is Prism running? Try: prism start[/yellow]")
        raise typer.Exit(1)

    if r.status_code != 200:
        console.print(f"[red]HTTP {r.status_code}: {r.text}[/red]")
        raise typer.Exit(1)

    data = r.json()
    items = data.get("agents", [])

    if not items:
        console.print("[yellow]No agents registered.[/yellow]")
        return

    table = Table(title="Registered Agents", show_header=True, header_style="bold")
    table.add_column("Agent ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Description")
    table.add_column("Created")

    for agent in items:
        status = "[green]enabled[/green]" if agent.get("enabled") else "[red]disabled[/red]"
        created = agent.get("created_at", "").split("T")[0]
        table.add_row(
            agent.get("agent_id", ""),
            agent.get("agent_name", ""),
            status,
            agent.get("description", ""),
            created,
        )

    console.print(table)


@agents_app.command("create")
def agents_create(
    name: str = typer.Argument(..., help="Agent name"),
    description: str = typer.Option("", "--description", "-d", help="Optional description"),
):
    """Register a new agent with the Fencio Proxy."""
    from rich.panel import Panel

    url = f"{_proxy_api_url()}/api/admin/agents"
    payload = {"agent_name": name, "description": description}

    try:
        r = httpx.post(url, json=payload, timeout=10)
    except Exception as e:
        console.print(f"[red]Could not reach Proxy API: {e}[/red]")
        console.print(f"[yellow]Is Prism running? Try: prism start[/yellow]")
        raise typer.Exit(1)

    if r.status_code != 201:
        console.print(f"[red]HTTP {r.status_code}: {r.text}[/red]")
        raise typer.Exit(1)

    data = r.json()
    tid = _tenant_id()
    content = (
        f"[bold]Agent ID:[/bold]   {data.get('agent_id', '')}\n"
        f"[bold]Agent Name:[/bold] {data.get('agent_name', '')}\n"
        f"[bold]API Key:[/bold]    {data.get('api_key', '')}\n"
        f"[bold]Tenant ID:[/bold]  {tid}"
    )
    console.print(Panel.fit(content, title="Agent Registered", border_style="green"))
    console.print("[dim]The API key is shown once. Store it securely.[/dim]")
    console.print(
        "[dim]Agent ID + API Key → set as [bold]X-Fencio-Agent-ID[/bold] / [bold]X-Fencio-API-Key[/bold] headers on requests through the proxy.[/dim]\n"
        "[dim]Tenant ID → use this when creating policies in the Prism UI.[/dim]"
    )


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
        console.print("[dim]→ Stashing local changes...[/dim]")
        subprocess.run(
            ["git", "-C", str(PRISM_HOME), "stash", "--include-untracked"],
            capture_output=True,
            text=True,
        )
        stashed = True
    else:
        stashed = False

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

    if stashed:
        console.print("[dim]→ Restoring stashed changes...[/dim]")
        pop_result = subprocess.run(
            ["git", "-C", str(PRISM_HOME), "stash", "pop"],
            capture_output=True,
            text=True,
        )
        if pop_result.returncode != 0:
            console.print(
                "[yellow]Could not restore stashed changes automatically. "
                "Run: git -C ~/.prism stash pop[/yellow]"
            )

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
# cert commands
# ──────────────────────────────────────────────────────────────────────────────

CERT_PATH = PRISM_HOME / "data" / "certs" / "fencio-root-ca.pem"

_MANUAL_INSTRUCTIONS = (
    "\n[bold]Manual installation:[/bold]\n"
    "  [bold]macOS:[/bold]\n"
    "    sudo security add-trusted-cert -d -r trustRoot \\\n"
    f"      -k /Library/Keychains/System.keychain {CERT_PATH}\n\n"
    "  [bold]Debian/Ubuntu:[/bold]\n"
    f"    sudo cp {CERT_PATH} /usr/local/share/ca-certificates/fencio-root-ca.crt\n"
    "    sudo update-ca-certificates\n\n"
    "  [bold]RHEL/Fedora:[/bold]\n"
    f"    sudo cp {CERT_PATH} /etc/pki/ca-trust/source/anchors/fencio-root-ca.pem\n"
    "    sudo update-ca-trust"
)


def _cert_fingerprint(cert_path: Path) -> str:
    """Return SHA-256 fingerprint of the PEM certificate."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        import binascii

        pem_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem_data)
        fp_bytes = cert.fingerprint(hashes.SHA256())
        hex_pairs = [f"{b:02X}" for b in fp_bytes]
        return ":".join(hex_pairs)
    except ImportError:
        pass

    result = subprocess.run(
        ["openssl", "x509", "-fingerprint", "-sha256", "-noout", "-in", str(cert_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        # output: "SHA256 Fingerprint=AA:BB:..."
        line = result.stdout.strip()
        return line.split("=", 1)[-1] if "=" in line else line
    return "(fingerprint unavailable)"


@cert_app.command("install")
def cert_install():
    """Add the Fencio Proxy Root CA to the system trust store."""
    from rich.panel import Panel

    if not CERT_PATH.exists():
        console.print(
            f"[red]Certificate not found at {CERT_PATH}.[/red]\n"
            "[yellow]Run [bold]prism start[/bold] first — the proxy generates its CA on first launch.[/yellow]"
        )
        raise typer.Exit(1)

    fingerprint = _cert_fingerprint(CERT_PATH)

    if sys.platform == "darwin":
        platform_action = (
            "sudo security add-trusted-cert -d -r trustRoot "
            f"-k /Library/Keychains/System.keychain {CERT_PATH}"
        )
        platform_label = "macOS Keychain (system)"
    elif Path("/etc/debian_version").exists():
        platform_action = (
            f"sudo cp {CERT_PATH} /usr/local/share/ca-certificates/fencio-root-ca.crt "
            "&& sudo update-ca-certificates"
        )
        platform_label = "Debian/Ubuntu system trust store"
    elif Path("/etc/redhat-release").exists():
        platform_action = (
            f"sudo cp {CERT_PATH} /etc/pki/ca-trust/source/anchors/fencio-root-ca.pem "
            "&& sudo update-ca-trust"
        )
        platform_label = "RHEL/Fedora system trust store"
    else:
        platform_action = None
        platform_label = "unknown"

    console.print(Panel.fit(
        f"[bold]Certificate:[/bold] {CERT_PATH}\n"
        f"[bold]Fingerprint:[/bold] {fingerprint}\n"
        f"[bold]Destination:[/bold] {platform_label}\n\n"
        "The Fencio Proxy Root CA must be added to your system trust store\n"
        "for HTTPS inspection to work without certificate errors.",
        title="Fencio Proxy Root CA",
        border_style="blue",
    ))

    if platform_action is None:
        console.print("[yellow]Automatic installation is not supported on this platform.[/yellow]")
        console.print(_MANUAL_INSTRUCTIONS)
        raise typer.Exit(0)

    if not typer.confirm("Add to system trust store?", default=False):
        console.print("\n[yellow]Skipped.[/yellow]")
        console.print(_MANUAL_INSTRUCTIONS)
        raise typer.Exit(1)

    # Remove any existing Fencio Root CA before installing to avoid stale cert mismatches
    if sys.platform == "darwin":
        subprocess.run(
            ["sudo", "security", "delete-certificate", "-c", "Fencio Root CA",
             "/Library/Keychains/System.keychain"],
            capture_output=True,
        )

    result = subprocess.run(platform_action, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        console.print(Panel.fit(
            "Certificate installed. HTTPS inspection is ready.",
            border_style="green",
        ))
    else:
        error_detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        console.print(Panel.fit(
            f"[red]Installation failed.[/red]\n\n{error_detail}",
            border_style="red",
        ))
        console.print(_MANUAL_INSTRUCTIONS)
        raise typer.Exit(1)


@cert_app.command("status")
def cert_status():
    """Check whether the Fencio Proxy Root CA is trusted by the system."""
    if not CERT_PATH.exists():
        console.print(
            f"[yellow]Certificate not found at {CERT_PATH}.[/yellow]\n"
            "[dim]Run [bold]prism start[/bold] to generate it, then [bold]prism cert install[/bold].[/dim]"
        )
        raise typer.Exit(1)

    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "verify-cert", "-c", str(CERT_PATH)],
            capture_output=True,
            text=True,
        )
    else:
        result = subprocess.run(
            ["openssl", "verify", "-CApath", "/etc/ssl/certs", str(CERT_PATH)],
            capture_output=True,
            text=True,
        )

    if result.returncode == 0:
        console.print("[green]Certificate is trusted by the system.[/green]")
    else:
        console.print("[yellow]Certificate is NOT trusted by the system.[/yellow]")
        console.print(f"[dim]Run [bold]prism cert install[/bold] to add it.[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# health command
# ──────────────────────────────────────────────────────────────────────────────

@app.command("health", help="Run a full connectivity and configuration health check.")
def health():
    """Run a full connectivity and configuration health check."""
    checks: list[tuple[str, bool, str]] = []

    # 1. OS cert trust
    if not CERT_PATH.exists():
        checks.append(("OS cert trust", False, "cert not found — run prism cert install"))
    else:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["security", "verify-cert", "-c", str(CERT_PATH)],
                capture_output=True, text=True,
            )
        else:
            result = subprocess.run(
                ["openssl", "verify", str(CERT_PATH)],
                capture_output=True, text=True,
            )
        checks.append(("OS cert trust", result.returncode == 0,
            "" if result.returncode == 0 else "cert not trusted — run: prism cert install"))

    # 2. Proxy port 47100
    proxy_ok = _port_open(47100)
    checks.append((
        "Proxy port 47100",
        proxy_ok,
        "" if proxy_ok else "not listening — is the proxy running? (make run-proxy)",
    ))

    # 3. Proxy API port 47101
    api_port_ok = _port_open(47101)
    checks.append((
        "Proxy API port 47101",
        api_port_ok,
        "" if api_port_ok else "not listening",
    ))

    # 4. Prism management plane
    mgmt_ok = _http_ok(f"{_prism_url()}/health")
    checks.append((
        "Prism management plane",
        mgmt_ok,
        "" if mgmt_ok else "unreachable — is prism running? (prism start)",
    ))

    # 5. Agent registered
    try:
        r = httpx.get("http://localhost:47101/api/admin/agents", timeout=3)
        data = r.json()
        agent_ok = data.get("count", len(data.get("agents", []))) > 0
        agent_detail = "" if agent_ok else "no agents registered — run prism agents create"
    except Exception:
        agent_ok = False
        agent_detail = "proxy API unreachable"
    checks.append(("Agent registered", agent_ok, agent_detail))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")

    for name, passed, detail in checks:
        result_cell = "[green]✓ pass[/green]" if passed else "[red]✗ fail[/red]"
        table.add_row(name, result_cell, detail)

    console.print(table)

    if all(passed for _, passed, _ in checks):
        console.print("[green]All checks passed.[/green]")
    else:
        console.print("[red]One or more checks failed.[/red]")
        raise typer.Exit(code=1)


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
