
#include"appstd.h"
#include"CVRender/cvrender.h"


_CMDI_BEG

std::string dataDir = "./data/";

void renderExamples_load_and_show_model()
{
	mdshow("model1", CVRModel(dataDir + "/3d/box1.3ds"));

	CVRModel model2(dataDir + "/3d/obj_01.ply");
	//CVRModel model2(dataDir + "/3d/box1.3ds");
	cv::Mat bg = imread(dataDir + "/bg/bg1.jpg");
	mdshow("model2", model2, bg.size(), CVRM_DEFAULT&~CVRM_ENABLE_TEXTURE, bg);

	bg = imread(dataDir + "/bg/bg2.jpg");
	mdshow("model3", CVRModel(dataDir + "/3d/box1.3ds"), bg.size(), CVRM_DEFAULT&~CVRM_ENABLE_TEXTURE, bg);

	cvxWaitKey();
}

CMD_BEG()
CMD0("examples.render.load_and_show_model", renderExamples_load_and_show_model)
CMD_END()

void renderExamples_render_to_image()
{
	CVRModel model(dataDir + "/3d/box2.3ds");
	model.setUniformColor(Vec4f(253.f/255, 0, 0, 0));

	CVRender render(model);
	Size viewSize(800, 800);

	CVRMats mats(model, viewSize); //init OpenGL matrixs to show the model in the default view

	//CVRResult result = render.exec(mats, viewSize);
	CVRResult result = render.exec(mats, viewSize, CVRM_IMAGE|CVRM_DEPTH, CVRM_VERTCOLOR);

	imshowx("img", result.img);
	imshow("depth", result.depth);

	CVRProjector prj(result);

	Point3f pt = prj.unproject(400, 400);
	printf("unproject pt=(%.2f,%.2f,%.2f)\n", pt.x, pt.y, pt.z);

	Mat bg = imread(dataDir + "/bg/bg1.jpg");
	render.setBgImage(bg);
	mats = CVRMats(model, bg.size());
	result = render.exec(mats, bg.size());

	imshow("imgInBG", result.img);

	cvxWaitKey();
}

CMD_BEG()
CMD0("examples.render.render_to_image", renderExamples_render_to_image)
CMD_END()

void renderExamples_set_GL_matrix()
{
	CVRModel model(dataDir + "/3d/box2.3ds");
	CVRender render(model);
	Size viewSize(800, 800);

	CVRMats matsInit(model, viewSize);

	float angle = 0;
	float dist = 0, delta = 0.1;
	while (true)
	{
		angle += 0.05;
		dist += delta;
		if (dist < 0 || dist>20)
			delta = -delta;

		CVRMats mats(matsInit);

		//rotate the object
		mats.mModel = mats.mModel * cvrm::rotate(angle, Vec3f(1, 0, 0));

		//move the camera
		mats.mView = mats.mView * cvrm::translate(0, 0, -dist);

		CVRResult result = render.exec(mats, viewSize);
		imshow("img", result.img);

		if (waitKey(30) == KEY_ESCAPE)
			break;
	}
}

CMD_BEG()
CMD0("examples.render.set_GL_matrix", renderExamples_set_GL_matrix)
CMD_END()

void test_view_samples()
{
	int nSamples = 10;

	std::vector<Vec3f> vecs;
	cvrm::sampleSphere(vecs, nSamples);

	std::vector<float> dist;
	for (auto &v : vecs)
	{
		float dm = FLT_MAX;
		for (auto &x : vecs)
		{
			if (&x != &v)
			{
				Vec3f d = x - v;
				float dx = sqrt(d.dot(d));
				if (dx < dm)
					dm = dx;
			}
		}
		dist.push_back(dm);
	}

	dist = dist;
}

void renderExamples_sample_sphere_views()
{
	//test_view_samples();

	int nSamples = 1000;

	std::vector<Vec3f> vecs;
	cvrm::sampleSphere(vecs, nSamples);

	CVRModel model(dataDir + "/3d/car.3ds");
	CVRender render(model);

	Size viewSize(500, 500);
	CVRMats objMats(model, viewSize);

	for (auto v : vecs)
	{
		printf("(%.2f,%.2f,%.2f)\n", v[0], v[1], v[2]);
		//objMats.mModel = cvrm::rotate(v, Vec3f(0, 0, 1));
		auto eyePos = v*4.0f;
		objMats.mView = cvrm::lookat(eyePos[0],eyePos[1],eyePos[2],0,0,0,0,0,1);

		CVRResult res = render.exec(objMats, viewSize);
		imshow("img", res.img);
		cv::waitKey(100);
	}
}

CMD_BEG()
CMD0("examples.render.sample_sphere_views", renderExamples_sample_sphere_views)
CMD_END()

void renderExamples_ortho_projection()
{
	CVRModel model(dataDir + "/3d/car.3ds");
	Size viewSize(800, 800);
	auto wnd = mdshow("perspective", model, viewSize);

	//imshowx("ortho",)
	namedWindow("ortho");

	CVRender render(model);

	wnd->resultFilter = newResultFilter([render, viewSize](CVRResult &rr) mutable {
		CVRMats mats = rr.mats;
		mats.mProjection = cvrm::ortho(-1, 1, -1, 1, 1, 100);
		auto r = render.exec(mats, viewSize);
		imshowx("ortho", r.img);
		//imshowx("ortho", vis(r.getNormalizedDepth()));
	});

	wnd->update();

	cvxWaitKey();
}

CMD_BEG()
CMD0("examples.render.ortho_projection", renderExamples_ortho_projection)
CMD_END()

void renderTest_set_rigid_mats()
{
	CVRModel model(dataDir + "/3d/car.3ds");
	Size viewSize(800, 800);
	auto wnd = mdshow("utilized", model, viewSize);

	namedWindow("rigid");

	CVRender render(model);

	wnd->resultFilter = newResultFilter([render, model, viewSize](CVRResult &rr) mutable {
		CVRMats mats;
		//mats.setModelView(model, 1.2f, 1.5f);
		auto K = cvrm::defaultK(viewSize, 1.2f);
		Rect roi(100, 100, viewSize.width / 4, viewSize.height /2);
		mats.setInROI(model, viewSize, roi, K);

		//decompose rotation from the similarity model-view matrix
		auto m=rr.mats.modelView();
		Matx33f R;
		Vec3f t;
		cvrm::decomposeRT(m, R, t);
		Matx33f T = R*R.t();
		float scale = sqrt((T(0, 0) + T(1, 1) + T(2, 2)) / 3.0f);
		R *= 1.0f / scale;
		mats.mModel = cvrm::fromR33T(R, Vec3f(0, 0, 0));


		mats.mProjection = cvrm::fromK(K,viewSize,1,1000);
		auto r = render.exec(mats, viewSize);

		cv::rectangle(r.img, roi, Scalar(0, 255, 255), 3);

		imshowx("rigid", r.img);
	});

	wnd->update();

	cvxWaitKey();
}

CMD_BEG()
CMD0("test.render.set_rigid_mats", renderTest_set_rigid_mats)
CMD_END()


void renderTest_depth_precision()
{
	Size vsize(800, 800);
	CVRMats mats;
	mats.mModel = cvrm::lookat(0, 0, 5, 0, 0, 0, 0, 1, 0);
	mats.mProjection = cvrm::perspective(vsize.height*1.5, vsize, 1, 1000);

	CVRender render;
	auto rr=render.exec(mats, vsize);

	CVRProjector prj(rr);
	//int x = vsize.width / 2, y = vsize.height / 2;
	int x = 284, y = 284;
	auto p = prj.project(Point3f(0,0,0));
	float z = rr.depth(y, x);
	auto X=prj.unproject(x, y, rr.depth(y, x));

	imshowx("rr", rr.img);
	imshowx("depth", rr.depth);
	cvxWaitKey();
}
CMD_BEG()
CMD0("tests.render.depth_precision", renderTest_depth_precision)
CMD_END()

void renderTest_measure_model()
{
	//CVRModel model("f:/face3d/mesh_sel.obj");
	//CVRModel model("f:/face3d/upload/person.obj");
	//CVRModel model("f:/face3d/test6/mesh_refine_texture.obj");
	//CVRModel model("f:/face3d/test7/mesh_refine_texture.obj");
	CVRModel model(R"(F:\face3d\DECA\2\2.obj)");

	auto vert=model.getVertices();
	Size viewSize(1200, 1200);
	auto wnd = mdshow("utilized", model, viewSize);
	auto *cvw = wnd->wnd;

	std::vector<Point>  points;
	std::vector<Point3f>  points3d;
	cvw->setEventHandler([wnd, &points, &points3d, cvw](int code, int param1, int param2, CVEventData data) {
		if (code == cv::EVENT_RBUTTONDOWN)
		{
			auto &rr = wnd->getCurrentResult();

			int x = param1, y = param2;
			points.push_back(Point(x, y));

			//float scale = 1.f;

			//float scale = 3.f / 0.31f;   //ģ��1
			//float scale = 3.59 / 0.83;   //ģ��2
			//float scale = 4.16 / 0.87;   //ģ��3
			float scale = 4.89 / 0.0515;   


			{
				CVRProjector prj(rr);
				auto q = prj.unproject(float(x), float(y))*scale;
				points3d.push_back(q);
				printf("(%.4f,%.4f,%.4f)\n", q.x, q.y, q.z);
			}

			if (points.size() % 2 == 0)
			{
				auto dp = points3d.back() - points3d[points.size() - 2];
				float len = sqrt(dp.dot(dp));
				printf("line-length : %f \n", len);
			}
			
			Mat dimg = rr.img.clone();
			for (size_t i = 0; i + 1 < points.size(); i+=2)
			{
				cv::line(dimg, points[i], points[i + 1], Scalar(0, 255, 255), 1, CV_AA);
			}
			for (auto &p : points)
				cv::circle(dimg, p, 2, Scalar(0, 255, 255), -1, CV_AA);
			cvw->show(dimg);
		}
		if (code == cv::EVENT_KEYBOARD)
		{
			param1 = toupper(param1);
			if (param1 == 'F')
			{
				points.clear();
				points3d.clear();
				wnd->showCurrentResult();
			}
		}

	}, "measureOp");

	cvxWaitKey();
}
CMD_BEG()
CMD0("tests.render.measure_model", renderTest_measure_model)
CMD_END()


_CMDI_END



