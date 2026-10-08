import argparse
from pathlib import Path

import numpy as np
import open3d as o3d



SUPPORTED_SUFFIXES = {".obj", ".ply"}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="View a mesh model and save current-view RGB/mask by pressing Space."
	)
	parser.add_argument("model", type=str, help="Path to mesh model (.obj or .ply)")
	parser.add_argument(
		"--out",
		type=str,
		required=True,
		help="Output directory for captured RGB and mask images",
	)
	parser.add_argument("--width", type=int, default=1280, help="Window width")
	parser.add_argument("--height", type=int, default=720, help="Window height")
	parser.add_argument(
		"--window-name", type=str, default="Mesh View", help="Window title"
	)
	return parser.parse_args()


def validate_model_path(model_path: Path) -> None:
	if not model_path.exists():
		raise FileNotFoundError(f"Model file not found: {model_path}")
	suffix = model_path.suffix.lower()
	if suffix not in SUPPORTED_SUFFIXES:
		raise ValueError(
			f"Unsupported model format: {suffix}. Supported formats: {sorted(SUPPORTED_SUFFIXES)}"
		)


def load_mesh(model_path: Path) -> o3d.geometry.TriangleMesh:
	mesh = o3d.io.read_triangle_mesh(str(model_path), enable_post_processing=True)
	if mesh.is_empty():
		raise RuntimeError(f"Failed to load mesh or mesh is empty: {model_path}")

	if not mesh.has_vertex_normals():
		mesh.compute_vertex_normals()

	return mesh


def center_mesh(mesh: o3d.geometry.TriangleMesh) -> o3d.geometry.TriangleMesh:
	centered = o3d.geometry.TriangleMesh(mesh)
	center = centered.get_center()
	centered.translate(-center)
	return centered


def find_start_index(output_dir: Path) -> int:
	max_index = -1
	for p in output_dir.glob("view_*.png"):
		stem = p.stem
		if not stem.startswith("view_"):
			continue
		index_str = stem.replace("view_", "", 1)
		if index_str.isdigit():
			max_index = max(max_index, int(index_str))
	return max_index + 1


def save_view(
	vis: o3d.visualization.VisualizerWithKeyCallback,
	output_dir: Path,
	counter: dict,
) -> bool:
	vis.poll_events()
	vis.update_renderer()

	rgb = np.asarray(vis.capture_screen_float_buffer(do_render=True))
	depth = np.asarray(vis.capture_depth_float_buffer(do_render=True))

	# Depth is 0 for background pixels; convert to uint8 binary mask.
	mask = (depth > 0).astype(np.uint8) * 255

	idx = counter["value"]
	rgb_path = output_dir / f"view_{idx:04d}.png"
	mask_path = output_dir / f"mask_{idx:04d}.png"

	o3d.io.write_image(str(rgb_path), o3d.geometry.Image((rgb * 255.0).astype(np.uint8)))
	o3d.io.write_image(str(mask_path), o3d.geometry.Image(mask))

	print(f"Saved RGB : {rgb_path}")
	print(f"Saved mask: {mask_path}")

	counter["value"] += 1
	return False


def main() -> None:
	args = parse_args()

	model_path = Path(args.model).expanduser().resolve()
	output_dir = Path(args.out).expanduser().resolve()
	output_dir.mkdir(parents=True, exist_ok=True)

	validate_model_path(model_path)
	mesh = center_mesh(load_mesh(model_path))

	vis = o3d.visualization.VisualizerWithKeyCallback()
	vis.create_window(
		window_name=args.window_name,
		width=args.width,
		height=args.height,
		visible=True,
	)

	vis.add_geometry(mesh)
	render_opt = vis.get_render_option()
	render_opt.mesh_show_back_face = True

	view_control = vis.get_view_control()
	view_control.set_lookat([0.0, 0.0, 0.0])

	counter = {"value": find_start_index(output_dir)}

	def on_space_key(v: o3d.visualization.VisualizerWithKeyCallback) -> bool:
		return save_view(v, output_dir, counter)

	vis.register_key_callback(ord(" "), on_space_key)

	print("Controls:")
	print("  Mouse drag (left): rotate around object center (trackball style)")
	print("  Mouse wheel: zoom")
	print("  Mouse drag (right): pan")
	print("  Space: save current RGB and mask")
	print("  Q / Esc: quit")

	vis.run()
	vis.destroy_window()


if __name__ == "__main__":
	main()
