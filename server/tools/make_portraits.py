"""Cut single-figure portraits out of a turnaround sheet.

Why: reference-to-video conditions on the whole reference image. A sheet showing
three figures gives the model three faces on one canvas, and models sometimes
composite a duplicate person into a shot. A single-figure crop removes that risk;
the sheet stays as the human-readable canon.

Detection is texture-based: a studio backdrop is smooth (near-zero column
standard deviation), a figure is not. Columns whose texture exceeds a fraction
of the page maximum are grouped into runs, then trimmed to the densest core.

Usage:
    python tools/make_portraits.py                      # all sheets in series/frames
    python tools/make_portraits.py --figure 1           # centre/three-quarter view
"""
import argparse
import os
from pathlib import Path

from PIL import Image
import numpy as np

DEFAULT_SHEETS = [
    ("lena_character_sheet.png", "lena_maid_portrait.jpg"),
    ("lena_suit_sheet.png", "lena_suit_portrait.jpg"),
    ("vivian_character_sheet.png", "vivian_portrait.jpg"),
    ("marcus_character_sheet.png", "marcus_portrait.jpg"),
]

MAX_WIDTH = 900
MAX_HEIGHT = 1400      # keep a standing figure from becoming a 1:4 ribbon
MIN_SHORT_SIDE = 480   # upscale only if the crop is genuinely small


def detect_figures(image: Image.Image, max_figures: int = 3) -> list:
    """Return (x0, y0, x1, y1) boxes, left to right."""
    grey = np.asarray(image.convert("L")).astype(np.float32)
    h, w = grey.shape

    # Backdrop is smooth; a figure has vertical texture.
    texture = grey.std(axis=0)
    kernel = max(3, w // 200)
    texture = np.convolve(texture, np.ones(kernel) / kernel, mode="same")

    threshold = max(texture.max() * 0.25, 3.0)
    runs, start = [], None
    for x, active in enumerate(texture > threshold):
        if active and start is None:
            start = x
        elif not active and start is not None:
            runs.append([start, x])
            start = None
    if start is not None:
        runs.append([start, w])

    # Bridge hairline gaps only; keep genuinely separate figures apart.
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < w * 0.012:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    merged = [r for r in merged if r[1] - r[0] > w * 0.05]

    if len(merged) > max_figures:
        scored = sorted(merged, key=lambda r: float(texture[r[0]:r[1]].max()), reverse=True)
        merged = sorted(scored[:max_figures], key=lambda r: r[0])

    boxes = []
    for x0, x1 in merged:
        x0 = max(0, int(x0))
        x1 = min(w, int(x1))
        # Vertical extent: rows that differ from the backdrop in this band.
        band = grey[:, x0:x1]
        backdrop = float(np.median(band))
        row_signal = np.abs(band - backdrop).mean(axis=1)
        rows = np.where(row_signal > 8.0)[0]
        if rows.size:
            y0, y1 = int(rows[0]), int(rows[-1])
        else:
            y0, y1 = 0, h
        if x1 - x0 < w * 0.05 or y1 - y0 < h * 0.2:
            continue
        boxes.append((x0, y0, x1, y1))

    # Clamp each box to the midpoint towards its neighbours so a wide arm or a
    # dropped hand from the adjacent figure cannot bleed into the crop.
    clamped = []
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        if i > 0:
            x0 = max(x0, int((boxes[i - 1][2] + x0) / 2))
        if i < len(boxes) - 1:
            x1 = min(x1, int((x1 + boxes[i + 1][0]) / 2))
        clamped.append((x0, y0, x1, y1))
    return clamped


def crop_figure(image: Image.Image, box, pad_x: float = 0.04, pad_y: float = 0.02) -> Image.Image:
    x0, y0, x1, y1 = box
    dx = int((x1 - x0) * pad_x)
    dy = int((y1 - y0) * pad_y)
    crop = image.crop(
        (max(0, x0 - dx), max(0, y0 - dy), min(image.width, x1 + dx), min(image.height, y1 + dy))
    ).convert("RGB")

    # Fit inside MAX_WIDTH x MAX_HEIGHT without distorting the figure.
    scale = min(MAX_WIDTH / crop.width, MAX_HEIGHT / crop.height)
    if scale > 1.0:
        scale = min(scale, 2.0)                     # avoid blowing up a small crop
        if min(crop.size) * scale < MIN_SHORT_SIDE:
            scale = min(MIN_SHORT_SIDE / min(crop.size), 2.0)
    if abs(scale - 1.0) > 0.01:
        crop = crop.resize(
            (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS
        )
    return crop


def make_portraits(frames_dir: str, out_dir: str, figure: int = 0) -> list:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    written = []
    for sheet, out_name in DEFAULT_SHEETS:
        src = Path(frames_dir) / sheet
        if not src.exists():
            print(f"skip {sheet}: not found")
            continue
        image = Image.open(src)
        boxes = detect_figures(image)
        if not boxes:
            print(f"skip {sheet}: no figure detected")
            continue
        index = min(figure, len(boxes) - 1)
        crop = crop_figure(image, boxes[index])
        dst = Path(out_dir) / out_name
        crop.save(dst, format="JPEG", quality=92, optimize=True)
        print(f"{sheet}: {len(boxes)} figure(s) -> {dst} {crop.size} {os.path.getsize(dst)//1024} KB")
        written.append(str(dst))
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default="series/frames")
    ap.add_argument("--out", default="series/portraits")
    ap.add_argument("--figure", type=int, default=0, help="0=front, 1=three-quarter, 2=profile")
    args = ap.parse_args()
    make_portraits(args.frames, args.out, args.figure)
