
#include"appstd.h"
#include"BFC/netcall.h"
#include<fstream>
#include<iostream>
using namespace std;
using namespace ff;

_CMDI_BEG

void on_test_netcall_1()
{
	{
		float f = 1.2f;
		int i = 123;
		std::string str = "hello\n";
		std::vector<int>  vi = { 1,2,3 };
		std::vector<double>  vd = { 4,8,9 };
		std::vector < std::string > vstr= {"a", "bb", "ccc"};
		std::vector<Point2f>  vpoints = { {1,2},{3,4} };
		std::vector<std::vector<Point2f>> vvpoints = { vpoints,vpoints };
		cv::Mat  m=cv::Mat::zeros(100, 100, CV_32FC3);
		cv::Mat img = cv::Mat3b::zeros(100, 100);
		std::vector<Mat>  vmats = { m,m,m };

		ff::NetObjs objs = {
			{ "f",f },
			{ "i", i},
			{"str",str},
			{"vi",vi},
			{"vd",vd},
			{"vstr",vstr},
			{"vpoints",vpoints},
			{"vvpoints",vvpoints},
			{"m",m},
			{"img",nct::Image(img,".png")},
			{"vmats",vmats}
		};

		if (false)
		{
			ff::NetcallServer serv("101.76.200.67", 8002);

			objs = serv.call(objs);

			serv.sendExit();
		}

		CV_Assert(objs["f"].get<float>() == f);
		CV_Assert(objs["i"].get<int>() == i);
		CV_Assert(objs["str"].get<string>() == str);
		CV_Assert(objs["vi"].getv<int>() == vi);
		CV_Assert(objs["vd"].getv<double>() == vd);
		CV_Assert(objs["vstr"].getv<string>() == vstr);
		CV_Assert(objs["vpoints"].getv<Point2f>() == vpoints);
		CV_Assert(objs["vvpoints"].getv<std::vector<Point2f>>() == vvpoints);
		CV_Assert(objs["m"].getm().size() == m.size());
		CV_Assert(objs["img"].getm().size() == img.size());
		CV_Assert(objs["vmats"].getv<Mat>().size() == vmats.size());
	}
}

CMD_BEG()
CMD0("tests.netcall_1", on_test_netcall_1)
CMD_END()



_CMDI_END

