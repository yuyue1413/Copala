"""Long-term memory CLI commands."""

import typer
from rich.console import Console
from rich.table import Table

from copala.cli.runtime import load_runtime_config
from copala.utils.helpers import sync_workspace_templates

console = Console()
memory_app = typer.Typer(help="Manage RAG-backed personal memory")


@memory_app.command("reindex")
def memory_reindex(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Reindex workspace memory files into the RAG store."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = load_runtime_config(config, workspace)
    sync_workspace_templates(cfg.workspace_path, silent=True)
    count = MemoryRAGStore(cfg.workspace_path).reindex_workspace()
    console.print(f"[green]Indexed {count} memory item(s).[/green]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Memory search query"),
    limit: int = typer.Option(8, "--limit", "-n", help="Number of results"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Search personal memory with BM25 + dense RRF retrieval."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = load_runtime_config(config, workspace)
    results = MemoryRAGStore(cfg.workspace_path).retrieve(query, top_k=limit)
    table = Table(title="Personal Memory Search")
    table.add_column("Type", style="cyan")
    table.add_column("Score", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Text")
    for result in results:
        item = result.item
        table.add_row(item.type, f"{result.score:.3f}", item.source, item.text[:180])
    console.print(table if results else "[yellow]No relevant memory found.[/yellow]")


@memory_app.command("list")
def memory_list(
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of items"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List stored personal memory items."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = load_runtime_config(config, workspace)
    items = MemoryRAGStore(cfg.workspace_path).list_items(mem_type=type, limit=limit)
    table = Table(title="Personal Memory")
    table.add_column("ID", style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Text")
    for item in items:
        table.add_row(item.id[:10], item.type, item.source, item.text[:180])
    console.print(table if items else "[yellow]No memory items found.[/yellow]")
