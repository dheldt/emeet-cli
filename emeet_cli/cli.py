"""
emeet-cli — command-line interface for the eMeet Pixy webcam.

Commands:
  capture   Take a photo
  zoom      Get or set zoom level (0–100)
  tilt      Get or set tilt position (0–100, 50 = center)
  pan       Get or set pan position (0–100, 50 = center)
  reset     Return camera to default position and zoom
  devices   List available camera indices (OpenCV)
  info      Show current pan/tilt/zoom values
"""

import sys
import click
from . import camera


def _err(msg: str):
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


@click.group()
def cli():
    """Control the eMeet Pixy webcam from the command line."""


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

@cli.command()
@click.option("-o", "--output", default="photo.jpg", show_default=True,
              help="Output file path. Extension determines format (jpg, png).")
@click.option("-d", "--device", default=None, type=int,
              help="Camera device index (default: auto-detect EMEET PIXY).")
def capture(output, device):
    """Take a photo and save it to a file."""
    try:
        camera.capture(output, device_index=device)
        click.echo(f"Saved: {output}")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# zoom
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("level", required=False, type=click.IntRange(0, 100), metavar="LEVEL")
def zoom(level):
    """
    Get or set zoom level.

    LEVEL is 0–100 (0 = widest, 100 = maximum zoom).
    Omit LEVEL to show the current value.
    """
    try:
        if level is None:
            info = camera.zoom_get()
            click.echo(f"Zoom: {info['current']}/100  (raw {info['raw']}, range {info['raw_min']}–{info['raw_max']})")
        else:
            camera.zoom_set(level)
            click.echo(f"Zoom set to {level}/100")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# tilt
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("level", required=False, type=click.IntRange(0, 100), metavar="LEVEL")
def tilt(level):
    """
    Get or set tilt position.

    LEVEL is 0–100 (0 = full down, 50 = center, 100 = full up).
    Omit LEVEL to show the current value.
    """
    try:
        if level is None:
            info = camera.pan_tilt_get()
            t = info["tilt"]
            click.echo(f"Tilt: {t['current']}/100  (raw {t['raw']})")
        else:
            camera.tilt_set(level)
            click.echo(f"Tilt set to {level}/100")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# pan
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("level", required=False, type=click.IntRange(0, 100), metavar="LEVEL")
def pan(level):
    """
    Get or set pan position.

    LEVEL is 0–100 (0 = full left, 50 = center, 100 = full right).
    Omit LEVEL to show the current value.
    """
    try:
        if level is None:
            info = camera.pan_tilt_get()
            p = info["pan"]
            click.echo(f"Pan: {p['current']}/100  (raw {p['raw']})")
        else:
            camera.pan_set(level)
            click.echo(f"Pan set to {level}/100")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

@cli.command()
def reset():
    """Return the camera to center position and minimum zoom."""
    try:
        camera.reset()
        click.echo("Camera reset to default position.")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

@cli.command()
def info():
    """Show current zoom, pan, and tilt values."""
    try:
        z = camera.zoom_get()
        pt = camera.pan_tilt_get()
        click.echo(f"Zoom:  {z['current']}/100")
        click.echo(f"Pan:   {pt['pan']['current']}/100")
        click.echo(f"Tilt:  {pt['tilt']['current']}/100")
    except Exception as e:
        _err(str(e))


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------

@cli.command()
def devices():
    """List available camera device indices."""
    cams = camera.list_cameras()
    if not cams:
        click.echo("No cameras found.")
    for c in cams:
        click.echo(f"  [{c['index']}] {c['name']}  ({c['width']}x{c['height']})")


if __name__ == "__main__":
    cli()
