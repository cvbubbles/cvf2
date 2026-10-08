"""Remote image display server for the ``imshow_remote`` C++ client.

The server listens on a TCP port and decodes the netcall objects that clients push (see
``xbase/netcall.py``). Every connected client gets one top-level window, and inside it one floating
sub-window per image name: the sub-window is as large as the image, the image itself cannot be
scrolled or panned, but the sub-window can be dragged with the mouse and may overlap the others.

Mouse:
	drag a sub-window     move it (anywhere on the title bar or on the image)
	wheel over a window   zoom that sub-window (the window follows the zoomed size)

Keys (while a client window has the focus):
	s / S       save the current frame of every sub-window to --save-dir
	r           start/stop recording the sub-window that was clicked last
	R           stop every running recording
	c           clear (close) every sub-window of every client
	u           show again the sub-windows closed with their x button
	q / Esc     quit the server (running recordings are closed)

Recording (--record NAME, --record-format, --record-dir):
	The sub-windows whose name matches a --record argument are recorded as soon as they receive
	frames, on every client. Recording can also be started/stopped interactively with the r key,
	and every recorded sub-window shows a REC marker. The frames are stored by a dedicated thread,
	so a slow disk does not block the client; the frames that do not fit the queue are counted and
	reported when the recording stops. Each recording also writes a .jsonl sidecar with the index,
	file name, receive time, shape, dtype and msg of every frame plus a .meta.json summary (frame
	count, measured fps, dropped frames).

Usage examples:
	python runImshowServer.py
	python runImshowServer.py --port 8100 --colormap turbo --cascade 30
	python runImshowServer.py --record depth --record-format seq --record-dir data/depth  #--record-format=seq/raw/mp4
	python runImshowServer.py --record color --record-format mp4 --record-fps 30
	python runImshowServer.py --selftest            # built-in sender, no C++ client needed
	python runImshowServer.py --save-dir data/remote_imshow

Client side (C++):
	ff::imshow_remote_init("192.168.1.10", 8100);
	ff::imshow_remote("left", img);
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import traceback

import cv2
import numpy as np
import tkinter as tk

if __package__ in (None, ''):
	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbase.netcall import encodeObjs, get_ip_address, recv_all, runServer

DEFAULT_PORT = 8100

COLOR_MAPS = {
	'jet': cv2.COLORMAP_JET,
	'turbo': cv2.COLORMAP_TURBO,
	'viridis': cv2.COLORMAP_VIRIDIS,
	'hot': cv2.COLORMAP_HOT,
	'bone': cv2.COLORMAP_BONE,
}


# ---------------------------------------------------------------- image utils

def normalize_to_uint8(arr, opts):
	"""Map an arbitrary numeric array into the 0..255 range."""
	if opts.range is not None:
		lo, hi = float(opts.range[0]), float(opts.range[1])
		src = np.clip(arr.astype(np.float64), lo, hi)
	else:
		src = arr.astype(np.float64)
		lo = float(np.nanmin(src))
		hi = float(np.nanmax(src))
	if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-12:
		return np.zeros(arr.shape, np.uint8)
	src = (src - lo) * (255.0 / (hi - lo))
	return np.nan_to_num(np.clip(src, 0, 255)).astype(np.uint8)


def to_display_image(img, opts):
	"""Convert a decoded netcall object into a uint8 gray/BGR/BGRA image."""
	arr = np.asarray(img)
	if arr.size == 0:
		return None

	if arr.dtype != np.uint8:
		arr = normalize_to_uint8(arr, opts)

	if arr.ndim == 1:
		disp = arr.reshape(1, -1)
	elif arr.ndim == 2:
		disp = arr
	elif arr.ndim == 3:
		channels = arr.shape[2]
		if channels == 1:
			disp = arr[:, :, 0]
		elif channels in (3, 4):
			disp = arr
		else:
			disp = arr[:, :, 0]
	else:
		# >3 dims: keep the first two axes and flatten the rest
		disp = arr.reshape(arr.shape[0], -1)

	if opts.colormap is not None and disp.ndim == 2:
		disp = cv2.applyColorMap(disp, opts.colormap)
	return disp


# ------------------------------------------------------------------- recorder

REC_FORMATS = ('mp4', 'seq', 'raw')

DEFAULT_RECORD_FPS = 25.0
RECORD_QUEUE_TIMEOUT = 1.0  # seconds a frame may wait for room in a recorder queue
AUTO_FPS_FRAMES = 30        # frames buffered before the frame rate can be measured
AUTO_FPS_SPAN = 0.5         # seconds buffered before the frame rate can be measured
# video backends tried in order, mp4v is the one that is available in most builds
RECORD_CODECS = (('mp4v', '.mp4'), ('avc1', '.mp4'), ('MJPG', '.avi'), ('XVID', '.avi'))

_INVALID_FILENAME = re.compile(r'[^0-9A-Za-z._-]+')


def sanitize_name(name):
	"""Turns a client/sub-window name into something usable inside a file name."""
	return _INVALID_FILENAME.sub('_', str(name)).strip('._') or 'unnamed'


def unique_path(path):
	"""Appends _1, _2, ... to the file name until the path is free."""
	if not os.path.exists(path):
		return path
	stem, ext = os.path.splitext(path)
	i = 1
	while os.path.exists(f'{stem}_{i}{ext}'):
		i += 1
	return f'{stem}_{i}{ext}'


def estimate_fps(items, fallback=DEFAULT_RECORD_FPS):
	"""Frame rate measured on a list of (img, ts, msg) items."""
	span = items[-1][1] - items[0][1] if len(items) > 1 else 0.0
	if span > 1e-6:
		fps = (len(items) - 1) / span
		if 1.0 <= fps <= 240.0:
			return fps
	return fallback


def write_json(path, obj):
	try:
		with open(path, 'w', encoding='utf-8') as f:
			json.dump(obj, f, indent=2, default=str)
	except OSError as e:
		print(f'[record] failed to write {path}: {e}')


class Recorder:
	"""Writes every frame of one (client, name) sub-window to a video or to an image sequence.

	The frames are handed over by the netcall thread and stored by a dedicated thread: writing them
	from the socket thread would block it on a slow disk and back-propagate to the client, which
	would then drop frames of *all* its sub-windows. The queue is bounded, and the frames that do
	not fit are counted instead of being lost silently.

	Formats:
		mp4  the displayed image (colormap applied) as a video
		seq  the displayed image, one png per frame
		raw  the image as received: png for uint8 and for single channel uint16, .npy otherwise
	Each recording also writes a .jsonl sidecar holding, for every frame, its index, the file it is
	stored in, its receive time, shape, dtype and the msg that was pushed with it, plus a
	.meta.json summary when it stops.
	"""

	def __init__(self, opts, client, name, reason):
		self.opts = opts
		self.client = client
		self.name = name
		self.reason = reason  # 'cli' (--record NAME) or 'interactive' (the r key)
		self.format = opts.record_format
		self.path = None
		self.frames = 0
		self.dropped = 0
		self.fps = float(opts.record_fps)
		self.started = time.time()
		self.ended = None
		self._first_ts = None
		self._last_ts = None
		self._queue = queue.Queue(maxsize=max(2, int(opts.record_queue)))
		self._thread = None
		self._writer = None
		self._size = None
		self._channels = None
		self._dtype = None
		self._index = 0
		self._sidecar = None
		self._warn_time = 0.0
		self._stopped = False

	# --------------------------------------------------------------- lifecycle

	def start(self):
		self._thread = threading.Thread(target=self._run, daemon=True,
										name=f'recorder-{self.client}-{self.name}')
		self._thread.start()
		return self

	def submit(self, img, ts, msg=''):
		"""Called from the netcall thread; returns quickly even when the disk cannot keep up."""
		if self._stopped:
			return
		try:
			self._queue.put((img, ts, msg), timeout=RECORD_QUEUE_TIMEOUT)
		except queue.Full:
			self.dropped += 1
			now = time.monotonic()
			if now - self._warn_time > 2.0:
				self._warn_time = now
				print(f'[record] {self.client}/{self.name}: writing is too slow, '
					  f'{self.dropped} frame(s) dropped so far')

	def stop(self):
		"""Drains the queue and closes the file; returns its path (None if nothing was recorded)."""
		self._stopped = True
		# the sentinel is queued after the pending frames, so they are all written before the
		# writer thread exits; retry until there is room for it in the queue
		deadline = time.monotonic() + 30.0
		while time.monotonic() < deadline:
			try:
				self._queue.put(None, timeout=RECORD_QUEUE_TIMEOUT)
				break
			except queue.Full:
				continue
		thread, self._thread = self._thread, None
		if thread is not None:
			thread.join(timeout=30.0)
			if thread.is_alive():
				print(f'[record] {self.client}/{self.name}: the writer thread is still busy, '
					  f'{self.path} may be incomplete')
		return self.path

	# ----------------------------------------------------------------- writing

	def _run(self):
		pending = []
		try:
			while True:
				item = self._queue.get()
				if item is None:
					break
				if self.path is None and self.format == 'mp4' and self.fps <= 0:
					# the video header needs a frame rate: measure it on the first frames
					pending.append(item)
					if len(pending) < AUTO_FPS_FRAMES and (
							len(pending) < 2 or pending[-1][1] - pending[0][1] < AUTO_FPS_SPAN):
						continue
					self.fps = estimate_fps(pending)
					for it in pending:
						self._write(it)
					pending = []
					continue
				self._write(item)
			for it in pending:  # the stream was shorter than the frame rate window
				if self.fps <= 0:
					self.fps = estimate_fps(pending)
				self._write(it)
		except Exception:
			traceback.print_exc()
		finally:
			self._close()

	def _ensure_open(self, img):
		if self.path is not None:
			return
		folder = os.path.join(self.opts.record_dir, sanitize_name(self.client))
		stem = f'{sanitize_name(self.name)}_{time.strftime("%Y%m%d_%H%M%S")}'
		os.makedirs(folder, exist_ok=True)
		if self.format == 'mp4':
			self._open_video(folder, stem, img)
		else:
			self.path = unique_path(os.path.join(folder, stem))
			os.makedirs(self.path, exist_ok=True)
			arr = np.asarray(img)
			self._size = list(arr.shape[:2])
			self._channels = 1 if arr.ndim == 2 else arr.shape[2]
			self._dtype = arr.dtype
		if self.path is None:  # nothing to write yet, retry on the next frame
			return
		self._sidecar = open(unique_path(self._sidecar_path()), 'w', encoding='utf-8')
		what = f'{self.fps:.1f} fps video' if self.format == 'mp4' else f'{self.format} frames'
		print(f'[record] {self.client}/{self.name} ({self.reason}): writing {what} to {self.path}')

	def _open_video(self, folder, stem, img):
		frame = self._video_frame(img)
		if frame is None:
			return
		self._size = frame.shape[:2]
		self._channels = 1 if frame.ndim == 2 else 3
		self._dtype = frame.dtype
		for fourcc, ext in RECORD_CODECS:
			path = unique_path(os.path.join(folder, stem + ext))
			writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), self.fps,
									(self._size[1], self._size[0]), self._channels == 3)
			if writer.isOpened():
				self._writer = writer
				self.path = path
				return
			writer.release()
		raise RuntimeError('no usable video codec found, try --record-format seq')

	def _video_frame(self, img):
		"""uint8 BGR or gray frame, as cv2.VideoWriter expects."""
		frame = to_display_image(img, self.opts)
		if frame is None:
			return None
		if frame.ndim == 3 and frame.shape[2] == 1:
			frame = frame[:, :, 0]
		elif frame.ndim == 3 and frame.shape[2] != 3:
			frame = frame[:, :, :3]  # a video has no alpha channel
		if self._channels == 3 and frame.ndim == 2:
			frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
		elif self._channels == 1 and frame.ndim == 3:
			frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		return np.ascontiguousarray(frame)

	def _write_image(self, img):
		"""Writes one frame file; returns its file name, or None when nothing was written."""
		# 'seq' stores what the window shows, 'raw' stores the data as received
		frame = to_display_image(img, self.opts) if self.format == 'seq' else np.asarray(img)
		if frame is None:
			return None
		if frame.ndim == 3 and frame.shape[2] == 1:
			frame = frame[:, :, 0]  # a single channel image is written as 2d (16 bit png, ...)
		png = self._can_png(frame)
		name = f'{self._index:06d}.' + ('png' if png else 'npy')
		path = os.path.join(self.path, name)
		if png:
			if not cv2.imwrite(path, np.ascontiguousarray(frame)):
				return None
		else:
			np.save(path, frame)
		return name

	@staticmethod
	def _can_png(arr):
		"""True when cv2.imwrite stores the array losslessly as png."""
		if arr.dtype == np.uint8 and arr.ndim in (2, 3):
			channels = 1 if arr.ndim == 2 else arr.shape[2]
			return channels in (1, 3, 4)
		return arr.dtype == np.uint16 and arr.ndim == 2

	def _write(self, item):
		img, ts, msg = item
		self._ensure_open(img)
		if self.path is None:
			return
		if self.format == 'mp4':
			frame = self._video_frame(img)
			if frame is None:
				return
			if frame.shape[:2] != self._size:  # the client changed the resolution
				frame = cv2.resize(frame, (self._size[1], self._size[0]),
								   interpolation=cv2.INTER_AREA)
			self._writer.write(frame)
			name = os.path.basename(self.path)  # the whole recording is one file
		else:
			name = self._write_image(img)
			if name is None:
				return

		arr = np.asarray(img)
		self.frames += 1
		if self._first_ts is None:
			self._first_ts = ts
		self._last_ts = ts
		self.ended = time.time()
		if self._sidecar is not None:
			# utf-8, so that a non-ascii msg stays readable in the file
			self._sidecar.write(json.dumps({
				'i': self._index, 'file': name, 'recv': round(self.ended, 6),
				'shape': list(arr.shape), 'dtype': str(arr.dtype), 'msg': msg},
				ensure_ascii=False) + '\n')
			# flushed per frame: a running recording can be inspected (or killed) at any time
			self._sidecar.flush()
		self._index += 1

	def _sidecar_path(self):
		return (self.path + '.jsonl' if self.format == 'mp4'
				else os.path.join(self.path, 'frames.jsonl'))

	def _meta_path(self):
		return (self.path + '.meta.json' if self.format == 'mp4'
				else os.path.join(self.path, 'meta.json'))

	def _close(self):
		if self._writer is not None:
			try:
				self._writer.release()
			except Exception:
				pass
			self._writer = None
		if self._sidecar is not None:
			self._sidecar.close()
			self._sidecar = None
		self.ended = self.ended or time.time()
		duration = (self._last_ts - self._first_ts) if self._first_ts is not None else 0.0
		measured = (self.frames - 1) / duration if self.frames > 1 and duration > 1e-6 else 0.0
		if self.path is None:
			print(f'[record] {self.client}/{self.name} ({self.reason}): nothing was recorded')
			return
		write_json(unique_path(self._meta_path()), {
			'client': self.client, 'name': self.name, 'format': self.format,
			'reason': self.reason, 'frames': self.frames, 'dropped': self.dropped,
			'video_fps': round(self.fps, 3) if self.format == 'mp4' else None,
			'measured_fps': round(measured, 3), 'duration': round(duration, 3),
			'size': list(self._size) if self._size else None, 'channels': self._channels,
			'dtype': str(self._dtype) if self._dtype else None,
			'colormap': getattr(self.opts, 'colormap_name', None), 'range': self.opts.range,
			'started': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started)),
			'ended': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.ended)),
		})
		dropped = f', {self.dropped} dropped' if self.dropped else ''
		print(f'[record] {self.client}/{self.name} ({self.reason}): {self.frames} frame(s) '
			  f'in {duration:.1f}s ({measured:.1f} fps{dropped}) -> {self.path}')


# ----------------------------------------------------------------- sub-window

TITLE_BG = '#3c3c3c'
TITLE_FG = '#e6e6e6'
IMAGE_BG = '#101010'


class SubWindow:
	"""Floating sub-window holding one image.

	It is created as large as the image itself and its content cannot be scrolled or panned, but
	the whole sub-window can be dragged with the mouse and may overlap the other sub-windows.
	"""

	def __init__(self, client_window, name, opts, pos):
		self.client_window = client_window
		self.canvas = client_window.canvas
		self.opts = opts
		self.name = name
		self.disp = None
		self.zoom = opts.scale if opts.scale > 0 else 1.0
		self.frames = 0
		self.fps = 0.0
		self._t0 = time.monotonic()
		self._photo = None
		self._drag = None
		self._sized = False

		self.frame = tk.Frame(self.canvas, bg=TITLE_BG, bd=1, relief='solid', highlightthickness=0)
		self.header = tk.Frame(self.frame, bg=TITLE_BG)
		self.header.pack(side='top', fill='x')
		self.close_btn = tk.Label(self.header, text='\u2715', bg=TITLE_BG, fg='#c8c8c8',
								  padx=5, cursor='hand2')
		self.close_btn.pack(side='right')
		self.title = tk.Label(self.header, text=name, bg=TITLE_BG, fg=TITLE_FG, anchor='w',
							  padx=6, cursor='fleur')
		self.title.pack(side='left', fill='x', expand=True)
		self.image = tk.Label(self.frame, bg=IMAGE_BG, bd=0, highlightthickness=0, cursor='fleur')
		self.image.pack(side='top')
		self.item = self.canvas.create_window(pos[0], pos[1], window=self.frame, anchor='nw')

		for w in (self.header, self.title, self.image):
			w.bind('<ButtonPress-1>', self._on_press)
			w.bind('<B1-Motion>', self._on_drag)
			w.bind('<ButtonRelease-1>', self._on_release)
			w.bind('<MouseWheel>', self._on_wheel)
		self.close_btn.bind('<Button-1>', self._on_close)

	# ------------------------------------------------------------------- content

	def set_image(self, img):
		disp = to_display_image(img, self.opts)
		if disp is None:
			return
		self.disp = disp
		if not self._sized:
			self._sized = True
			h, w = disp.shape[:2]
			if self.opts.fit_max > 0 and max(w, h) > self.opts.fit_max:
				self.zoom = float(self.opts.fit_max) / max(w, h)

		now = time.monotonic()
		self.frames += 1
		if now - self._t0 >= 0.5:
			self.fps = self.frames / (now - self._t0)
			self.frames = 0
			self._t0 = now
		self._render()

	def _render(self):
		h, w = self.disp.shape[:2]
		out = self.disp
		if self.zoom != 1.0:
			size = (max(1, int(round(w * self.zoom))), max(1, int(round(h * self.zoom))))
			interp = cv2.INTER_AREA if self.zoom < 1.0 else cv2.INTER_NEAREST
			out = cv2.resize(self.disp, size, interpolation=interp)

		ok, buf = cv2.imencode('.png', out)
		if not ok:
			return
		# master= keeps the image in the interpreter of this canvas instead of the default root
		self._photo = tk.PhotoImage(master=self.canvas, data=base64.b64encode(buf.tobytes()))
		self.image.config(image=self._photo)

		self.refresh_title()
		self.client_window.clamp(self)

	def _title_text(self):
		h, w = self.disp.shape[:2]
		channels = 'gray' if self.disp.ndim == 2 else f'{self.disp.shape[2]}ch'
		zoom = f'  x{self.zoom:.2f}' if self.zoom != 1.0 else ''
		rec = ''
		if self.client_window.server.is_recording(self.client_window.client, self.name):
			rec = '  \u25cf REC'
		return f'{self.name}  {w}x{h} {channels}{zoom}  {self.fps:.1f}fps{rec}'

	def refresh_title(self):
		if self.disp is not None:
			self.title.config(text=self._title_text())

	# -------------------------------------------------------- drag / wheel / close

	def _on_press(self, event):
		self.client_window.raise_sub(self)
		x, y = self.client_window.event_pos(event)
		cx, cy = self.canvas.coords(self.item)
		self._drag = (x - cx, y - cy)

	def _on_drag(self, event):
		if self._drag is None:
			return
		x, y = self.client_window.event_pos(event)
		self.client_window.move_sub(self, x - self._drag[0], y - self._drag[1])

	def _on_release(self, event):
		self._drag = None

	def _on_wheel(self, event):
		if not event.delta:
			return
		self.zoom = min(8.0, max(0.05, self.zoom * (1.1 if event.delta > 0 else 1.0 / 1.1)))
		self._render()
		return 'break'

	def _on_close(self, event):
		self.client_window.remove_sub(self.name)
		return 'break'

	# ---------------------------------------------------------------- lifecycle

	def save(self, folder):
		if self.disp is None:
			return None
		os.makedirs(folder, exist_ok=True)
		path = os.path.join(folder, f'{sanitize_name(self.client_window.client)}'
									f'_{sanitize_name(self.name)}.png')
		if not cv2.imwrite(path, self.disp):
			return None
		return path

	def destroy(self):
		self.canvas.delete(self.item)


# -------------------------------------------------------------- client window

class ClientWindow:
	"""Top-level window of one client: a desktop holding the draggable image sub-windows."""

	def __init__(self, server, client, title, opts):
		self.server = server
		self.client = client
		self.title = title or client
		self.opts = opts
		self.subs = {}
		self.hidden = set()  # sub-windows closed locally with the x button
		self.closed = False
		self.last_update = time.monotonic()
		self._spawned = 0

		self.win = tk.Toplevel(server.root)
		self.win.title(f'{self.title} [{self.client}]')
		self.win.geometry(opts.win_size)
		self.win.minsize(240, 160)
		self.win.protocol('WM_DELETE_WINDOW', self.close)

		self.status = tk.Label(self.win, anchor='w', padx=6, bg='#2d2d2d', fg='#d0d0d0')
		self.status.pack(side='bottom', fill='x')
		self.canvas = tk.Canvas(self.win, bg='#202020', highlightthickness=0)
		self.canvas.pack(side='top', fill='both', expand=True)
		self.hint = tk.Label(self.canvas, bg='#202020', fg='#808080',
							 text=f'waiting for images from {self.client}...')
		self.hint.place(relx=0.5, rely=0.5, anchor='center')
		self.canvas.bind('<Configure>', lambda e: self.clamp_all())
		self.update_status()

	# -------------------------------------------------------------------- images

	def add_image(self, name, img):
		self.last_update = time.monotonic()
		if name in self.hidden:
			return
		sub = self.subs.get(name)
		if sub is None:
			sub = SubWindow(self, name, self.opts, self._next_pos())
			self.subs[name] = sub
			self.hint.place_forget()
			print(f'[imshow] {self.client}: new sub-window "{name}"')
		sub.set_image(img)
		self.update_status()

	def remove_sub(self, name):
		sub = self.subs.pop(name, None)
		if sub is None:
			return False
		sub.destroy()
		self.hidden.add(name)
		if not self.subs:
			self.hint.place(relx=0.5, rely=0.5, anchor='center')
		self.server.on_sub_removed(self.client, name)
		self.update_status()
		print(f'[imshow] {self.client}: sub-window "{name}" closed locally'
			  f" (press 'u' to show it again)")
		return True

	def close_sub(self, name):
		sub = self.subs.pop(name, None)
		if sub is None:
			return False
		sub.destroy()
		if not self.subs:
			self.hint.place(relx=0.5, rely=0.5, anchor='center')
		self.server.on_sub_removed(self.client, name)
		self.update_status()
		print(f'[imshow] {self.client}: closed sub-window "{name}"')
		return True

	def close_subs(self):
		names = list(self.subs)
		for sub in list(self.subs.values()):
			sub.destroy()
		self.subs.clear()
		self.hint.place(relx=0.5, rely=0.5, anchor='center')
		for name in names:
			self.server.on_sub_removed(self.client, name)
		self.update_status()

	def show_hidden(self):
		"""Lets the x-ed sub-windows come back with the next frame of their name."""
		n = len(self.hidden)
		self.hidden.clear()
		self.update_status()
		return n

	# --------------------------------------------------------- window geometry

	def _next_pos(self):
		step = max(0, self.opts.cascade)
		w = max(320, self.canvas.winfo_width())
		h = max(240, self.canvas.winfo_height())
		pos = (16 + (self._spawned * step) % max(1, int(w * 0.6)),
			   16 + (self._spawned * step) % max(1, int(h * 0.6)))
		self._spawned += 1
		return pos

	def event_pos(self, event):
		"""Mouse event position in canvas coordinates (works for any inner widget)."""
		return (getattr(event, 'x_root', 0) - self.canvas.winfo_rootx(),
				getattr(event, 'y_root', 0) - self.canvas.winfo_rooty())

	def raise_sub(self, sub):
		self.canvas.tag_raise(sub.item)
		self.canvas.focus_set()
		self.server.active = (self.client, sub.name)  # the r key records this sub-window

	def move_sub(self, sub, x, y):
		self.canvas.coords(sub.item, *self._clamp_pos(sub, x, y))

	def _clamp_pos(self, sub, x, y):
		"""Keeps enough of the sub-window inside the desktop so it can always be grabbed again."""
		cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
		fw = max(sub.frame.winfo_width(), sub.frame.winfo_reqwidth())
		if cw > 1:
			x = max(48 - fw, min(x, cw - 48))
		if ch > 1:
			y = max(0, min(y, ch - 20))
		return (x, y)

	def clamp(self, sub):
		self.move_sub(sub, *self.canvas.coords(sub.item))

	def clamp_all(self):
		for sub in self.subs.values():
			self.clamp(sub)

	# ------------------------------------------------------------- title/status

	def set_title(self, suffix=''):
		idle = time.monotonic() - self.last_update
		self.win.title(f'{self.title} [{self.client}] - {len(self.subs)} image(s)'
					   f'{f" - last frame {idle:.0f}s ago" if idle > 5 else ""}{suffix}')

	def update_status(self):
		parts = [self.client, f'{len(self.subs)} image(s)']
		recording = [name for name in self.subs
					 if self.server.is_recording(self.client, name)]
		if recording:
			parts.append('REC ' + ','.join(sorted(recording)))
		if self.hidden:
			parts.append(f'{len(self.hidden)} hidden')
		idle = time.monotonic() - self.last_update
		if idle > 5:
			parts.append(f'last frame {idle:.0f}s ago')
		self.status.config(text='  |  '.join(parts))

	def close(self):
		if self.closed:
			return
		self.closed = True
		self.win.destroy()
		self.server.on_client_closed(self)


# -------------------------------------------------------------------- server

class ImshowServer:
	"""Receives frames from the netcall threads and paints them in the GUI thread."""

	def __init__(self, opts):
		self.opts = opts
		self.clients = {}
		self.muted = {}
		self._lock = threading.Lock()
		self._pending = {}
		self._commands = []
		self._frames_recv = 0
		self._title_time = time.monotonic()
		self.recorders = {}            # (client, name) -> Recorder
		self.record_names = set(opts.record)
		self.active = None             # (client, name) of the sub-window clicked last
		self._record_dirty = False

		self.root = tk.Tk()
		self.root.title('imshow remote server')
		self.root.withdraw()
		self.root.bind_all('<Key-s>', lambda e: self.save_all())
		self.root.bind_all('<Key-S>', lambda e: self.save_all())
		self.root.bind_all('<Key-c>', lambda e: self.close_all())
		self.root.bind_all('<Key-u>', lambda e: self.unhide_all())
		self.root.bind_all('<Key-r>', lambda e: self.toggle_recording())
		self.root.bind_all('<Key-R>', lambda e: self.stop_all_recording())
		self.root.bind_all('<Key-q>', self._on_quit)
		self.root.bind_all('<Escape>', self._on_quit)

	def _on_quit(self, event=None):
		print('[imshow] quitting')
		self.stop_all_recording()
		self.root.quit()

	# -------------------------------------------------------------- recording

	def is_recording(self, client, name):
		return (client, name) in self.recorders

	def _submit_recording(self, client, name, img, ts, msg):
		"""Called from the netcall thread; hands the frame over to the recorder of that window."""
		rec = self.recorders.get((client, name))
		if rec is None:
			if name not in self.record_names:
				return
			rec = self.start_recording(client, name, 'cli')
		rec.submit(img, ts, msg)

	def start_recording(self, client, name, reason):
		key = (client, name)
		rec = self.recorders.get(key)
		if rec is not None:
			return rec
		rec = Recorder(self.opts, client, name, reason)
		self.recorders[key] = rec
		rec.start()
		self._record_dirty = True
		return rec

	def stop_recording(self, key):
		rec = self.recorders.pop(key, None)
		if rec is None:
			return None
		self._record_dirty = True
		return rec.stop()

	def stop_all_recording(self):
		for key in list(self.recorders):
			self.stop_recording(key)

	def toggle_recording(self):
		"""The r key: start/stop the recording of the sub-window clicked last."""
		key = self.active
		if key is None:
			print("[imshow] 'r': click a sub-window first, then press r to record it")
			return
		if key in self.recorders:
			self.stop_recording(key)
			return
		client, name = key
		if client in self.muted or client not in self.clients:
			print(f'[imshow] {client}/{name}: nothing displayed, cannot record it')
			return
		if name not in self.clients[client].subs:
			print(f'[imshow] {client}/{name}: no such sub-window')
			return
		self.start_recording(client, name, 'interactive')

	def on_sub_removed(self, client, name):
		"""A sub-window disappeared from the desktop: stop the recording the r key started."""
		rec = self.recorders.get((client, name))
		if rec is not None and rec.reason == 'interactive':
			self.stop_recording((client, name))

	# ---------------------------------------------------- netcall (worker) side

	def handle(self, objs):
		"""Called from a netcall connection thread: queue only, never touch the GUI."""
		cmd = str(objs.get('cmd', '') or '')
		client = str(objs.get('client', '') or 'unknown')
		title = str(objs.get('title', '') or '')
		seq = int(objs.get('seq', 0))
		now = time.monotonic()

		if cmd == 'imshow':
			name = str(objs.get('win', '') or '')
			img = objs.get('img')
			msg = objs.get('msg', '')
			if not name:
				return {'ok': np.int32(0), 'msg': 'empty win name'}
			if img is None:
				return {'ok': np.int32(0), 'msg': 'missing img'}
			# record before the display coalescing (and before the mute check), so that a
			# recording keeps every frame the client sent, even while its window is closed
			self._submit_recording(client, name, img, now, '' if msg is None else str(msg))
			with self._lock:
				if client in self.muted:
					return {'ok': np.int32(1), 'seq': np.int32(seq)}
				self._pending[(client, name)] = (title, img, now)
				self._frames_recv += 1
		elif cmd == 'hello':
			with self._lock:
				if self.muted.pop(client, None) is not None:
					print(f'[imshow] {client}: reconnected, unmuted')
				self._commands.append(('hello', client, title, now))
		elif cmd == 'close':
			name = str(objs.get('win', '') or '')
			with self._lock:
				self._commands.append(('close', client, name, now))
		elif cmd == 'closeAll':
			with self._lock:
				self._commands.append(('closeAll', client, '', now))
		else:
			return {'ok': np.int32(0), 'msg': f'unknown cmd: {cmd}'}

		return {'ok': np.int32(1), 'seq': np.int32(seq)}

	# ----------------------------------------------------------- GUI side

	def on_client_closed(self, client_window):
		self.clients.pop(client_window.client, None)
		for key in list(self.recorders):
			rec = self.recorders[key]
			if key[0] == client_window.client and rec.reason == 'interactive':
				self.stop_recording(key)
		with self._lock:
			self.muted[client_window.client] = True
		print(f'[imshow] {client_window.client}: window closed, frames muted'
			  f' (a new "hello" unmutes it)')

	def get_client(self, client, title=''):
		cw = self.clients.get(client)
		if cw is not None and not cw.closed:
			return cw
		cw = ClientWindow(self, client, title, self.opts)
		self.clients[client] = cw
		print(f'[imshow] {client}: opened top-level window "{cw.title}"')
		return cw

	def save_all(self):
		saved = []
		for cw in list(self.clients.values()):
			for sub in list(cw.subs.values()):
				path = sub.save(self.opts.save_dir)
				if path:
					saved.append(path)
		if saved:
			print(f'[imshow] saved {len(saved)} image(s): {", ".join(saved)}')
		else:
			print('[imshow] nothing to save yet')

	def close_all(self):
		for cw in list(self.clients.values()):
			cw.close_subs()
		print('[imshow] cleared all sub-windows')

	def unhide_all(self):
		n = sum(cw.show_hidden() for cw in list(self.clients.values()))
		print(f'[imshow] {n} locally closed sub-window(s) will show again'
			  if n else '[imshow] no hidden sub-window')

	def _apply_commands(self, commands):
		for cmd, client, arg, _ts in commands:
			cw = self.clients.get(client)
			if cmd == 'hello':
				cw = self.get_client(client, arg)
				if arg:
					cw.title = arg
				cw.show_hidden()
				cw.set_title()
			elif cmd == 'close' and cw is not None:
				cw.close_sub(arg)
			elif cmd == 'closeAll' and cw is not None:
				cw.close()

	def _apply_frames(self, pending):
		for (client, name), (title, img, _t) in pending.items():
			if client in self.muted:  # the user closed that window, do not reopen it
				continue
			cw = self.clients.get(client)
			if cw is None or cw.closed:
				cw = self.get_client(client, title)
			cw.add_image(name, img)

	def _pump(self):
		try:
			with self._lock:
				pending = self._pending
				self._pending = {}
				commands = self._commands
				self._commands = []
			# a close picked up in the same batch is newer than the frames queued before it
			for cmd, client, arg, _ts in commands:
				if cmd == 'close':
					pending.pop((client, arg), None)
				elif cmd == 'closeAll':
					for key in [k for k in pending if k[0] == client]:
						pending.pop(key, None)
			try:
				self._apply_commands(commands)
				self._apply_frames(pending)
			except Exception:
				traceback.print_exc()
			now = time.monotonic()
			if self._record_dirty:
				self._record_dirty = False
				for cw in list(self.clients.values()):
					for sub in list(cw.subs.values()):
						sub.refresh_title()
					cw.update_status()
			if now - self._title_time >= 1.0:
				self._title_time = now
				for cw in list(self.clients.values()):
					cw.set_title()
			if self.opts.auto_close_timeout > 0:
				for cw in list(self.clients.values()):
					if now - cw.last_update > self.opts.auto_close_timeout:
						print(f'[imshow] {cw.client}: no frame for '
							  f'{self.opts.auto_close_timeout:.0f}s, closing window')
						cw.close()
		finally:
			self.root.after(self.opts.poll_ms, self._pump)

	def run(self):
		self.root.after(self.opts.poll_ms, self._pump)
		if self.opts.status:
			self.root.after(2000, self._status_tick)
		self.root.mainloop()

	def _status_tick(self):
		try:
			desc = []
			for cw in self.clients.values():
				names = ','.join(
					f'{s.name}:{s.disp.shape if s.disp is not None else "-"}' for s in cw.subs.values())
				desc.append(f'{cw.client}[{names}]')
			print(f'[status] frames={self._frames_recv} clients={len(self.clients)} '
				  f'{" ".join(desc) if desc else "(none)"}')
		finally:
			self.root.after(2000, self._status_tick)


# ----------------------------------------------------------------- selftest

def _send_objs(sock, objs):
	data = encodeObjs(objs)
	sock.sendall(data)
	head = recv_all(sock, 4)
	if head is None:
		raise ConnectionError('server closed the connection')
	size = struct.unpack('<i', head)[0]
	if recv_all(sock, size) is None:
		raise ConnectionError('server closed the connection')


def _selftest_sender(port, stop_event, client='selftest', phase=0.0):
	"""Push synthetic frames so the display can be checked without the C++ client."""
	time.sleep(0.5)
	idx = 0
	while not stop_event.is_set():
		try:
			sock = socket.create_connection(('127.0.0.1', port), timeout=5)
			sock.settimeout(5)
			print(f'[selftest] {client}: connected to 127.0.0.1:{port}')
			_send_objs(sock, {'cmd': 'hello', 'client': client, 'title': f'{client} demo'})
			while not stop_event.is_set():
				t = idx / 30.0 + phase
				bgr = np.zeros((240, 320, 3), np.uint8)
				cv2.circle(bgr, (int(160 + 110 * math.cos(t)), int(120 + 80 * math.sin(t))),
						   28, (0, 200, 255), -1)
				cv2.putText(bgr, f'{client} #{idx}', (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
							0.7, (255, 255, 255), 2)
				depth = (np.tile(np.arange(320, dtype=np.uint16), (240, 1)) * 100).astype(np.uint16)
				f32 = (np.sin(np.linspace(0, 6.28, 320, dtype=np.float32))[None, :]
					   * np.cos(np.linspace(0, 6.28, 240, dtype=np.float32))[:, None]).astype(np.float32)
				gray = np.full((120, 160), int(60 + 40 * math.sin(t)), np.uint8)

				_send_objs(sock, {'cmd': 'imshow', 'client': client, 'win': 'color', 'seq': idx,
								  'msg': f'frame {idx} t={t:.2f}', 'img': bgr})
				_send_objs(sock, {'cmd': 'imshow', 'client': client, 'win': 'depth (u16)', 'seq': idx,
								  'msg': f'depth #{idx}', 'img': depth})
				_send_objs(sock, {'cmd': 'imshow', 'client': client, 'win': 'float', 'seq': idx, 'img': f32})
				_send_objs(sock, {'cmd': 'imshow', 'client': client, 'win': 'jpeg', 'seq': idx, 'img:jpg': bgr})
				if phase == 0.0:
					_send_objs(sock, {'cmd': 'imshow', 'client': client, 'win': 'gray', 'seq': idx, 'img': gray})
				idx += 1
				time.sleep(1.0 / 15)
		except Exception as e:
			if stop_event.is_set():
				break
			print(f'[selftest] {client}: {e} (retrying in 1s)')
			time.sleep(1.0)


# --------------------------------------------------------------------- main

def find_free_port(ip, port):
	bind_ip = '0.0.0.0' if ip in ('', 'localhost', '0.0.0.0') else ip
	while port <= 65535:
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
			try:
				s.bind((bind_ip, port))
				return port
			except OSError:
				print(f'[imshow] port {port} not available, trying {port + 1}')
				port += 1
	raise RuntimeError('no available ports found')


def parse_args(argv=None):
	parser = argparse.ArgumentParser(
		description='Remote imshow display server: one top-level window per client, '
					'one sub-window per image name.')
	parser.add_argument('--ip', default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
	parser.add_argument('--port', type=int, default=DEFAULT_PORT,
						help=f'Bind port (default: {DEFAULT_PORT})')
	parser.add_argument('--win-size', default='1000x700',
						help='Initial size WxH of a client window (default: 1000x700)')
	parser.add_argument('--cascade', type=int, default=28,
						help='Offset in pixels between the sub-windows created one after the other '
							 '(default: 28, 0 stacks them all at the same place)')
	parser.add_argument('--scale', type=float, default=0.0,
						help='Initial zoom of new sub-windows, <=0 means 1:1 (default: 0)')
	parser.add_argument('--fit-max', type=int, default=0,
						help='Scale down images larger than this many pixels on their longer side '
							 '(default: 0, never scale down)')
	parser.add_argument('--poll-ms', type=int, default=10,
						help='GUI refresh interval in milliseconds (default: 10)')
	parser.add_argument('--colormap', choices=sorted(COLOR_MAPS), default=None,
						help='Colormap applied to single-channel images (16-bit/float depth maps)')
	parser.add_argument('--range', type=float, nargs=2, metavar=('LO', 'HI'), default=None,
						help='Fixed intensity range used to map non-uint8 images to 8-bit '
							 '(default: per-frame min/max)')
	parser.add_argument('--save-dir', default='remote_imshow',
						help="Folder used by the 's' key (default: remote_imshow)")
	parser.add_argument('--record', action='append', default=[], metavar='NAME',
						help='Record every sub-window with this name as soon as it gets frames '
							 '(repeatable; see also the r key)')
	parser.add_argument('--record-format', choices=REC_FORMATS, default='mp4',
						help='mp4: one video per sub-window; seq: the displayed image as one png '
							 'per frame; raw: the data as received, lossless (png for uint8 and '
							 'uint16, .npy otherwise). Default: mp4')
	parser.add_argument('--record-dir', default='remote_records',
						help='Folder receiving the recordings (default: remote_records)')
	parser.add_argument('--record-fps', type=float, default=0.0,
						help='Frame rate written in the mp4 header; <=0 measures it on the '
							 'incoming frames (default: 0)')
	parser.add_argument('--record-queue', type=int, default=64,
						help='Frames buffered by a recorder before frames are dropped (default: 64)')
	parser.add_argument('--auto-close-timeout', type=float, default=0.0,
						help='Close a client window after this many seconds without frames '
							 '(default: 0, never close)')
	parser.add_argument('--selftest', action='store_true',
						help='Start a built-in synthetic sender (no C++ client needed)')
	parser.add_argument('--status', action='store_true',
						help='Print a status snapshot to the console every 2 seconds')
	return parser.parse_args(argv)


def main(argv=None):
	opts = parse_args(argv)
	opts.colormap_name = opts.colormap
	opts.colormap = COLOR_MAPS[opts.colormap] if opts.colormap else None

	port = find_free_port(opts.ip, opts.port)
	opts.port = port
	server = ImshowServer(opts)

	display_ip = opts.ip if opts.ip not in ('', '0.0.0.0', 'localhost') else get_ip_address()
	if not display_ip:
		display_ip = '127.0.0.1'
	print(f'[imshow] waiting for clients on {display_ip}:{port}')
	print(f"[imshow] C++: ff::imshow_remote_init(\"{display_ip}\", {port});")

	net_thread = threading.Thread(target=runServer, args=(server.handle, port, opts.ip), daemon=True)
	net_thread.start()

	stop_event = threading.Event()
	senders = []
	if opts.selftest:
		opts.status = True
		senders.append(threading.Thread(target=_selftest_sender,
										args=(port, stop_event, 'selftest', 0.0), daemon=True))
		senders.append(threading.Thread(target=_selftest_sender,
										args=(port, stop_event, 'selftest-2', 1.5), daemon=True))
		for t in senders:
			t.start()

	try:
		server.run()
	finally:
		stop_event.set()
		server.stop_all_recording()  # a video that is not released cannot be opened
		print('[imshow] server stopped')


if __name__ == '__main__':
	main()
