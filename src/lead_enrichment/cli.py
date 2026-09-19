"""CLI — `lead-enrich` and `python -m lead_enrichment.cli`."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.progress import track

from .config import load_config
from .pipeline import EnrichmentPipeline
from .ingest import load_file

app = typer.Typer(
    name="lead-enrich",
    help="Lead Enrichment — ingest one big CSV/Excel file, get back tiered files (Platinum → Reject) via TypeSafe Jev.",
    add_completion=False,
)
console = Console()

# ── logging setup ───────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, rich_tracebacks=True)],
        force=True,
    )
    # quiet noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ── commands ────────────────────────────────────────────────────────────────

@app.command("enrich")
def enrich(
    input: Path = typer.Argument(..., help="Input file: CSV, TSV, XLSX, XLS, Parquet, JSON/JSONL", exists=True, dir_okay=False),
    output: Path = typer.Option(Path("./out"), "--output", "-o", help="Output directory (created if needed)"),
    format: str = typer.Option("csv", "--format", "-f", help="Output format: csv or xlsx", case_sensitive=False),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config YAML (see examples/icp_config.yaml)"),
    sheet: Optional[str] = typer.Option(None, "--sheet", help="Excel sheet name/index (auto-picks largest if omitted)"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="Override concurrency (env: LEAD_ENRICH_CONCURRENCY)"),
    no_dedup: bool = typer.Option(False, "--no-dedup", help="Disable deduplication"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Enrich a single file → tiered outputs.

    Example:
        lead-enrich enrich leads.csv -o ./out --format xlsx
        lead-enrich enrich data.xlsx --config icp_config.yaml --concurrency 30
    """
    setup_logging(verbose)
    log = logging.getLogger(__name__)

    cfg = load_config(config)
    if concurrency:
        cfg.runtime.concurrency = concurrency
    if verbose:
        cfg.runtime.model = cfg.runtime.model  # no-op, just keeps cfg

    console.rule("[bold]Lead Enrichment · Jev (TypeSafe System One)")
    console.print(f"[dim]Input:[/] [bold]{input}[/]  [dim]→[/] [bold]{output}[/]  [dim]format={format} model={cfg.runtime.model} concurrency={cfg.runtime.concurrency}[/]")
    if config:
        console.print(f"[dim]Config: {config}[/]")
    console.print(f"[dim]ICP:[/] {cfg.icp.name} — {cfg.icp.description[:90]}…")

    # sheet index handling: typer gives str, convert numeric
    sheet_val: Optional[str | int] = None
    if sheet is not None:
        try:
            sheet_val = int(sheet)
        except ValueError:
            sheet_val = sheet

    pipeline = EnrichmentPipeline(config=cfg)

    try:
        enriched, stats, out_dir = pipeline.run_sync(
            input_path=input,
            output_dir=output,
            output_format=format,
            sheet=sheet_val,
            dedup=not no_dedup,
        )
    except Exception as e:
        console.print(f"[bold red]Pipeline failed:[/] {e}")
        logging.exception("pipeline failed")
        raise typer.Exit(1)

    # ── pretty summary ──────────────────────────────────────────────────────
    table = Table(title=f"Results — {len(enriched)} enriched in {stats.elapsed_seconds}s {'(MOCK)' if stats.mock_mode else '(LIVE Jev)'}", show_lines=False)
    table.add_column("Tier", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Action", style="dim")
    from .export import TIER_DESCRIPTIONS
    from .schemas import Tier
    for tier in [Tier.S_PLATINUM, Tier.A_GOLD, Tier.B_SILVER, Tier.C_BRONZE, Tier.REVIEW, Tier.D_REJECT]:
        count = stats.by_tier.get(tier.value, 0)
        pct = (count / max(1, len(enriched)) * 100)
        style = {"S_platinum": "magenta", "A_gold": "green", "B_silver": "blue", "C_bronze": "yellow", "REVIEW": "red", "D_reject": "dim"}.get(tier.value, "")
        table.add_row(f"[{style}]{tier.label}[/]", str(count), f"{pct:.1f}%", TIER_DESCRIPTIONS[tier])
    console.print(table)

    if stats.by_flag:
        ft = Table(title="Flags", show_lines=False)
        ft.add_column("Flag")
        ft.add_column("Count", justify="right")
        for k, v in sorted(stats.by_flag.items(), key=lambda x: -x[1]):
            ft.add_row(k, str(v))
        console.print(ft)

    console.print(f"[green]✓ Done.[/] Outputs in [bold]{out_dir.resolve()}[/]")
    console.print(f"[dim]Open: {out_dir / 'summary.html'}  |  Manifest: {out_dir / 'manifest.json'}[/]")
    if stats.mock_mode:
        console.print("[yellow]⚠ Ran in MOCK mode (no TYPESAFE_API_KEY). Set TYPESAFE_API_KEY for live Jev scoring.[/]")


@app.command("preview")
def preview(
    input: Path = typer.Argument(..., help="Input file to preview", exists=True, dir_okay=False),
    n: int = typer.Option(10, "--rows", "-n", help="Rows to show"),
    sheet: Optional[str] = typer.Option(None, "--sheet"),
):
    """Preview the input file (after normalization) without calling Jev."""
    setup_logging(False)
    from .normalize import normalize_frame
    sheet_val: Optional[str | int] = None
    if sheet is not None:
        try:
            sheet_val = int(sheet)
        except ValueError:
            sheet_val = sheet
    df = load_file(input, sheet=sheet_val)
    console.print(f"[dim]Raw: {len(df)} rows × {len(df.columns)} cols — {list(df.columns)}[/]")
    console.print(df.head(n).to_string(index=False))
    console.rule("After normalization")
    df2 = normalize_frame(df)
    console.print(f"[dim]Normalized: {len(df2)} rows × {len(df2.columns)} cols — {list(df2.columns)}[/]")
    console.print(df2.head(n).to_string(index=False))


@app.command("init-config")
def init_config(
    output: Path = typer.Argument(Path("./icp_config.yaml"), help="Where to write the config"),
    force: bool = typer.Option(False, "--force", help="Overwrite if exists"),
):
    """Write a starter config YAML you can edit (ICP, weights, thresholds)."""
    if output.exists() and not force:
        console.print(f"[red]Refusing to overwrite {output} — use --force[/]")
        raise typer.Exit(1)
    cfg = load_config()
    cfg.to_yaml(output)
    console.print(f"[green]Wrote starter config → {output}[/]")
    console.print("[dim]Edit target_industries / target_titles / weights / thresholds, then run:[/]")
    console.print(f"  lead-enrich enrich leads.csv --config {output} -o ./out --format xlsx")


@app.command("demo")
def demo(
    output: Path = typer.Option(Path("./out_demo"), "--output", "-o"),
    format: str = typer.Option("csv", "--format", "-f"),
):
    """Run a quick demo on the bundled sample file (no API key needed — uses mock)."""
    from pathlib import Path as P
    sample = P(__file__).parent.parent.parent / "examples" / "sample_leads.csv"
    # also try src-relative
    if not sample.exists():
        sample = Path("examples/sample_leads.csv")
    if not sample.exists():
        console.print(f"[red]Sample file not found: {sample}[/]")
        raise typer.Exit(1)
    console.print(f"[dim]Demo input: {sample}[/]")
    # delegate to enrich
    enrich(input=sample, output=output, format=format, config=None, sheet=None, concurrency=None, no_dedup=False, verbose=False)


# alias: `lead-enrich` with no subcommand defaults to `enrich` for ergonomics
@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        # don't exit with error — just show help


def main():
    app()


if __name__ == "__main__":
    main()
