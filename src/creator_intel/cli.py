"""Typer command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
import json

import typer

from . import __version__
from .config import Settings
from .commercial import RuleClassifier
from .io_utils import read_json
from .errors import CreatorIntelError
from .logging_utils import configure_console_logging
from .pipeline import analyze_profile as run_profile
from .models import ProductBrief


app = typer.Typer(
    name="creator-intel",
    help="Reverse engineer commercial content capability from public TikTok creator content.",
    no_args_is_help=True,
)


@app.command("analyze-profile")
def analyze_profile_command(
    url: Annotated[str, typer.Argument(help="Public TikTok creator profile URL")],
    limit: Annotated[int, typer.Option(min=1, max=500, help="Maximum videos to fetch")] = 100,
    top: Annotated[int, typer.Option(min=1, max=50, help="Deep-analysis candidate count")] = 10,
    deep_analysis: Annotated[bool, typer.Option("--deep-analysis", help="Download and analyze top candidates")] = False,
    metadata_only: Annotated[bool, typer.Option("--metadata-only", help="Never download media or call AI")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-run completed media/AI stages")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Do not read cached results for this run")] = False,
    output: Annotated[Path | None, typer.Option(help="Report root override")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="Print structured stage logs")] = False,
    direct_enrichment: Annotated[
        bool,
        typer.Option("--direct-enrichment", help="Fetch each public video URL to enrich explicit ad/product/caption fields"),
    ] = False,
    analyze_comments: Annotated[
        bool,
        typer.Option("--analyze-comments", help="Reserved optional module; does not block the MVP pipeline"),
    ] = False,
) -> None:
    """Analyze a public creator profile."""
    if metadata_only and deep_analysis:
        raise typer.BadParameter("Choose either --metadata-only or --deep-analysis, not both.")
    configure_console_logging(verbose)
    if analyze_comments:
        typer.echo("WARNING: comment purchase-intent analysis is not implemented in v0.2; continuing without it.", err=True)
    try:
        result = run_profile(
            url,
            limit=limit,
            top=top,
            deep_analysis=deep_analysis,
            metadata_only=metadata_only,
            force=force,
            no_cache=no_cache,
            output=output,
            verbose=verbose,
            direct_enrichment=direct_enrichment,
        )
    except CreatorIntelError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Analyzed @{result.profile.username}: {len(result.videos)} public videos discovered.")
    typer.echo(f"Commercial candidates selected: {len(result.candidates)}")
    typer.echo(f"Run status: {result.manifest.status}")
    if result.manifest.errors:
        typer.echo(f"Recorded failures: {len(result.manifest.errors)}", err=True)
        for error in result.manifest.errors[:5]:
            typer.echo(f"WARNING: {error}", err=True)
    typer.echo(f"Report: {result.report_dir / 'report.md'}")


@app.command("analyze-video")
def analyze_video_command(
    url: Annotated[str, typer.Argument(help="Public TikTok video URL")],
    force: Annotated[bool, typer.Option("--force")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    output: Annotated[Path | None, typer.Option(help="Output directory override")] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Run the independent single-video deep-analysis pipeline."""
    configure_console_logging(verbose)
    try:
        from .video_pipeline import analyze_video_url

        result, report_path = analyze_video_url(
            url, settings=Settings(), force=force, no_cache=no_cache, output=output, verbose=verbose
        )
    except CreatorIntelError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Video stage: {result.stage.value}")
    typer.echo(f"Report: {report_path}")


@app.command("prepare-codex")
def prepare_codex_command(
    url: Annotated[str, typer.Argument(help="Public TikTok creator profile URL")],
    limit: Annotated[int, typer.Option(min=1, max=500, help="Maximum videos to fetch")] = 100,
    top: Annotated[int, typer.Option(min=1, max=50, help="Highest-view commercial candidates to prepare")] = 10,
    context_videos: Annotated[
        int,
        typer.Option(min=0, max=10, help="Strong organic/context videos to prepare for creator-fit comparison"),
    ] = 5,
    product_name: Annotated[str | None, typer.Option(help="Product name")] = None,
    product_description: Annotated[str | None, typer.Option(help="Product description or selling brief")] = None,
    product_category: Annotated[str | None, typer.Option(help="Product category")] = None,
    product_price: Annotated[float | None, typer.Option(min=0, help="Optional product price")] = None,
    product_image: Annotated[
        list[Path] | None,
        typer.Option("--product-image", help="Product image path; repeat for multiple images"),
    ] = None,
    selling_point: Annotated[
        list[str] | None,
        typer.Option("--selling-point", help="Product selling point; repeat as needed"),
    ] = None,
    target_audience: Annotated[
        list[str] | None,
        typer.Option("--target-audience", help="Target audience; repeat as needed"),
    ] = None,
    no_audio: Annotated[bool, typer.Option("--no-audio", help="Skip optional local audio extraction")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-download and regenerate media evidence")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Ignore cached scenes")] = False,
    output: Annotated[Path | None, typer.Option(help="Report root override")] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Prepare public evidence for analysis by the current Codex session; no API key required."""

    configure_console_logging(verbose)
    supplied_product = any(
        [product_name, product_description, product_category, product_price is not None, product_image, selling_point, target_audience]
    )
    product = (
        ProductBrief(
            name=product_name,
            description=product_description,
            category=product_category,
            price=product_price,
            selling_points=selling_point or [],
            target_audience=target_audience or [],
            image_paths=[str(path) for path in (product_image or [])],
        )
        if supplied_product
        else None
    )
    try:
        from .codex_native import prepare_codex_profile

        result = prepare_codex_profile(
            url,
            limit=limit,
            top=top,
            context_videos=context_videos,
            product=product,
            force=force,
            no_cache=no_cache,
            output=output,
            verbose=verbose,
            extract_audio_track=not no_audio,
        )
    except CreatorIntelError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    ready = sum(asset.status == "ready" for asset in result.job.assets)
    typer.echo(f"Prepared @{result.job.creator.username}: {ready}/{len(result.job.assets)} assets ready.")
    typer.echo("No AI analysis was executed by this command.")
    typer.echo(f"Codex job: {result.job_path}")
    typer.echo(f"Instructions: {result.instructions_path}")
    typer.echo(f"Required schema: {result.schema_path}")
    typer.echo(f"Expected review: {result.review_output_path}")


@app.command("finalize-codex")
def finalize_codex_command(
    job: Annotated[Path, typer.Argument(help="Path to codex_job.json")],
    review: Annotated[Path | None, typer.Option(help="Review JSON override")] = None,
) -> None:
    """Validate a current-Codex review and render product-fit/script reports."""

    try:
        from .codex_native import finalize_codex_review

        report_path = finalize_codex_review(job, review)
    except CreatorIntelError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Validated Codex review and wrote report: {report_path}")


@app.command("version")
def version_command() -> None:
    typer.echo(__version__)


@app.command("benchmark-classifier")
def benchmark_classifier_command(
    fixture: Annotated[
        Path,
        typer.Option(help="Human-labeled JSON fixture"),
    ] = Path("tests/fixtures/commercial_cases.json"),
) -> None:
    """Print an offline confusion report for the deterministic classifier."""
    settings = Settings()
    path = fixture if fixture.is_absolute() else settings.project_root / fixture
    cases = read_json(path)
    report = RuleClassifier(settings.rules_path).benchmark(cases)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
