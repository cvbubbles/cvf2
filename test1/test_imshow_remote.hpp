#include"appstd.h"
#include"CVX/imshow_remote.h"
#include<cmath>
#include<iostream>
#include<thread>
using namespace std;
using namespace ff;

_CMDI_BEG

// Pushes synthetic images to Python/runImshowServer.py through cv::imshow_remote().
// Start the server first, then run this command in test1 (see main.cpp):
//     python Python/runImshowServer.py --port 8100
// The target defaults to 127.0.0.1:8100 and can be changed with the IMSHOW_REMOTE_HOST,
// IMSHOW_REMOTE_PORT and IMSHOW_REMOTE_CLIENT environment variables;
// IMSHOW_REMOTE_TEST_SECONDS overrides how long frames are pushed (default 10).
static void on_test_imshow_remote()
{
	cv::RemoteImshowConfig cfg;
	cfg.title = "test_imshow_remote";
	cfg.verbose = true;
	cv::imshow_remote_init(cfg);

	const cv::RemoteImshowConfig &acfg = cv::RemoteImshow::get().config();
	cout << "imshow_remote -> " << acfg.server << ":" << acfg.port
		 << " client=\"" << acfg.client << "\"" << endl;

	const double seconds = std::getenv("IMSHOW_REMOTE_TEST_SECONDS")
							   ? std::atof(std::getenv("IMSHOW_REMOTE_TEST_SECONDS"))
							   : 10.0; // how long frames are pushed
	const int sleepMs = 50;            // ~20 fps

	int frame = 0;
	double beg = ff::elapsed();
	while (ff::elapsed() - beg < seconds)
	{
		const double t = frame * 0.1;

		cv::Mat bgr(240, 320, CV_8UC3, cv::Scalar(24, 24, 24));
		cv::circle(bgr,
				   cv::Point(160 + (int)(110 * std::cos(t)), 120 + (int)(80 * std::sin(t * 1.3))),
				   30, cv::Scalar(0, 200, 255), -1);
		cv::putText(bgr, ff::StrFormat("frame %d", frame), cv::Point(10, 28),
					cv::FONT_HERSHEY_SIMPLEX, 0.7, cv::Scalar(255, 255, 255), 2);
		// the optional msg travels with the frame and lands in the .jsonl of the server recording
		cv::imshow_remote("color", bgr, ff::StrFormat("frame %d at t=%.2f", frame, t));

		// 8-bit gray: sent as jpeg
		cv::Mat gray(120, 160, CV_8UC1);
		for (int y = 0; y < gray.rows; ++y)
			for (int x = 0; x < gray.cols; ++x)
				gray.at<uchar>(y, x) = (uchar)((x * 2 + y + frame * 4) & 255);
		cv::imshow_remote("gray", gray);

		// 8-bit BGRA: sent as png (jpeg has no alpha channel)
		cv::Mat bgra(100, 100, CV_8UC4, cv::Scalar(0, 0, 0, 0));
		for (int y = 0; y < bgra.rows; ++y)
			for (int x = 0; x < bgra.cols; ++x)
				bgra.at<cv::Vec4b>(y, x) = cv::Vec4b(200, (uchar)(x * 2), 100,
													 (uchar)((x + y + frame) & 255));
		cv::imshow_remote("bgra", bgra);

		// 16-bit depth (millimeter): sent as raw Mat, the server normalizes it for display
		cv::Mat depth(180, 240, CV_16UC1);
		for (int y = 0; y < depth.rows; ++y)
			for (int x = 0; x < depth.cols; ++x)
				depth.at<ushort>(y, x) = (ushort)(x * 20 + y * 10 + frame * 30);
		cv::imshow_remote("depth u16", depth);

		// 32-bit float with a range outside 0..1: also raw
		cv::Mat f32(160, 200, CV_32FC1);
		for (int y = 0; y < f32.rows; ++y)
			for (int x = 0; x < f32.cols; ++x)
				f32.at<float>(y, x) = (float)(std::sin(x * 0.05 + t) * std::cos(y * 0.04) + 1.5);
		cv::imshow_remote("float", f32);

		// a bigger jpeg frame now and then, to check the throughput
		if (frame % 10 == 0)
		{
			cv::Mat big(720, 1280, CV_8UC3, cv::Scalar(30, 30, 30));
			cv::circle(big, cv::Point(640 + (int)(500 * std::cos(t * 0.7)), 360 + (int)(250 * std::sin(t * 0.7))),
					   80, cv::Scalar(120, 200, 30), -1);
			cv::imshow_remote("big 1280x720", big);
		}

		++frame;
		if (frame % 20 == 0)
			cout << "imshow_remote: " << frame << " frames pushed" << endl;
		std::this_thread::sleep_for(std::chrono::milliseconds(sleepMs));
	}

	cv::imshow_remote_close("bgra");
	cout << "imshow_remote: asked the server to close sub-window \"bgra\"" << endl;
	std::this_thread::sleep_for(std::chrono::milliseconds(300));

	cout << "imshow_remote: closing the connection" << endl;
	cv::imshow_remote_close();
	cout << "imshow_remote: done, " << frame << " frames pushed" << endl;
}

// ff::exec("tests.imshow_remote")
CMD_BEG()
CMD0("tests.imshow_remote", on_test_imshow_remote)
CMD_END()

_CMDI_END
