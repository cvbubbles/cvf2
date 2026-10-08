"""Visualize 16-bit depth maps (CV_16UC1) stored as PNG images.

Reads every .png in a folder, truncates depth values beyond ``--max-depth``
(pixels beyond the threshold become black background), normalizes the valid
range to 8-bit, applies a colormap, then either saves the results to an output
folder and/or displays them in a window.

Usage examples:
	python visualizeDepth.py data/depth --out data/depth_vis --max-depth 5000
	python visualizeDepth.py data/depth --display --max-depth 5000
	python visualizeDepth.py data/depth --out vis --display
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# 保存到另一文件夹（截断 5000mm，超出部分黑底）
#python visualizeDepth.py data/depth --out data/depth_vis --max-depth 5000

# 窗口逐张显示
#python visualizeDepth.py data/depth --display --max-depth 5000

# 既保存又显示
#python visualizeDepth.py data/depth --out vis --display --colormap turbo


COLORMAPS = {
	"jet": cv2.COLORMAP_JET,
	"turbo": cv2.COLORMAP_TURBO,
	"viridis": cv2.COLORMAP_VIRIDIS,
	"gray": None,  # no colormap, plain grayscale
}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Visualize 16-bit depth maps (CV_16UC1) stored as PNG."
	)
	parser.add_argument("input", type=str, help="Folder containing depth .png images")
	parser.add_argument(
		"--out",
		type=str,
		default=None,
		help="Output folder for visualized images (created if missing). "
		"Required unless --display is used.",
	)
	parser.add_argument(
		"--display",
		action="store_true",
		help="Show the depth maps in a window instead of (or in addition to) saving.",
	)
	parser.add_argument(
		"--max-depth",
		type=float,
		default=None,
		help="Depth truncation threshold (e.g. 5000 for mm). Pixels beyond it "
		"become black background. Defaults to each image's own maximum value.",
	)
	parser.add_argument(
		"--colormap",
		type=str,
		default="jet",
		choices=sorted(COLORMAPS.keys()),
		help="Colormap used for visualization (default: jet).",
	)
	parser.add_argument(
		"--suffix",
		type=str,
		default="_vis",
		help="Suffix appended to saved filenames (default: _vis).",
	)
	return parser.parse_args()


def process_depth(depth: np.ndarray, max_depth: float | None) -> np.ndarray:
	"""Truncate and normalize a 16-bit depth map into an 8-bit image.

	Values beyond ``max_depth`` (and any 0/invalid pixels) become black, and
	the remaining [0, max_depth] range is stretched to [0, 255].
	"""
	truncated = depth.astype(np.float32)
	if max_depth is None:
		max_depth = float(truncated.max())
	if max_depth <= 0:
		max_depth = 1.0
	truncated[truncated > max_depth] = 0.0  # truncate -> black background
	vis8 = np.clip(truncated / max_depth * 255.0, 0, 255).astype(np.uint8)
	return vis8


def main() -> None:
	args = parse_args()

	in_dir = Path(args.input)
	if not in_dir.is_dir():
		raise NotADirectoryError(f"Input folder not found: {in_dir}")

	png_files = sorted(in_dir.glob("*.png"))
	if not png_files:
		raise FileNotFoundError(f"No .png files found in: {in_dir}")

	out_dir = Path(args.out) if args.out else None
	if out_dir is not None:
		out_dir.mkdir(parents=True, exist_ok=True)

	if out_dir is None and not args.display:
		raise SystemExit("Specify at least one of --out or --display.")

	cmap = COLORMAPS[args.colormap]
	win_name = "Depth Visualization"
	if args.display:
		cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)

	try:
		for i, path in enumerate(png_files):
			depth = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
			if depth is None:
				print(f"[skip] failed to read: {path.name}")
				continue
			if depth.ndim != 2 or depth.dtype != np.uint16:
				print(f"[skip] not a 16-bit single-channel image: {path.name}")
				continue

			vis8 = process_depth(depth, args.max_depth)
			vis = cv2.applyColorMap(vis8, cmap) if cmap is not None else cv2.cvtColor(
				vis8, cv2.COLOR_GRAY2BGR
			)

			if out_dir is not None:
				out_path = out_dir / f"{path.stem}{args.suffix}.png"
				cv2.imwrite(str(out_path), vis)
				print(f"[save] {out_path}")

			if args.display:
				cv2.imshow(win_name, vis)
				print(
					f"[show] {path.name} ({i + 1}/{len(png_files)}) "
					f"- press any key for next, ESC/q to quit"
				)
				key = cv2.waitKey(0) & 0xFF
				if key in (27, ord("q")):
					break
	finally:
		if args.display:
			cv2.destroyAllWindows()


if __name__ == "__main__":
	main()
