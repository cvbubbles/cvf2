
#ifdef _WIN32
#define WIN32
#endif

#include"rude_socketimpl.cpp"
#include"socket.cpp"
#include"socket_comm.cpp"
#include"socket_comm_normal.cpp"
#include"socket_comm_ssh.cpp"
#include"socket_connect.cpp"
#include"socket_connect_normal.cpp"
#include"socket_connect_proxy.cpp"
#include"socket_connect_socks4.cpp"
#include"socket_connect_socks5.cpp"
#include"socket_connect_tunnel.cpp"
#include"socket_tcpclient.cpp"

#ifdef WIN32
#pragma comment(lib,"ws2_32.lib")

class InitSocket
{
public:
	InitSocket()
	{
		WSADATA wsadata;
		WSAStartup(MAKEWORD(2, 2), &wsadata);
	}
	~InitSocket()
	{
		WSACleanup();
	}
};

InitSocket g_initSocket;
#endif
