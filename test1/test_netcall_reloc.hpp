
#include"appstd.h"
#include"BFC/netcall.h"
#include<fstream>
#include<iostream>
#include<array>
#include<cmath>
#include<cstdlib>
#include<sstream>
using namespace std;
using namespace ff;

#undef string_t
#include"nlohmann/json.hpp"

_CMDI_BEG



struct MarkerQuad
{
    int marker_id=-1;
    std::array<cv::Point3d,4> pts{};
};

static std::array<double,4> parseCameraParams(const std::string &s)
{
    std::array<double,4> p={0,0,0,0};
    std::stringstream ss(s);
    std::string token;
    for(int i=0; i<4 && std::getline(ss, token, ','); ++i)
        p[i]=std::atof(token.c_str());
    return p;
}

static std::vector<MarkerQuad> loadMarkerQuads(const std::string &markerPoseFile)
{
    std::vector<MarkerQuad> markers;
    std::ifstream ifs(markerPoseFile);
    if(!ifs.is_open())
    {
        cout<<"[warn] failed to open markerPoseFile: "<<markerPoseFile<<endl;
        return markers;
    }

    nlohmann::json jf;
    ifs>>jf;
    if(!jf.contains("markers") || !jf["markers"].is_array()) return markers;

    for(const auto &jm : jf["markers"])
    {
        MarkerQuad mq;
        mq.marker_id=jm.value("marker_id", -1);

        std::array<bool,4> hasPt={false,false,false,false};
        if(jm.contains("corners") && jm["corners"].is_array())
        {
            for(const auto &jc : jm["corners"])
            {
                int idx=jc.value("corner_index", -1);
                if(idx<0 || idx>=4) continue;
                if(!jc.contains("world_xyz") || !jc["world_xyz"].is_array() || jc["world_xyz"].size()!=3) continue;
                const auto &xyz=jc["world_xyz"];
                mq.pts[idx]=cv::Point3d(
                    xyz.at(0).get<double>(),
                    xyz.at(1).get<double>(),
                    xyz.at(2).get<double>());
                hasPt[idx]=true;
            }
        }
        if(hasPt[0] && hasPt[1] && hasPt[2] && hasPt[3])
            markers.push_back(mq);
    }
    return markers;
}

static void drawMarkerPoseOverlay(cv::Mat &vis, const cv::Mat &poseW2C, const std::vector<MarkerQuad> &markers,
                                  double fx, double fy, double cx, double cy)
{
    if(poseW2C.rows!=3 || poseW2C.cols!=4)
    {
        cout<<"[warn] pose shape is not 3x4, skip marker projection."<<endl;
        return;
    }

    cv::Mat pose64;
    poseW2C.convertTo(pose64, CV_64F);
    cv::Mat Rwc=pose64(cv::Range(0,3), cv::Range(0,3)).clone();
    cv::Mat twc=pose64(cv::Range(0,3), cv::Range(3,4)).clone();

    auto toVec3 = [](const cv::Point3d &p)->cv::Vec3d { return cv::Vec3d(p.x, p.y, p.z); };
    auto toPoint3 = [](const cv::Vec3d &v)->cv::Point3d { return cv::Point3d(v[0], v[1], v[2]); };
    auto projectPoint = [&](const cv::Point3d &pw, cv::Point &pi)->bool {
        cv::Mat Xw=(cv::Mat_<double>(3,1)<<pw.x,pw.y,pw.z);
        cv::Mat Xc=Rwc*Xw+twc;
        double z=Xc.at<double>(2,0);
        if(z<=1e-8) return false;
        double u=fx*Xc.at<double>(0,0)/z+cx;
        double v=fy*Xc.at<double>(1,0)/z+cy;
        pi=cv::Point((int)std::round(u), (int)std::round(v));
        return true;
    };

    for(auto &m : markers)
    {
        std::array<cv::Vec3d,4> base3d;
        for(int i=0; i<4; ++i)
            base3d[i]=toVec3(m.pts[i]);

        cv::Vec3d e01=base3d[1]-base3d[0];
        cv::Vec3d e03=base3d[3]-base3d[0];
        cv::Vec3d n=e01.cross(e03);
        double nlen=cv::norm(n);
        if(nlen<=1e-12) continue;
        n/=nlen;

        double sideA=cv::norm(e01);
        double sideB=cv::norm(e03);
        double h=0.6*std::min(sideA, sideB);
        if(h<=1e-8) continue;

        cv::Vec3d center=(base3d[0]+base3d[1]+base3d[2]+base3d[3])*0.25;
        cv::Mat C0=(cv::Mat_<double>(3,1)<<center[0],center[1],center[2]);
        cv::Mat C1=(cv::Mat_<double>(3,1)<<center[0]+n[0]*h,center[1]+n[1]*h,center[2]+n[2]*h);
        double z0=cv::Mat(Rwc*C0+twc).at<double>(2,0);
        double z1=cv::Mat(Rwc*C1+twc).at<double>(2,0);
        if(z1>z0) n=-n; // 挤出方向尽量朝向相机，便于观察

        std::array<cv::Point3d,8> cube3d;
        for(int i=0; i<4; ++i)
        {
            cube3d[i]=toPoint3(base3d[i]);
            cube3d[i+4]=toPoint3(base3d[i]+n*h);
        }

        std::array<cv::Point,8> cube2d;
        bool ok=true;
        for(int i=0; i<8; ++i)
        {
            if(!projectPoint(cube3d[i], cube2d[i])) { ok=false; break; }
        }
        if(!ok) continue;

        std::vector<cv::Point> basePoly={cube2d[0],cube2d[1],cube2d[2],cube2d[3]};
        std::vector<cv::Point> topPoly ={cube2d[4],cube2d[5],cube2d[6],cube2d[7]};
        cv::polylines(vis, basePoly, true, cv::Scalar(0,255,0), 2, cv::LINE_AA);
        cv::polylines(vis, topPoly,  true, cv::Scalar(0,200,255), 2, cv::LINE_AA);
        for(int i=0; i<4; ++i)
            cv::line(vis, cube2d[i], cube2d[i+4], cv::Scalar(255,120,0), 2, cv::LINE_AA);
        for(auto &p : cube2d) cv::circle(vis, p, 3, cv::Scalar(0,0,255), -1, cv::LINE_AA);

        cv::putText(vis, std::string("id:")+std::to_string(m.marker_id), cube2d[0]+cv::Point(6,-6),
                    cv::FONT_HERSHEY_SIMPLEX, 0.55, cv::Scalar(255,255,0), 1, cv::LINE_AA);
    }
}

#define TEST_NETCALL_RELOC_USE_CAMERA 1
#define TEST_NETCALL_RELOC_CAMERA_ID 1

void test_netcall_reloc()
{
	{
		std::string imgDir=R"(.\data\office3\color\)";
        std::vector<String> imgFiles;
#if !TEST_NETCALL_RELOC_USE_CAMERA
        cv::glob(imgDir+"*.jpg", imgFiles);
        std::sort(imgFiles.begin(), imgFiles.end());
#endif
        
        bool showMarkerPose=true;
        std::string markerPoseFile=imgDir+"/../markers.json";
        std::string cameraParams="644.565674,643.857605,657.600647,411.930359";
        auto k=parseCameraParams(cameraParams);
        double fx=k[0], fy=k[1], cx=k[2], cy=k[3];
        std::vector<MarkerQuad> markers = showMarkerPose ? loadMarkerQuads(markerPoseFile) : std::vector<MarkerQuad>();
        if(showMarkerPose) cout<<"loaded markers: "<<markers.size()<<" from "<<markerPoseFile<<endl;

		ff::NetcallServer serv("10.102.33.100", 8000);

        cv::VideoCapture cap;
#if TEST_NETCALL_RELOC_USE_CAMERA
        cap.open(TEST_NETCALL_RELOC_CAMERA_ID);
        if(!cap.isOpened())
        {
            cout<<"[err] failed to open camera: "<<TEST_NETCALL_RELOC_CAMERA_ID<<endl;
            serv.sendExit();
            return;
        }
        cap.set(cv::CAP_PROP_FRAME_WIDTH, 1280);
        cap.set(cv::CAP_PROP_FRAME_HEIGHT, 800);
        cout<<"camera opened, actual size: "
            <<(int)cap.get(cv::CAP_PROP_FRAME_WIDTH)<<"x"
            <<(int)cap.get(cv::CAP_PROP_FRAME_HEIGHT)<<endl;
#endif

        size_t fileIdx=0;
        while(true)
        {
            cv::Mat image;
            std::string frameTag;
#if TEST_NETCALL_RELOC_USE_CAMERA
            cap>>image;
            frameTag="camera";
#else
            if(fileIdx>=imgFiles.size()) break;
            frameTag=imgFiles[fileIdx];
            image=cv::imread(imgFiles[fileIdx]);
            ++fileIdx;
#endif
            if(image.empty()) continue;

            ff::NetObjs objs = {
                {"image",ff::nct::Image(image,".jpg")},
                {"camera_model","PINHOLE"}, 
                {"camera_params",cameraParams}
            };
            ff::NetObjs ret = serv.call(objs);
            cv::Mat poseW2C=ret["pose"].getm();
            int num_inliers=ret["num_inliers"].get<int>();
            float inlier_ratio=ret["inlier_ratio"].get<float>();
            cout<<frameTag<<" "<<num_inliers<<" "<<inlier_ratio<<endl;
            cout<<"pose: "<<poseW2C<<endl;

            if(showMarkerPose && !markers.empty())
            {
                if(ret.hasKey("retvis"))
                {
                    cv::Mat vmatch=ret["retvis"].getm();
                    cv::imshow("reloc_vmatch", vmatch);
                }

                cv::Mat vis=image.clone();
                drawMarkerPoseOverlay(vis, poseW2C, markers, fx, fy, cx, cy);
                cv::imshow("reloc_marker_pose", vis);
                if('q'==cv::waitKey(50))
                    break;
            }
            else if(TEST_NETCALL_RELOC_USE_CAMERA)
            {
                if('q'==cv::waitKey(1))
                    break;
            }
        }

		serv.sendExit();
    }
}

CMD_BEG()
CMD0("tests.netcall_reloc", test_netcall_reloc)
CMD_END()



_CMDI_END
