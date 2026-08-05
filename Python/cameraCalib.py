"""USB camera intrinsic calibration using a chessboard pattern.

Usage example:
	python cameraCalib.py --camera 0 --rows 6 --cols 9 --square-size 0.025

Hotkeys:
	Space: capture current frame if chessboard corners are found.
	q / ESC: quit and run calibration if enough samples are collected.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

import cv2
import numpy as np


def build_object_points(rows: int, cols: int, square_size: float) -> np.ndarray:
	"""Create chessboard 3D points in board coordinates (z=0)."""
	objp = np.zeros((rows * cols, 3), np.float32)
	grid = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
	objp[:, :2] = grid * square_size
	return objp


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Calibrate USB camera intrinsics with a chessboard."
	)
	parser.add_argument("--camera", type=int, default=2, help="USB camera index")
	parser.add_argument(
		"--rows",
		type=int,
		default=8,
		help="Chessboard inner corners rows (height direction)",
	)
	parser.add_argument(
		"--cols",
		type=int,
		default=11,
		help="Chessboard inner corners cols (width direction)",
	)
	parser.add_argument(
		"--square-size",
		type=float,
		default=1.0,
		help="One chess square size in user unit (e.g. meter/mm)",
	)
	parser.add_argument(
		"--min-frames",
		type=int,
		default=15,
		help="Minimum successful captures needed before calibration",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=Path("camera_intrinsics.npz"),
		help="Output file path for calibration results (.npz)",
	)
	parser.add_argument(
		"--save-yaml",
		action="store_true",
		help="Also save OpenCV YAML next to the npz file",
	)
	parser.add_argument(
		"--width",
		type=int,
		default=1280,
		help="Optional capture width, 0 means driver default",
	)
	parser.add_argument(
		"--height",
		type=int,
		default=1024,
		help="Optional capture height, 0 means driver default",
	)
	parser.add_argument(
		"--auto-capture",
		action="store_true",
		help="Enable automatic capture when pattern is detected",
	)
	parser.add_argument(
		"--auto-interval",
		type=float,
		default=0.8,
		help="Minimum seconds between auto captures",
	)
	parser.add_argument(
		"--auto-min-corner-shift",
		type=float,
		default=12.0,
		help="Minimum mean corner shift in pixels from last capture for auto mode",
	)
	parser.add_argument(
		"--auto-stop",
		action="store_true",
		help="In auto mode, stop capture once min-frames is reached",
	)
	return parser.parse_args()


def compute_mean_reprojection_error(
	object_points: list[np.ndarray],
	image_points: list[np.ndarray],
	rvecs: list[np.ndarray],
	tvecs: list[np.ndarray],
	camera_matrix: np.ndarray,
	dist_coeffs: np.ndarray,
) -> float:
	total_error = 0.0
	total_points = 0
	for i, objp in enumerate(object_points):
		projected, _ = cv2.projectPoints(
			objp, rvecs[i], tvecs[i], camera_matrix, dist_coeffs
		)
		error = cv2.norm(image_points[i], projected, cv2.NORM_L2)
		total_error += error * error
		total_points += len(objp)
	return float(np.sqrt(total_error / max(total_points, 1)))


def is_pose_changed_enough(
	new_corners: np.ndarray,
	last_corners: np.ndarray | None,
	min_shift_px: float,
) -> bool:
	if last_corners is None:
		return True
	# Use mean pixel displacement across all corners as a simple pose diversity gate.
	delta = new_corners.reshape(-1, 2) - last_corners.reshape(-1, 2)
	mean_shift = float(np.mean(np.linalg.norm(delta, axis=1)))
	return mean_shift >= min_shift_px


def save_yaml(
	yaml_path: Path,
	image_size: tuple[int, int],
	camera_matrix: np.ndarray,
	dist_coeffs: np.ndarray,
	rms: float,
	mean_error: float,
) -> None:
	fs = cv2.FileStorage(str(yaml_path), cv2.FILE_STORAGE_WRITE)
	if not fs.isOpened():
		raise RuntimeError(f"Cannot open yaml for writing: {yaml_path}")
	fs.write("image_width", int(image_size[0]))
	fs.write("image_height", int(image_size[1]))
	fs.write("camera_matrix", camera_matrix)
	fs.write("dist_coeffs", dist_coeffs)
	fs.write("rms", float(rms))
	fs.write("mean_reprojection_error", float(mean_error))
	fs.release()


def main() -> int:
	args = parse_args()
	pattern_size = (args.cols, args.rows)

	cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
	if not cap.isOpened():
		cap = cv2.VideoCapture(args.camera)
	if not cap.isOpened():
		print(f"[ERROR] Cannot open camera index {args.camera}")
		return 1

	if args.width > 0:
		cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
	if args.height > 0:
		cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

	criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
	objp = build_object_points(args.rows, args.cols, args.square_size)

	object_points: list[np.ndarray] = []
	image_points: list[np.ndarray] = []
	image_size: tuple[int, int] | None = None
	auto_enabled = args.auto_capture
	last_capture_time = 0.0
	last_captured_corners: np.ndarray | None = None

	print("[INFO] Calibration started.")
	print(
		f"[INFO] Pattern: rows={args.rows}, cols={args.cols}, "
		f"square_size={args.square_size}"
	)
	print("[INFO] Press SPACE to capture corners, press q or ESC to finish.")
	print(
		"[INFO] Press 'a' to toggle auto capture. "
		f"Auto is {'ON' if auto_enabled else 'OFF'}."
	)

	while True:
		ok, frame = cap.read()
		if not ok:
			print("[WARN] Failed to read frame, retrying...")
			continue

		gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		image_size = (gray.shape[1], gray.shape[0])

		found, corners = cv2.findChessboardCorners(
			gray,
			pattern_size,
			cv2.CALIB_CB_ADAPTIVE_THRESH
			+ cv2.CALIB_CB_NORMALIZE_IMAGE
			+ cv2.CALIB_CB_FAST_CHECK,
		)

		display = frame.copy()
		status_text = f"Captured: {len(image_points)} / {args.min_frames}"
		cv2.putText(
			display,
			status_text,
			(20, 35),
			cv2.FONT_HERSHEY_SIMPLEX,
			1.0,
			(50, 220, 50),
			2,
			cv2.LINE_AA,
		)
		mode_text = f"Mode: {'AUTO' if auto_enabled else 'MANUAL'}"
		cv2.putText(
			display,
			mode_text,
			(20, 105),
			cv2.FONT_HERSHEY_SIMPLEX,
			0.7,
			(220, 220, 50) if auto_enabled else (200, 200, 200),
			2,
			cv2.LINE_AA,
		)

		if found:
			corners2 = cv2.cornerSubPix(
				gray,
				corners,
				winSize=(11, 11),
				zeroZone=(-1, -1),
				criteria=criteria,
			)
			cv2.drawChessboardCorners(display, pattern_size, corners2, found)
			cv2.putText(
				display,
				"Pattern found - press SPACE to capture",
				(20, 70),
				cv2.FONT_HERSHEY_SIMPLEX,
				0.7,
				(0, 255, 0),
				2,
				cv2.LINE_AA,
			)

			now_ts = time.time()
			if auto_enabled:
				interval_ok = (now_ts - last_capture_time) >= args.auto_interval
				pose_ok = is_pose_changed_enough(
					corners2,
					last_captured_corners,
					args.auto_min_corner_shift,
				)
				if interval_ok and pose_ok:
					object_points.append(objp.copy())
					image_points.append(corners2)
					last_capture_time = now_ts
					last_captured_corners = corners2.copy()
					print(f"[INFO] Auto captured frame {len(image_points)}")
					if args.auto_stop and len(image_points) >= args.min_frames:
						print("[INFO] Reached min-frames in auto mode, stopping capture.")
						break
		else:
			corners2 = None
			cv2.putText(
				display,
				"Pattern not found",
				(20, 70),
				cv2.FONT_HERSHEY_SIMPLEX,
				0.7,
				(0, 0, 255),
				2,
				cv2.LINE_AA,
			)

		cv2.imshow("USB Camera Calibration", display)
		key = cv2.waitKey(1) & 0xFF

		if key == 32:  # SPACE
			if corners2 is None:
				print("[WARN] Chessboard not found, skip this frame.")
				continue
			object_points.append(objp.copy())
			image_points.append(corners2)
			last_capture_time = time.time()
			last_captured_corners = corners2.copy()
			print(f"[INFO] Captured frame {len(image_points)}")

		if key in (ord("a"), ord("A")):
			auto_enabled = not auto_enabled
			print(f"[INFO] Auto capture {'enabled' if auto_enabled else 'disabled'}.")

		if key in (27, ord("q")):
			break

	cap.release()
	cv2.destroyAllWindows()

	if len(image_points) < args.min_frames:
		print(
			f"[ERROR] Not enough captures: {len(image_points)} < {args.min_frames}. "
			"Calibration canceled."
		)
		return 2
	if image_size is None:
		print("[ERROR] No valid image size acquired.")
		return 3

	rms, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
		object_points,
		image_points,
		image_size,
		None,
		None,
	)

	mean_error = compute_mean_reprojection_error(
		object_points,
		image_points,
		rvecs,
		tvecs,
		camera_matrix,
		dist_coeffs,
	)

	args.output.parent.mkdir(parents=True, exist_ok=True)
	now = dt.datetime.now().isoformat(timespec="seconds")
	np.savez(
		args.output,
		camera_matrix=camera_matrix,
		dist_coeffs=dist_coeffs,
		image_width=image_size[0],
		image_height=image_size[1],
		rms=float(rms),
		mean_reprojection_error=float(mean_error),
		capture_count=len(image_points),
		rows=args.rows,
		cols=args.cols,
		square_size=args.square_size,
		created_at=now,
	)

	print("\n===== Calibration Result =====")
	print(f"RMS: {rms:.6f}")
	print(f"Mean reprojection error: {mean_error:.6f} px")
	print("Camera matrix:")
	print(camera_matrix)
	print("Distortion coeffs:")
	print(dist_coeffs.ravel())
	print(f"Saved: {args.output}")

	if args.save_yaml:
		yaml_path = args.output.with_suffix(".yml")
		save_yaml(
			yaml_path,
			image_size,
			camera_matrix,
			dist_coeffs,
			rms,
			mean_error,
		)
		print(f"Saved: {yaml_path}")

	return 0


if __name__ == "__main__":
	#raise SystemExit(main())
	main()
