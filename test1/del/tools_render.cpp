
#include"appstd.h"
#include"BFC/bfstream.h"
#include"CVX/bfsio.h"

_CMDI_BEG


void show_models_drag_drop()
{
	printf("Please drag-and-drop 3D model files to the main window.\n");

	Mat3b img = Mat3b::zeros(300, 300);
	auto wnd = imshowx("main", img);
	wnd->setEventHandler([](int code, int param1, int param2, CVEventData data) {
		if (code == cv::EVENT_DRAGDROP)
		{
			auto vfiles = (std::vector<std::string>*)data.ptr;
			for (auto &file : *vfiles)
			{
				CVRModel model(file);

				//model.saveAs(ff::ReplacePathElem(file, "obj", ff::RPE_FILE_EXTENTION));

				mdshow(file, model, Size(500, 500));
			}
		}
	}, "showModels");

	cv::cvxWaitKey();
}

CMD_BEG()
CMD0("tools.render.show_models_drag_drop", show_models_drag_drop)
CMD_END()

void show_model_file()
{
	//std::string file = R"(F:\dev\prj-c1\1100-Re3DX\TestRe3DX\3ds\plane.3ds)";

	//std::string file = R"(F:\SDUicloudCache\re3d\3ds-model\plane\plane.3ds)";
	
	std::string file = R"(F:\T\adizero_F50_TRX_FG_LEA\meshes\model.obj)";

	CVRModel model;

#if 0
	model.load(file, 3, "-extFile -");
	Matx44f m = model.calcStdPose();
	m = m*cvrm::rotate(CV_PI, Vec3f(1, 0, 0));

	model.setTransformation(m);
	std::string xfile = ff::ReplacePathElem(file, "yml", ff::RPE_FILE_EXTENTION);
	{
		cv::FileStorage xfs(xfile, FileStorage::WRITE);
		xfs << "pose0" << cv::Mat(m);
	}
#else
	model.load(file);
#endif

	mdshow("model", model);
	cv::cvxWaitKey();
}
CMD_BEG()
CMD0("tools.render.show_model_file", show_model_file)
CMD_END()


void renderModelToVideo(const std::string &modelFile, const std::string &vidFile, const std::string &imageFile)
{
	CVRModel model(modelFile);
	CVRender render(model);
	Size viewSize(800, 800);
	render.setBgColor(1, 1, 1);

	CVRMats matsInit(model, viewSize);

	cv::VideoWriter vw;
	vw.open(vidFile, cv::VideoWriter::fourcc('M', 'J', 'P', 'G'), 15, viewSize);

	matsInit.mModel = cvrm::rotate(-CV_PI / 2, Vec3f(1, 0, 0));

	float angle = 0;
	float delta = CV_PI * 2 / 100;
	bool isFirst = true;
	while (true)
	{
		angle += delta;

		CVRMats mats(matsInit);

		//rotate the object
		mats.mModel = mats.mModel * cvrm::rotate(angle, Vec3f(0, 1, 0));

		//rotate 90 degrees around y-axis
		//if (true)
		//	mats.mModel = cvrm::rotate(CV_PI / 2, Vec3f(1, 0, 0))*mats.mModel;

		//CVRResult result = render.exec(mats, viewSize, 3, CVRM_DEFAULT&~CVRM_ENABLE_TEXTURE);
		CVRResult result = render.exec(mats, viewSize, CVRM_IMAGE);
		flip(result.img, result.img, -1);

		imshow("img", result.img);
		if (angle>delta + 1e-6f) //ignore the first frame
		{
			if (isFirst && !imageFile.empty())
			{
				imwrite(imageFile, result.img);
				isFirst = false;
			}
			vw.write(result.img);
		}

		waitKey(1);
		//if (waitKey(1) == KEY_ESCAPE)
		if (angle >= CV_PI * 2)
			break;
}
}

void renderDirModelsToVideo(const std::string &dir)
{
	//std::string dataDir = D_DATA + "/re3d/3ds-models/";

	std::vector<std::string> subDirs;
	ff::listSubDirectories(dir, subDirs, false, false);

	for (auto &d : subDirs)
	{
		std::string name = ff::GetFileName(d, false);
		if (ff::IsDirChar(name.back()))
			name.pop_back();

		std::string file = dir + d + "/" + name + ".obj";
		if (ff::pathExist(file))
		{
			printf("%s\n", file.c_str());
			std::string vidFile = dir + d + "/" + name + ".avi";
			std::string imageFile = dir + "/" + name + ".png";
			renderModelToVideo(file, vidFile, imageFile);
		}
		//break;
	}
}

void on_renderToVideo()
{
	//renderDirModelsToVideo(D_DATA + "/re3d/3ds-model/");
	//renderDirModelsToVideo(D_DATA + "/re3d/models-618/");
	renderDirModelsToVideo(D_DATA + "/re3d/test/");
}

CMD_BEG()
CMD("tools.render.render_model_as_video", on_renderToVideo, "render 3d models as video files", "", "")
CMD_END()


void set_model_pose()
{
	std::string dir = R"(F:\SDUicloudCache\re3d\scan\models-618\)";
	std::string ddir = dir + "../models-618-2/";

	//dir = ddir;

	std::vector<string> subDirs;
	ff::listSubDirectories(dir, subDirs);

	//for (auto name : subDirs)
	{
		/*if (name != "bottle4\\")
			continue;*/

		//printf("%s\n", name.c_str());
		//auto modelFile = dir + name + "\\" + ff::GetFileName(name) + ".3ds";

		std::string modelFile = R"(F:\Test1\car\models\car.3ds)";
		std::string name = "car";

		CVRModel model;
		model.load(modelFile,0);
		auto mT=model.calcStdPose();
		model.transform(mT);

		auto center = model.getCenter();
		model.transform(cvrm::translate(-center[0], -center[1], -center[2]));

		auto outDir = ddir + ff::GetFileName(name)+"/";
		if (!ff::pathExist(outDir))
			ff::makeDirectory(outDir);
		auto outFile=outDir+ ff::GetFileName(name) + ".3ds";
		//model.saveAs(outFile);

		//break;
		mdshow("model", model);
		cvxWaitKey();
	}
}

CMD_BEG()
CMD0("tools.render.set_model_pose", set_model_pose)
CMD_END()


struct Pose
{
	Matx33f R;
	Vec3f t;
};
std::vector<Pose>  loadPoses(const std::string &file)
{
	FILE *fp = fopen(file.c_str(), "r");
	if (!fp)
		throw "file open failed";
	std::vector<Pose> vpose;
	int fi;
	float vf[12];
	while (fscanf(fp, "%d%f%f%f%f%f%f%f%f%f%f%f%f", &fi, &vf[0], &vf[1], &vf[2], &vf[3], &vf[4], &vf[5], &vf[6], &vf[7], &vf[8], &vf[9], &vf[10], &vf[11]) == 13)
	{
		Pose pose;
		memcpy(pose.R.val, vf, sizeof(float) * 9);
		memcpy(pose.t.val, &vf[9], sizeof(float) * 3);
		vpose.push_back(pose);
	}
	fclose(fp);
	return vpose;
}

void render_6dpose_results()
{
	std::string droot = "E:/vuforia/";
	auto poses = loadPoses(droot + "/pose.txt");

	CVRModel model(droot + "/model/printer.obj");
	CVRender render(model);

	CVRMats mats;
	mats.mProjection = cvrm::perspective(1300, 1300, 360, 640, Size(720, 1280), 1, 3000);

	for (int fi = 0; fi < 1130; ++fi)
	{
		auto imfile = droot + ff::StrFormat("frames/%d.png", fi);
		Mat img = imread(imfile);
		auto pose = poses[fi];
		mats.mModel = cvrm::fromR33T(pose.R, pose.t);

		render.setBgImage(img);
		auto rr0=render.exec(mats, img.size(), CVRM_IMAGE | CVRM_DEPTH , CVRM_ENABLE_LIGHTING );

		Mat1b mask = rr0.getMaskFromDepth();

		auto rr = render.exec(mats, img.size(), CVRM_IMAGE, CVRM_DEFAULT);

		cv::addWeighted(rr0.img, 0.4, rr.img, 0.6, 0, rr.img);

		/*imshow("rr0", rr0.img);
		imshow("img", img);*/
		imshow("rr", rr.img);

		auto outfile = droot + ff::StrFormat("output/%d.jpg", fi);
		imwrite(outfile, rr.img);

		cv::waitKey(5);
	}
}

CMD_BEG()
CMD0("tools.render.render_6dpose_results", render_6dpose_results)
CMD_END()



void render_build_scene_1()
{
	//Mat img = imread("f:/plane.png");
	Mat img = imread(R"(F:\store\datasets\alphamatting\input_training_highres\gt14.png)");

	float w = float(img.cols) / img.rows;
	Point3f quad[] = {
		Point3f(w,1.f,0),Point3f(-w,1.f,0.f),Point3f(-w,-1.f,0.f),Point3f(w,-1.f,0.f)
	};
	//CVRQuad plane(quad,img);
	CVRModel model;
	//model.addQuad(quad, img, "quad1");
	model.addCuboid("box1", Vec3f(1.1,1.0, 10.9), img);

	Size viewSize(800, 800);

	/*CVRMats mats;
	mats.mView = cvrm::lookat(0, 0, 8, 0, 0, 0, 0, 1, 0);
	mats.mProjection = cvrm::fromK(cvrm::defaultK(viewSize,1.5),viewSize,1,100);

	CVRender render(model);
	auto rr = render.exec(mats, viewSize);
	imshow("rr", rr.img);*/

	mdshow("quad", model, viewSize, CVRM_DEFAULT|CVRM_ENABLE_LIGHTING&~(CVRM_ENABLE_TEXTURE));

	cvxWaitKey();
}

inline char AllowCopyWithMemory(Pose);

void render_build_scene()
{
	Mat img1 = imread("f:/00004.jpg"); 
	Mat img3 = imread(R"(F:\store\datasets\alphamatting\input_training_highres\gt13.png)");
	Mat img2 = imread(R"(F:\store\datasets\alphamatting\input_training_highres\gt19.png)");

	
	CVRModel model;
	//model.addQuad(quad, img, "quad1");
	Vec3f boxSize1 = Vec3f(8.f, 5.f, 8.f)*2000.f;
	model.addCuboid("box1", boxSize1, { img1, img3},"-order yxz -notop -lookInside", cvrm::translate(0,boxSize1[1]/6,0));

	Vec3f boxSize2 = Vec3f(5.f, 2.f, 5.f)*800.f;
	model.addCuboid("box2", boxSize2, { img2 });

	Size viewSize(1280, 720);

	CVRMats mats;
	//mats.mProjection = cvrm::fromK(cvrm::defaultK(viewSize,1.0),viewSize,10,20000);
	mats.mProjection = cvrm::perspective(750, 750, 640, 360, viewSize, 10, 20000);

	CVRender render(model);

	std::vector<Pose>  vposes;

	cv::Point3f viewPoint(0, 4000, 8000);
	int nFrames = 50;

	for (int i = 0; i < nFrames; ++i)
	{
		double theta = i*CV_PI * 2 / nFrames;
		Point3f curViewPoint = viewPoint*cvrm::rotate(theta, Vec3f(0, 1, 0));
		mats.mView= cvrm::lookat(curViewPoint.x,curViewPoint.y,curViewPoint.z, 0, 0, 0, 0, 1, 0);

		Pose pose;
		cvrm::decomposeRT(mats.modelView(), pose.R, pose.t);
		vposes.push_back(pose);

		auto rr = render.exec(mats, viewSize);
		cv::imwrite(ff::StrFormat("e:/syn/%03d.jpg", i + 1), rr.img);

		imshow("rr", rr.img);
		cv::waitKey(0);
	}
	ff::OBFStream os("e:/cam._");
	os << vposes;
}

CMD_BEG()
CMD0("tools.render.build_scene", render_build_scene)
CMD_END()

void getRotation(float *Quaternion, float *rt_mat)
{
	rt_mat[0] = 1 - 2 * (Quaternion[2] * Quaternion[2]) - 2 * (Quaternion[3] * Quaternion[3]);
	rt_mat[1] = 2 * Quaternion[1] * Quaternion[2] - 2 * Quaternion[0] * Quaternion[3];
	rt_mat[2] = 2 * Quaternion[1] * Quaternion[3] + 2 * Quaternion[0] * Quaternion[2];
	rt_mat[3] = 2 * Quaternion[1] * Quaternion[2] + 2 * Quaternion[0] * Quaternion[3];
	rt_mat[4] = 1 - 2 * (Quaternion[1] * Quaternion[1]) - 2 * (Quaternion[3] * Quaternion[3]);
	rt_mat[5] = 2 * Quaternion[2] * Quaternion[3] - 2 * Quaternion[0] * Quaternion[1];
	rt_mat[6] = 2 * Quaternion[1] * Quaternion[3] - 2 * Quaternion[0] * Quaternion[2];
	rt_mat[7] = 2 * Quaternion[2] * Quaternion[3] + 2 * Quaternion[0] * Quaternion[1];
	rt_mat[8] = 1 - 2 * (Quaternion[1] * Quaternion[1]) - 2 * (Quaternion[2] * Quaternion[2]);
}

std::vector<Pose>  loadColmapExports(const std::string &file)
{
	FILE *fp = fopen(file.c_str(), "r");
	if (!fp)
		throw file.c_str();

	int maxCount = 1024 * 1024;
	std::vector<char> _buf(maxCount);
	char *buf = &_buf[0];

	for (int i = 0; i < 4; ++i)
		fgets(buf, maxCount, fp);

	std::vector<Pose> vposes;
	int IMAGE_ID, CAMERA_ID;
	float Q[4], T[3];
	char  name[32];
	while (fgets(buf, maxCount, fp))
	{
		if (sscanf(buf, "%d%f%f%f%f %f%f%f %d%s", &IMAGE_ID, Q, Q + 1, Q + 2, Q + 3, T, T + 1, T + 2, &CAMERA_ID, name) == 10)
		{
			Pose p;
			getRotation(Q, p.R.val);
			p.t = Vec3f(T[0], T[1], T[2]);
			vposes.push_back(p);
		}
		else
			break;
		fgets(buf, maxCount, fp);
	}
	fclose(fp);
	return vposes;
}

void tools_verify_colmap_reconstruction()
{
	std::string estFile = R"(E:\syn\xx\images.txt)";

	std::vector<Pose>  gt, est=loadColmapExports(estFile);
	ff::IBFStream is("e:/cam._");
	is >> gt;

	auto getCamPos = [](const Pose &pose) {
		return -pose.R.t()*pose.t;
	};
	auto getCamDist = [&getCamPos](const Pose &pose1, const Pose &pose2) {
		Vec3f dv = getCamPos(pose1)-getCamPos(pose2);
		return sqrt(dv.dot(dv));
	};

	CV_Assert(gt.size() == est.size());
	float scale = 0;
	for (size_t i = 0; i < gt.size() - 1; ++i)
	{
		float r = getCamDist(gt[i], gt[i + 1]) / getCamDist(est[i], est[i + 1]);
		printf("%f\n", r);
		scale += r;
	}
	scale /= gt.size() - 1;

	for (auto &p : est)
		p.t *= scale;

	//q = Diff*p;
	auto getPoseDiff = [](const Pose &p, const Pose &q) {
		Pose d;
		d.R = q.R*p.R.t();
		d.t = q.t - d.R*p.t;
		return d;
	};

	float err = 0;
	for (size_t i = 1; i < gt.size(); ++i)
	{
		auto gti = getPoseDiff(gt[0], gt[i]);
		auto esti = getPoseDiff(est[0], est[i]);

		float e = getCamDist(gti, esti);
		printf("e=%f\n",e);
		err += e;
	}
	err /= gt.size();
	printf("err=%f\n", err);
}

CMD_BEG()
CMD0("tools.render.verify_colmap_reconstruction", tools_verify_colmap_reconstruction)
CMD_END()


_CMDI_END



