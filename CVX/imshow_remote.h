#pragma once

/*!
	Remote imshow client (cv::imshow_remote*): pushes Mat images to Python/runImshowServer.py so that
	images can be inspected on a desktop while the program (e.g. running on an Android device) keeps
	running.

	Server side (start it before the client):
		python Python/runImshowServer.py --port 8100

	Client side:
		cv::imshow_remote_init("192.168.1.10", 8100);   // once, e.g. in main()
		cv::imshow_remote("left", leftImg);             // any thread, returns immediately
		cv::imshow_remote("depth", depthImg16U);        // 16-bit/float Mats are sent lossless
		cv::imshow_remote("left", img, u8"frame 12");   // optional utf-8 text carried with the frame
		cv::imshow_remote_close("left");                // optional, closes the sub-window

	The server can also record a sub-window into a video or an image sequence (see the
	--record/--record-format options and the r key of runImshowServer.py).

	Every client process owns one top-level window on the server, and every distinct name passed to
	imshow_remote() opens a sub-window inside it. The client id defaults to "<hostname>:<program
	name>", so running the same program again reuses the window of the previous run instead of
	opening a new one (the sub-windows are the same, their images simply get updated again). Set
	IMSHOW_REMOTE_CLIENT to give an instance its own window, e.g. when several instances of the same
	program must be watched at the same time.

	Sending is asynchronous: the frame is copied into a pending slot, only the newest frame of each
	sub-window is kept, and the worker thread sends the pending frames one by one and reads the
	server acknowledgement (which also provides the back pressure). When the server is unreachable
	the client silently drops frames and keeps retrying, so image output never blocks the caller or
	breaks the program.

	The default configuration can be overridden by environment variables, which are read when a
	RemoteImshowConfig is constructed: IMSHOW_REMOTE=0 (disable), IMSHOW_REMOTE_HOST,
	IMSHOW_REMOTE_PORT, IMSHOW_REMOTE_QUALITY, IMSHOW_REMOTE_CLIENT.
*/

#include "CVX/def.h"
#include "BFC/def.h"
#include "BFC/netcall.h"
#include "BFC/log.h"
#include "BFC/portable.h"
#include "BFC/stdf.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <exception>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

_CVX_BEG

/*! Name of the running executable: the same client program keeps the same name over its runs. */
inline std::string remoteImshowExeName()
{
	std::string name;
	try
	{
		name = ff::X2MBS(ff::GetFileName(ff::getExePath(), false)); // without the directory and extension
	}
	catch (...)
	{
	}
	return name.empty() ? std::string("program") : name;
}

/*! Name of this machine, used to build the default client id. */
inline std::string remoteImshowHostName()
{
	const char *names[] = { std::getenv("COMPUTERNAME"), std::getenv("HOSTNAME") };
	for (const char *n : names)
		if (n != nullptr && *n)
			return n;
	return "client";
}

/*! Settings of the remote imshow client; the constructor applies the IMSHOW_REMOTE* overrides. */
struct RemoteImshowConfig
{
	std::string server = "127.0.0.1"; //!< server host name or ip
	int port = 8100;                  //!< server port (the default of Python/runImshowServer.py)
	int jpegQuality = 90;             //!< 0..100; a negative value sends 8-bit images as raw Mats
	std::string client;               //!< top-level window id, default "<hostname>:<exe name>"
	std::string title;                //!< top-level window title, default: client
	bool enabled = true;              //!< false: imshow() only drops the frames
	bool verbose = false;             //!< log connects/disconnects and every frame
	int iotimeoutSec = 5;             //!< socket timeout for connect/send/receive
	int reconnectIntervalMs = 1000;   //!< delay between two connection attempts

	RemoteImshowConfig();
};

inline RemoteImshowConfig::RemoteImshowConfig()
{
	const char *v = nullptr;
	if ((v = std::getenv("IMSHOW_REMOTE_HOST")) != nullptr && *v)
		server = v;
	if ((v = std::getenv("IMSHOW_REMOTE_PORT")) != nullptr && *v)
		port = std::atoi(v);
	if ((v = std::getenv("IMSHOW_REMOTE_QUALITY")) != nullptr && *v)
		jpegQuality = std::atoi(v);
	if ((v = std::getenv("IMSHOW_REMOTE_CLIENT")) != nullptr && *v)
		client = v;
	if ((v = std::getenv("IMSHOW_REMOTE")) != nullptr && *v)
		enabled = std::atoi(v) != 0;
}

inline std::string remoteImshowDefaultClientId()
{
	return ff::StrFormat("%s:%s", remoteImshowHostName().c_str(), remoteImshowExeName().c_str());
}

/*!
	Asynchronous image sender. Use the process-wide instance returned by get() (which the
	cv::imshow_remote* helper functions do); the configuration is read-only while running.
*/
class RemoteImshow
{
public:
	//! Process-wide default instance used by the cv::imshow_remote* helper functions.
	static RemoteImshow &get()
	{
		static RemoteImshow inst;
		return inst;
	}

	RemoteImshow() {}
	~RemoteImshow()
	{
		try
		{
			this->close();
		}
		catch (...)
		{
		}
	}

	RemoteImshow(const RemoteImshow &) = delete;
	RemoteImshow &operator=(const RemoteImshow &) = delete;

	//! Starts the worker thread; call again to reconfigure (the previous connection is closed).
	void open(const RemoteImshowConfig &cfg)
	{
		this->close();

		_cfg = cfg;
		if (_cfg.client.empty())
			_cfg.client = remoteImshowDefaultClientId();
		if (_cfg.title.empty())
			_cfg.title = _cfg.client;
		if (!_cfg.enabled)
			return;

		std::lock_guard<std::mutex> lk(_mutex);
		_pending.clear();
		_cmds.clear();
		_stop = false;
		_running = true;
		_thread.reset(new std::thread([this]() { _sendLoop(); }));
	}

	void open(const std::string &server, int port)
	{
		RemoteImshowConfig cfg;
		cfg.server = server;
		cfg.port = port;
		this->open(cfg);
	}

	//! Stops the worker thread and disconnects (an "exit" command is sent best effort).
	void close()
	{
		{
			std::lock_guard<std::mutex> lk(_mutex);
			_stop = true;
			_pending.clear();
			_cmds.clear();
		}
		_cond.notify_all();

		std::unique_ptr<std::thread> th;
		{
			std::lock_guard<std::mutex> lk(_mutex);
			th = std::move(_thread);
		}
		if (th && th->joinable())
		{
			if (th->get_id() == std::this_thread::get_id())
				th->detach(); // close() called from the worker thread itself
			else
				th->join();
		}
		_running = false;
	}

	bool isEnabled() const
	{
		return _enabled && _cfg.enabled;
	}
	void setEnabled(bool b)
	{
		_enabled = b;
	}
	//! Only used before open(); afterwards the worker thread reads the configuration.
	void setJpegQuality(int quality)
	{
		_cfg.jpegQuality = quality;
	}
	const RemoteImshowConfig &config() const
	{
		return _cfg;
	}

	/*!
		Queues \p img for the remote sub-window \p name. The newest frame of a name wins, older
		ones are dropped. \p msg is optional text carried with the frame (the server stores it in
		the .jsonl sidecar of a recording) and must be utf-8. Returns immediately, safe to call
		from any thread.
	*/
	void imshow(const std::string &name, const Mat &img, const std::string &msg = "")
	{
		if (!_running || !_enabled || name.empty() || img.empty())
			return;

		Mat copy = img.clone(); // decouple the frame from the caller's buffer
		{
			std::lock_guard<std::mutex> lk(_mutex);
			if (_stop)
				return;
			_Frame &dst = _pending[name];
			dst.img = std::move(copy);
			dst.msg = msg;
			dst.isClose = false;
			dst.seq = ++_seq;
		}
		_cond.notify_one();
	}

	//! Asks the server to close the sub-window \p name.
	void closeWindow(const std::string &name)
	{
		if (!_running || name.empty())
			return;
		{
			std::lock_guard<std::mutex> lk(_mutex);
			if (_stop)
				return;
			_Frame &dst = _pending[name];
			dst.img.release();
			dst.msg.clear();
			dst.isClose = true;
			dst.seq = ++_seq;
		}
		_cond.notify_one();
	}

	//! Asks the server to close every sub-window of this client.
	void closeAll()
	{
		if (!_running)
			return;
		{
			std::lock_guard<std::mutex> lk(_mutex);
			if (_stop)
				return;
			_pending.clear();
			_cmds.push_back("closeAll");
		}
		_cond.notify_one();
	}

private:
	struct _Frame
	{
		Mat img;
		std::string msg;
		bool isClose = false;
		int64 seq = 0;
	};

	// ------------------------------------------------------------ worker thread

	void _sendLoop()
	{
		std::unique_ptr<rude::Socket> net;
		bool needHello = true;
		while (true)
		{
			std::map<std::string, _Frame> frames;
			std::vector<std::string> cmds;
			{
				std::unique_lock<std::mutex> lk(_mutex);
				_cond.wait(lk, [this]() { return _stop || !_pending.empty() || !_cmds.empty(); });
				if (_stop)
					break;
				frames.swap(_pending);
				cmds.swap(_cmds);
			}

			if (!net)
			{
				net = _connect();
				if (!net)
				{
					_sleepReconnect();
					continue;
				}
				needHello = true;
			}

			bool ok = true;
			try
			{
				if (needHello)
				{
					ok = _call(*net, _makeHello());
					needHello = false;
				}
				for (const std::string &cmd : cmds)
				{
					if (!(ok = _call(*net, _makeCmd(cmd))))
						break;
				}
				if (ok)
				{
					for (auto &v : frames)
					{
						if (!(ok = _call(*net, _makeFrame(v.first, v.second))))
							break;
					}
				}
			}
			catch (const std::exception &e)
			{
				ok = false;
				_logWarn(ff::StrFormat("imshow_remote: send failed: %s", e.what()));
			}
			catch (...)
			{
				ok = false;
			}

			if (!ok)
			{
				net.reset(); // the connection is broken, the unsent frames are stale anyway
				if (!_stop)
				{
					_logWarn(ff::StrFormat("imshow_remote: disconnected from %s:%d",
										   _cfg.server.c_str(), _cfg.port));
					_sleepReconnect();
				}
			}
		}

		if (net)
		{
			try
			{
				_call(*net, _makeCmd("exit"));
			}
			catch (...)
			{
			}
		}
		_logInfo(ff::StrFormat("imshow_remote: stopped (client \"%s\")", _cfg.client.c_str()));
	}

	std::unique_ptr<rude::Socket> _connect()
	{
		std::unique_ptr<rude::Socket> net(new rude::Socket);
		net->setTimeout(_cfg.iotimeoutSec, 0);
		if (!net->connect(_cfg.server.c_str(), _cfg.port))
		{
			const char *err = net->getError();
			_logWarn(ff::StrFormat("imshow_remote: cannot connect to %s:%d (%s)",
								   _cfg.server.c_str(), _cfg.port, err ? err : "unknown error"));
			return nullptr;
		}
		_logInfo(ff::StrFormat("imshow_remote: connected to %s:%d as client \"%s\"",
							   _cfg.server.c_str(), _cfg.port, _cfg.client.c_str()));
		return net;
	}

	void _sleepReconnect()
	{
		std::unique_lock<std::mutex> lk(_mutex);
		_cond.wait_for(lk, std::chrono::milliseconds(std::max(1, _cfg.reconnectIntervalMs)),
					   [this]() { return _stop; });
	}

	// ------------------------------------------------------------------ protocol

	ff::NetObjs _makeHello()
	{
		ff::NetObjs objs;
		objs["cmd"] = ff::ObjStream("hello");
		objs["client"] = ff::ObjStream(_cfg.client);
		objs["title"] = ff::ObjStream(_cfg.title);
		return objs;
	}
	ff::NetObjs _makeCmd(const std::string &cmd)
	{
		ff::NetObjs objs;
		objs["cmd"] = ff::ObjStream(cmd);
		objs["client"] = ff::ObjStream(_cfg.client);
		return objs;
	}
	ff::NetObjs _makeFrame(const std::string &name, const _Frame &frame)
	{
		ff::NetObjs objs;
		objs["client"] = ff::ObjStream(_cfg.client);
		objs["title"] = ff::ObjStream(_cfg.title);
		objs["win"] = ff::ObjStream(name);
		objs["seq"] = ff::ObjStream((int32)frame.seq);
		if (frame.isClose)
		{
			objs["cmd"] = ff::ObjStream("close");
		}
		else
		{
			objs["cmd"] = ff::ObjStream("imshow");
			if (!frame.msg.empty())
				objs["msg"] = ff::ObjStream(frame.msg);
			objs["img"] = _encodeImage(frame.img);
		}
		return objs;
	}

	/*!
		Encodes the frame as jpeg/png (ff::nct::Image) or as a raw Mat. The raw Mat is lossless and is
		used for everything jpeg cannot represent (16-bit, float, more than 3 channels).
	*/
	ff::ObjStream _encodeImage(const Mat &img)
	{
		const int quality = _cfg.jpegQuality;
		const int channels = img.channels();
		if (quality >= 0 && img.depth() == CV_8U && channels >= 1 && channels <= 3)
		{
			std::vector<int> params;
			if (quality > 0 && quality < 100)
			{
				params.push_back(cv::IMWRITE_JPEG_QUALITY);
				params.push_back(quality);
			}
			return ff::ObjStream(ff::nct::Image(img, ".jpg", params));
		}
		if (img.depth() == CV_8U && channels == 4)
			return ff::ObjStream(ff::nct::Image(img, ".png")); // jpeg has no alpha channel
		return ff::ObjStream(img);
	}

	bool _call(rude::Socket &net, const ff::NetObjs &objs)
	{
		std::string data = ff::netcall_encode(objs);

		// rude::Socket sends one byte at a time (select() + send() per byte) as soon as a timeout
		// is configured, which caps the throughput at a few frames per second. Clear the timeout
		// for the payload, then set it back to keep detecting a dead server while reading the
		// acknowledgement.
		net.setTimeout(0, 0);
		bool sent = _sendAll(net, data);
		net.setTimeout(_cfg.iotimeoutSec, 0);
		if (!sent)
			return false;

		std::string reply;
		if (!_recvFrame(net, reply))
			return false;

		if (_cfg.verbose)
			_logInfo(ff::StrFormat("imshow_remote: %d byte(s) sent, %d byte(s) reply",
								   (int)data.size(), (int)reply.size()));
		_checkReply(reply);
		return true;
	}

	void _checkReply(const std::string &reply)
	{
		if (reply.empty())
			return;
		try
		{
			ff::NetObjs ret = ff::netcall_decode(reply, false);
			if (ret.hasKey("ok") && ret["ok"].get<int>() <= 0)
			{
				std::string msg = ret.hasKey("msg")
									  ? ff::StrFormat(": %s", ret["msg"].get<std::string>().c_str())
									  : std::string();
				_logWarn(ff::StrFormat("imshow_remote: server rejected the frame%s", msg.c_str()));
			}
		}
		catch (...)
		{
			// a malformed reply is not fatal, the connection stays usable
		}
	}

	static bool _sendAll(rude::Socket &net, const std::string &data)
	{
		const char *p = data.data();
		int left = (int)data.size();
		while (left > 0)
		{
			int r = net.send(p, left);
			if (r <= 0)
				return false;
			p += r;
			left -= r;
		}
		return true;
	}

	static bool _recvAll(rude::Socket &net, char *buf, int size)
	{
		while (size > 0)
		{
			int r = net.read(buf, size);
			if (r <= 0)
				return false;
			buf += r;
			size -= r;
		}
		return true;
	}

	static bool _recvFrame(rude::Socket &net, std::string &body)
	{
		int32 size = 0;
		if (!_recvAll(net, (char *)&size, sizeof(size)))
			return false;
		if (size < 0 || size > (128 << 20))
			return false;
		body.resize(size);
		return size == 0 || _recvAll(net, &body[0], size);
	}

	// --------------------------------------------------------------------- log

	void _logWarn(const std::string &msg)
	{
		double now = ff::elapsed();
		if (now - _lastWarn < 2.0)
			return;
		_lastWarn = now;
		ff::LOG[CWarning]("%s", msg.c_str());
	}
	void _logInfo(const std::string &msg)
	{
		if (_cfg.verbose)
			ff::LOG[CInfo]("%s", msg.c_str());
	}

private:
	RemoteImshowConfig _cfg;
	std::atomic<bool> _enabled{ true };
	std::atomic<bool> _running{ false };
	bool _stop = false;
	int64 _seq = 0;
	double _lastWarn = -1e9;

	std::mutex _mutex;
	std::condition_variable _cond;
	std::map<std::string, _Frame> _pending; //!< newest frame/close request of every sub-window
	std::vector<std::string> _cmds;         //!< pending client level commands
	std::unique_ptr<std::thread> _thread;
};

// ------------------------------------------------------------------- helpers

/*! Configures the process-wide client and starts its worker thread. */
inline void imshow_remote_init(const RemoteImshowConfig &cfg)
{
	RemoteImshow::get().open(cfg);
}
inline void imshow_remote_init(const std::string &server, int port)
{
	RemoteImshow::get().open(server, port);
}

/*! Writes \p img to the remote sub-window \p name; returns immediately.
	\p msg is optional utf-8 text carried with the frame, e.g. for the recording sidecar. */
inline void imshow_remote(const std::string &name, const Mat &img, const std::string &msg = "")
{
	RemoteImshow::get().imshow(name, img, msg);
}

/*! Asks the server to close the sub-window \p name. */
inline void imshow_remote_close(const std::string &name)
{
	RemoteImshow::get().closeWindow(name);
}

/*! Asks the server to close every sub-window of this client. */
inline void imshow_remote_close_all()
{
	RemoteImshow::get().closeAll();
}

/*! Stops the client: closes the connection and joins the worker thread. */
inline void imshow_remote_close()
{
	RemoteImshow::get().close();
}

_CVX_END
