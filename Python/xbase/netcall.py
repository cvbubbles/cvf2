#from socketserver import TCPServer, StreamRequestHandler
import socketserver
import socket
import threading
import queue
import time
import numpy as np
import cv2
import struct
from enum import IntEnum

from numpy.core.defchararray import decode

BUFSIZ = 1024

class nct(IntEnum):
    unknown=0
    char=1
    uchar=2
    short=3
    ushort=4
    int32=5
    uint32=6
    int64=7
    uint64=8
    float=9
    double=10
    string=11
    image=12
    file=13
    list=14

nct_traits={
    nct.char:  (np.int8,1,'<b'),
    nct.uchar: (np.uint8,1,'B'),
    nct.short: (np.int16,2,'h'),
    nct.ushort:(np.uint16,2,'H'),
    nct.int32:   (np.int32,4,'<i'),
    nct.uint32:  (np.uint32,4,'<I'),
    nct.int64:   (np.int64,8,'<q'),
    nct.uint64:  (np.uint64,8,'<Q'),
    nct.float: (np.float32,4,'<f'),
    nct.double:(np.float64,8,'<d')
}

def nct_from_dtype(dtype_):
    t=nct.unknown
    for k,v in nct_traits.items():
        if dtype_==v[0]:
            t=k
            break
    return t

'''
def encodeBytesList(bytesList):
    INT_SIZE=4
    totalSize=INT_SIZE*(len(bytesList)+1)
    head=struct.pack('!i',len(bytesList))
    for x in bytesList:
        head+=struct.pack('!i',len(x))
        totalSize+=len(x)
    
    #data=struct.pack('i',totalSize)
    data=head
    for x in bytesList:
        data+=bytes(x)
    return data

def decodeBytesList(data):
    INT_SIZE=4
    bytesList=[]
    data=bytes(data)
    count=struct.unpack('!i', data[0:INT_SIZE])[0]
    sizes=[]
    for i in range(1,count+1):
        t=data[i*INT_SIZE:i*INT_SIZE+INT_SIZE]
        isize=struct.unpack('!i', t)[0]
        sizes.append(isize)
    dpos=INT_SIZE*(count+1)
    for i in range(0,count):
        bytesList.append(data[dpos:dpos+sizes[i]])
        dpos+=sizes[i]
    return bytesList

def _decodeObj(data, typeLabel):
    obj=None
    if typeLabel=='image':
        nparr = np.frombuffer(bytes(data), np.uint8)
        obj = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return obj

def encodeObjs(objs):
    INT_SIZE=4
    subList=[[],[],[]]
    for k,v in objs.items():
        name,typeLabel=getNameType(v, k)
        subList[0].append(name.encode())
        subList[1].append(typeLabel.encode())
        subList[2].append(_encodeObj(v,typeLabel))

    totalSize=0
    for i in range(0,len(subList)):
        subList[i]=encodeBytesList(subList[i])
        totalSize+=len(subList[i])

    totalSize+=INT_SIZE*len(subList)
    data=struct.pack('!i',totalSize)
    for x in subList:
        data+=struct.pack('!i',len(x))
    
    for x in subList:
        data+=x
    return data
        
def decodeObjs(data):
    INT_SIZE=4
    subList=[0,0,0]
    for i in range(0,3):
        subList[i]=struct.unpack('!i', data[INT_SIZE*i:INT_SIZE*(i+1)])[0]
    p=INT_SIZE*len(subList)
    for i in range(0,3):
        isize=subList[i]
        subList[i]=decodeBytesList(data[p:p+isize])
        p+=isize
    objs=dict()
    nobjs=len(subList[0])
    for i in range(0,nobjs):
        name=subList[0][i].decode()
        type_=subList[1][i].decode()
        obj=_decodeObj(subList[2][i], type_)
        objs[name]=obj
    return objs
'''
def _encodeBytesWithSize(b):
    head=struct.pack('<i',len(b))
    return head+bytes(b)

def _decodeBytesWithSize(data, pos):
    size=struct.unpack('<i', data[pos:pos+4])[0]
    end=pos+4+size
    return data[pos+4:end],end

def getNameType(v,name):
    p=name.find(':')
    t=None
    if p<0:
        s=str(type(v))
        beg=s.find('\'')
        end=s.find('\'',beg+1)
        t=(name,s[beg+1:end])
    else:
        t=(name[0:p],name[p+1:])
    return t

def _packShape(shape):
    d=bytes()
    for i in shape:
        d+=struct.pack('<i',i)
    d+=struct.pack('<i',-1)
    return d

def _encodeObj(v,typeLabel):
    rd=None
    t=nct.unknown
    if type(v)==np.ndarray:
        if typeLabel=='jpg' or typeLabel=='png':
            rd=cv2.imencode('.'+typeLabel, v)[1]
            rd=_encodeBytesWithSize(rd)
            t=nct.image
        else:
            rd=_packShape(v.shape)+v.tobytes()
            t=nct_from_dtype(v.dtype)
            assert t!=nct.unknown
    elif type(v)==str:
        if typeLabel=='file':
            rd=_encodeBytesWithSize(v.encode())
            with open(v, 'rb') as file:
                fdata = file.read()
                rd+=_encodeBytesWithSize(fdata)
            t=nct.file
        else:
            rd=_encodeBytesWithSize(v.encode())
            t=nct.string
    elif type(v)==list:
        rd=struct.pack('<i',len(v))
        t=nct.list
        if len(v)>0:
           for x in v:
            xb=_encodeObj(x,typeLabel)
            rd+=xb
    else:
        dt=np.dtype(type(v))
        t=nct_from_dtype(dt)
        assert t!=nct.unknown
        fmt=nct_traits[t][2]
        rd=struct.pack(fmt,v)

    t=struct.pack('<i', t)

    return t+rd

def encodeObjs(objs, addHead=True):
    INT_SIZE=4
    data=bytes()
    for k,v in objs.items():
        name,typeLabel=getNameType(v, k)
        data+=_encodeBytesWithSize(name.encode())
       #data+=_encodeBytes(typeLabel.encode())
        objBytes=_encodeObj(v,typeLabel)
        data+=_encodeBytesWithSize(objBytes)

    head=bytes()
    if addHead:
        totalSize=len(data)
        head=struct.pack('<i',totalSize)

    return head+data
'''
class  BytesObject:
    tConfig={
        'char':('<c',1),
        'int':('<i',4),
        'float':('<f',4),
        'double':('<d',8)
    }

    def __init__(self, data):
        self.data=bytes(data)

    def decode_as(self,typeLabel):
        INT_SIZE=4
        obj=None
        data=self.data
        if typeLabel=='image':
            nparr = np.frombuffer(data, np.uint8)
            obj = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif typeLabel=='str':
            n=struct.unpack_from('<i',data,0)[0]
            assert len(data)==n+INT_SIZE
            obj=data[INT_SIZE:].decode()
        else:
            if typeLabel not in BytesObject.tConfig:
                raise 'unknown type'
            
            cfg=BytesObject.tConfig[typeLabel]
            SIZE=cfg[1]
            fmt=cfg[0]
            data=self.data
            
            if len(data)==SIZE:
                obj=struct.unpack_from(fmt,data,0)[0]
            else:
                n=struct.unpack_from('<i',data,0)[0]
                if len(data)==SIZE*n+INT_SIZE:
                    obj=[]
                    for i in range(0,n):
                        v=struct.unpack_from(fmt,self.data,i*SIZE+INT_SIZE)[0]
                        obj.append(v)
        return obj
'''

class  BytesObject:
    def __init__(self, data):
        self.data=bytes(data)
        
    def _decode_list(self, data, rp):
        INT_SIZE=4

        size=struct.unpack_from('<i',data,rp)[0]
        rp+=INT_SIZE
        objList=[]
        for i in range(0,size):
            obj,rp=self._decode(data, rp)
            objList.append(obj)
        return objList,rp

    def decode(self, typeLabel=None):
        obj,rp=self._decode(self.data,0)
        assert rp==len(self.data)
        return obj

    def _decode(self,data,rp):
        obj=None
        INT_SIZE=4
        typeLabel=struct.unpack_from('<i',data,rp)[0]
        rp+=INT_SIZE
        #data=self.data
        if typeLabel==nct.image:
            #n=struct.unpack_from('<i',data,0)[0]
            #assert len(data)>=n+INT_SIZE
            size=struct.unpack_from('<i',data,rp)[0]
            rp+=INT_SIZE
            nparr = np.frombuffer(data, dtype=np.uint8, offset=rp, count=size)
            rp+=size
            obj = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
            #readSize=n+INT_SIZE
        elif typeLabel==nct.string:
            size=struct.unpack_from('<i',data,rp)[0]
            rp+=INT_SIZE
            obj=data[rp:rp+size].decode()
            rp+=size
            #readSize=n+INT_SIZE
        elif typeLabel==nct.list:
            obj,rp=self._decode_list(data,rp)
        else:
            if typeLabel not in nct_traits:
                raise 'unknown type'
            
            MAX_DIM=4
            dtype_,SIZE,fmt=nct_traits[typeLabel]
            
            # if len(data)==SIZE:
            #     obj=struct.unpack_from(fmt,data,0)[0]
            # else:
            shape=[]
            n=1
            if len(data)>SIZE+INT_SIZE:
                m=0
                for i in range(0,MAX_DIM):
                    m=struct.unpack_from('<i',data,rp+i*INT_SIZE)[0]
                    if m<0:
                        break
                    shape.append(m)
                    n*=m
                assert m<0
                rp+=(len(shape)+1)*INT_SIZE
            else:
                assert len(data)==SIZE+INT_SIZE

            dim=len(shape)
            arr=np.frombuffer(data,offset=rp,dtype=dtype_,count=n)
            if dim==0:
                obj=dtype_(arr[0])
            else:
                obj=np.reshape(arr,tuple(shape))
            rp+=SIZE*n

        return obj,rp


def decodeObjs(data, withHead=True):
    INT_SIZE=4
    p=INT_SIZE if withHead else 0
    dsize=len(data)
    objs=dict()
    while p<dsize:
        objBytes,p=_decodeBytesWithSize(data, p)
        name=objBytes.decode()
        #objBytes,p=_decodeBytes(data, p)
        #type_=objBytes.decode()
        objBytes,p=_decodeBytesWithSize(data, p)
        #obj=_decodeObj(objBytes, type_)
        objs[name]=BytesObject(objBytes).decode()
    return objs


def recv_all(sock, count):
    """纭繚浠巗ocket璇诲彇绮剧‘鐨刢ount瀛楄妭"""
    buf = b''
    while count:
        try:
            newbuf = sock.recv(count)
            if not newbuf: return None
            buf += newbuf
            count -= len(newbuf)
        except Exception as e:
            return None
    return buf

def recvObjs(rq):
    INT_SIZE=4
    # Read Header (Size)
    buf = recv_all(rq, INT_SIZE)
    if buf is None:
        return None
    
    # Parse Size
    totalSize = struct.unpack('<i', buf)[0]
    
    # Validation: sanity check on size
    # Assuming valid packet size is between 0 and 100MB
    if totalSize < 0 or totalSize > 100 * 1024 * 1024: 
        print(f"recvObjs: invalid size header: {totalSize}")
        return None
    
    # Read Body (Exact Size)
    data = recv_all(rq, totalSize)
    if data is None:
        print(f"recvObjs: connection closed while reading body of size {totalSize}")
        return None
        
    return decodeObjs(data, False)

def get_ip_address():
    # 鍒涘缓涓€涓猆DP濂楁帴瀛?
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 杩炴帴鍒颁竴涓叕鍏盌NS鏈嶅姟鍣紝浠ユ鑾峰彇鏈満IP鍦板潃
        s.connect(("8.8.8.8", 80))  # 8.8.8.8鏄胺姝岀殑鍏叡DNS
        ip_address = s.getsockname()[0]
    except Exception as e:
        print(f"鑾峰彇IP鍦板潃鏃跺嚭閿? {e}")
        ip_address = None
    finally:
        s.close()
    return ip_address

def runServer(handleFunc, port=8000, ip='localhost'):
    if ip == 'localhost':
        ip = get_ip_address()

    # Create a lock to serialize handleFunc execution.
    func_lock = threading.Lock()

    class NetcallRequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            print('... connected from {}'.format(self.client_address))

            while True:
                try:
                    objs = recvObjs(self.request)
                    if objs is None:
                        break

                    if 'cmd' in objs and objs['cmd'] == 'exit':
                        print('...disconnet from {}'.format(self.client_address))
                        break

                    with func_lock:
                        retObjs = handleFunc(objs)

                    if 'req_id' in objs:
                        retObjs['req_id'] = objs['req_id']

                    rdata = encodeObjs(retObjs)
                    self.request.send(rdata)
                except Exception as e:
                    print('netcall exception:{}'.format(e.args))
                    dobjs = {'error': np.int32(-1)}
                    if 'objs' in locals() and objs is not None and 'req_id' in objs:
                        dobjs['req_id'] = objs['req_id']
                    rdata = encodeObjs(dobjs)
                    self.request.send(rdata)

    class NetcallTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    # Try to bind server directly; if bind fails, move to next port.
    while True:
        try:
            tcp_server = NetcallTCPServer((ip, port), NetcallRequestHandler)
            break
        except OSError:
            print(f"Server port {port} not available, trying {port+1}")
            port += 1
            if port > 65535:
                raise Exception('No available ports found')

    print('等待客户端连接...({}:{})'.format(ip, port))
    try:
        tcp_server.serve_forever()
    except KeyboardInterrupt:
        tcp_server.server_close()
        print('\nClose')
        exit()

def runServerMultiWorkers(workers, port=8000, ip='localhost'):
    """
    鍚姩澶歐orker鏈嶅姟鍣紙鏂规涓€锛氫换鍔￠槦鍒楁ā寮忥級
    :param workers: list of worker instances (e.g. GpuInferer objects). 
                    Each worker must be callable: result = worker(objs)
    :param port: initial port
    :param ip: bind ip
    """
    if ip == 'localhost':
        ip = get_ip_address()

    # Find available port
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((ip, port))
                s.close()
                break
        except OSError as e:
            print(f"Server port {port} not available, trying {port+1}")
            port += 1
            if port > 65535:
                raise Exception("No available ports found")
    
    address = (ip, port)
    
    # --- Worker Thread Management ---
    task_queue = queue.Queue()

    def _worker_loop(worker_inst):
        while True:
            # Task item structure: (input_objs, result_dict, done_event)
            task_item = task_queue.get()
            if task_item is None: 
                break # Sentinel to stop
            
            objs, result_holder, evt = task_item
            try:
                # Execute the worker logic (e.g., GPU inference)
                # worker_inst is expected to be thread-safe regarding its own resources
                # or exclusive to this thread.
                result_holder['val'] = worker_inst(objs)
            except Exception as e:
                result_holder['exc'] = e
            finally:
                evt.set()       # Notify completion
                task_queue.task_done()

    # Start a thread for each worker instance
    threads = []
    for w in workers:
        t = threading.Thread(target=_worker_loop, args=(w,), daemon=True)
        t.start()
        threads.append(t)
    
    print(f"宸插惎鍔?{len(threads)} 涓悗鍙板伐浣滅嚎绋?")

    class NetcallRequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            print('... connected from {}'.format(self.client_address))
            while True:
                try:
                    objs = recvObjs(self.request) 
                    if objs is None:
                        break

                    if 'cmd' in objs and objs['cmd'] == 'exit':
                        print('...disconnet from {}'.format(self.client_address))
                        break

                    # --- Submit task to Queue ---
                    evt = threading.Event()
                    result_holder = {}
                    
                    # Put task into queue and wait
                    # Queue automatically handles load balancing among consumers
                    task_queue.put((objs, result_holder, evt))
                    
                    # Block until a worker finishes this task
                    evt.wait()
                    
                    # Check for exceptions from worker
                    if 'exc' in result_holder:
                        raise result_holder['exc']
                    
                    retObjs = result_holder['val']
                    if 'req_id' in objs:
                        retObjs['req_id'] = objs['req_id']
                    
                    rdata = encodeObjs(retObjs)
                    self.request.send(rdata) 

                except Exception as e:
                    print('netcall exception:{}'.format(e.args))
                    dobjs = {'error': np.int32(-1), 'msg': str(e)}
                    # Check if 'objs' is bound in local scope before accessing it
                    if 'objs' in locals() and objs is not None and 'req_id' in objs:
                        dobjs['req_id'] = objs['req_id']
                    rdata = encodeObjs(dobjs)
                    self.request.send(rdata)

    # Use ThreadingTCPServer to accept multiple network connections concurrently
    # The actual processing is serialized by the task_queue size (throughput limit)
    # but concurrent network I/O is allowed.
    tcp_server = socketserver.ThreadingTCPServer(address, NetcallRequestHandler)
    
    print('绛夊緟瀹㈡埛绔繛鎺?(Multi-Worker Mode)...({}:{})'.format(ip, port))
    try:
        tcp_server.serve_forever()
    except KeyboardInterrupt:
        tcp_server.server_close()
        print('\nClose')
        exit()


def run_tests_1():
    objs={
        'x':1.0, 
        'y':np.array([1,2,3]),
        'z':['he','she','me']
    }
    
    data=encodeObjs(objs)
    objs=decodeObjs(data)
    print(objs)


def run_tests_2():

    def testHandler(objs):
        print(objs)
        retObjs=dict(objs)
        if 'img' in retObjs:
            retObjs['img:png']=objs['img']
            del retObjs['img']

        return encodeObjs(retObjs)

    runServer(testHandler, 8002)

if __name__ == '__main__':
#   main()
    run_tests_2()

