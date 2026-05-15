"""Command-line interface for focus-bridge."""

from pathlib import Path

import typer

from focus_bridge.pipeline import convert_cur_to_focus
from focus_bridge.validation import FocusValidationError

app = typer.Typer(
    help="Convert AWS CUR 2.0 billing exports to FOCUS 1.2 specification.",
    no_args_is_help=True,
)


@app.command()
def convert(
    input_path: Path = typer.Option(
        ...,
        "--input",
        "-i",
        exists=True,
        readable=True,
        help="Path to the AWS CUR 2.0 Parquet file to convert.",
    ),
    output_path: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path where the FOCUS 1.2 Parquet file will be written.",
    ),
) -> None:
    """Convert a CUR 2.0 Parquet file to FOCUS 1.2 Parquet."""
    typer.echo(f"Converting {input_path} → {output_path}")
    try:
        convert_cur_to_focus(input_path, output_path)
    except FocusValidationError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho("✓ Conversion complete", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()