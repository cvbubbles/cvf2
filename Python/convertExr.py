"""Convert EXR depth images to lossless float32 TIFF files.

The depth maps in the target folder are stored as single-channel
floating-point EXR (32-bit float, in meters, with -9999 sentinel
pixels).  A PNG/JPG export would quantize or clip those values, so we
write IEEE float32 TIFF instead, which keeps every value -- including
sentinels, NaN and Inf -- bit-exact.

Reader (EXR):
  1. OpenCV  -- the opencv-python wheels ship the EXR codec compiled
     but disabled, so ``OPENCV_IO_ENABLE_OPENEXR`` is set below before
     ``cv2`` is imported.  OpenCV decodes EXR into float32 arrays.
  2. ``OpenEXR`` python bindings -- fallback used when OpenCV cannot
     decode a given file (e.g. an uint32 EXR).

Writer (TIFF):
  1. ``tifffile`` if installed (can store float32/float64, NaN/Inf).
  2. Otherwise OpenCV ``cv2.imwrite`` (verified to write float32 TIFF
     losslessly for single-channel grayscale images).

Usage examples:
    python convertExr.py
    python convertExr.py --input F:\\zj\\4cam\\scene1\\data1\\depth
    python convertExr.py --out F:\\zj\\4cam\\scene1\\data1\\depth_tiff
    python convertExr.py --recursive --verify
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Must happen before ``import cv2``: the EXR codec in opencv-python is
# compiled but disabled unless this env var is set.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np

DEFAULT_INPUT = Path(r"F:\zj\4cam\scene1\data1\depth")
OUT_SUFFIX = ".tiff"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Losslessly convert EXR images (e.g. depth) to float32 TIFF."
	)
	parser.add_argument(
		"--input",
		"-i",
		type=Path,
		default=DEFAULT_INPUT,
		help="Folder containing the .exr files (default: %(default)s)",
	)
	parser.add_argument(
		"--out",
		"-o",
		type=Path,
		default=None,
		help="Output folder (default: <input folder>_tiff)",
	)
	parser.add_argument(
		"--recursive",
		"-r",
		action="store_true",
		help="Also scan sub-folders for .exr files",
	)
	parser.add_argument(
		"--verify",
		action="store_true",
		help="Re-read every written .tiff and abort if it differs from the EXR",
	)
	parser.add_argument(
		"--channel",
		type=str,
		default=None,
		help="EXR channel to export (OpenEXR fallback path; "
		"default: auto pick Y/Z/R/G/B/A)",
	)
	return parser.parse_args()


# --------------------------------------------------------------------------
# EXR readers
# --------------------------------------------------------------------------

def read_exr_cv2(path: Path) -> np.ndarray:
	"""Read an EXR with OpenCV; returns float32 for float/half EXR files."""
	import cv2

	img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
	if img is None:
		raise RuntimeError(
			f"OpenCV could not decode {path.name} (unsupported channel layout "
			"or pixel type?)"
		)
	return np.asarray(img)


def read_exr_openexr(path: Path, channel: str | None) -> np.ndarray:
	"""Read a single EXR channel with the official OpenEXR bindings."""
	import OpenEXR
	import Imath

	file = OpenEXR.InputFile(str(path))
	try:
		header = file.header()
		box = header["dataWindow"]
		width = box.max.x - box.min.x + 1
		height = box.max.y - box.min.y + 1
		channels = header["channels"]
		names = list(channels.keys())
		if not names:
			raise RuntimeError(f"EXR has no channels: {path.name}")

		if channel is None:
			for preferred in ("Y", "Z", "R", "G", "B", "A"):
				if preferred in channels:
					channel = preferred
					break
			else:
				channel = names[0]
		if channel not in channels:
			raise RuntimeError(
				f"EXR has no channel {channel!r} (available: {names})"
			)

		pixel_type = channels[channel].type
		if pixel_type == Imath.PixelType.FLOAT:
			dtype = "<f4"
		elif pixel_type == Imath.PixelType.HALF:
			dtype = "<f2"
		elif pixel_type == Imath.PixelType.UINT:
			dtype = "<u4"
		else:
			raise RuntimeError(f"Unsupported EXR pixel type: {path.name}")

		buf = file.channel(channel, pixel_type)
		return np.frombuffer(buf, dtype=dtype).reshape(height, width)
	finally:
		file.close()


# --------------------------------------------------------------------------
# TIFF writers
# --------------------------------------------------------------------------

def get_writer():
	"""Return (write_tiff, backend_name). Prefer tifffile for TIFF output."""
	try:
		import tifffile

		def write_tiff(img: np.ndarray, path: Path) -> None:
			if img.dtype == np.float16:
				img = img.astype(np.float32)  # half -> float32 is lossless
			tifffile.imwrite(str(path), img)

		return write_tiff, "tifffile"
	except ImportError:
		pass

	import cv2

	def write_tiff(img: np.ndarray, path: Path) -> None:
		# float16 -> float32 (lossless); float32 is what cv2 EXR decode yields.
		if img.dtype == np.float16:
			img = img.astype(np.float32)
		if img.dtype != np.float32:
			raise RuntimeError(
				f"Cannot write dtype {img.dtype} with the OpenCV TIFF writer; "
				"install tifffile (pip install tifffile) for full support"
			)
		if img.ndim not in (2, 3):
			raise RuntimeError(f"Unexpected array shape {img.shape}")
		if not cv2.imwrite(str(path), img):
			raise RuntimeError(f"OpenCV failed to write {path.name}")

	return write_tiff, "cv2"


def read_tiff(path: Path, use_tifffile: bool) -> np.ndarray:
	"""Read a TIFF back for verification."""
	if use_tifffile:
		import tifffile

		return tifffile.imread(str(path))
	import cv2

	img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
	if img is None:
		raise RuntimeError(f"Could not read back {path.name}")
	return np.asarray(img)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def arrays_equal(a: np.ndarray, b: np.ndarray) -> bool:
	"""Bit-exact comparison that treats NaN positions as equal."""
	return a.shape == b.shape and np.array_equal(a, b, equal_nan=True)


def fmt_stats(img: np.ndarray) -> str:
	flat = img.reshape(-1)
	info = f"{img.dtype} {img.shape[1]}x{img.shape[0]}"
	nan_count = int(np.isnan(flat).sum()) if flat.dtype.kind == "f" else 0
	if flat.dtype.kind == "f":
		valid = flat[np.isfinite(flat)]
		if valid.size:
			info += f" range [{float(valid.min()):.6g}, {float(valid.max()):.6g}]"
		else:
			info += " (all NaN/Inf)"
		if nan_count:
			info += f" NaNx{nan_count}"
	return info


def find_exr_files(input_dir: Path, recursive: bool) -> list[Path]:
	pattern = "**/*.exr" if recursive else "*.exr"
	return sorted(
		p for p in input_dir.glob(pattern) if p.suffix.lower() == ".exr"
	)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
	args = parse_args()

	input_dir = args.input
	if not input_dir.is_dir():
		print(f"[error] input folder not found: {input_dir}", file=sys.stderr)
		return 1

	out_dir = args.out or input_dir.with_name(input_dir.name + "_tiff")

	exr_files = find_exr_files(input_dir, args.recursive)
	if not exr_files:
		print(f"[error] no .exr files found under {input_dir}", file=sys.stderr)
		return 1

	# Prefer cv2 for EXR reading (fast, exact for float EXR).
	try:
		read_exr = lambda p: read_exr_cv2(p)  # noqa: E731
		exr_backend = "OpenCV"
		import cv2  # noqa: F401  (validate availability early)
	except Exception:
		read_exr = lambda p: read_exr_openexr(p, args.channel)  # noqa: E731
		exr_backend = "OpenEXR"

	write_tiff, tiff_backend = get_writer()
	use_tifffile = tiff_backend == "tifffile"

	out_dir.mkdir(parents=True, exist_ok=True)
	print(
		f"input : {input_dir}\n"
		f"output: {out_dir}\n"
		f"files : {len(exr_files)} .exr found\n"
		f"reader: {exr_backend}\n"
		f"writer: {tiff_backend} (float32 TIFF)\n"
	)

	ok_count = 0
	for src in exr_files:
		try:
			img = read_exr(src)
		except Exception as exc:  # fall back to the OpenEXR binding per file
			if exr_backend == "OpenCV":
				try:
					img = read_exr_openexr(src, args.channel)
				except Exception:
					print(f"  [FAIL] {src.name}: {exc}", file=sys.stderr)
					continue
			else:
				print(f"  [FAIL] {src.name}: {exc}", file=sys.stderr)
				continue

		dst = out_dir / (src.stem + OUT_SUFFIX)
		try:
			write_tiff(img, dst)
		except Exception as exc:
			print(f"  [FAIL] {src.name}: {exc}", file=sys.stderr)
			continue

		if args.verify:
			try:
				back = read_tiff(dst, use_tifffile)
				if not arrays_equal(img, back):
					print(
						f"  [MISMATCH] {src.name}: round trip is not bit-exact",
						file=sys.stderr,
					)
					continue
			except Exception as exc:
				print(f"  [FAIL] verify {src.name}: {exc}", file=sys.stderr)
				continue

		ok_count += 1
		check = " [verified]" if args.verify else ""
		print(f"  [ OK ] {src.name} ({fmt_stats(img)}) -> {dst.name}{check}")

	print(f"\ndone: {ok_count}/{len(exr_files)} converted to {out_dir}")
	if ok_count != len(exr_files):
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
