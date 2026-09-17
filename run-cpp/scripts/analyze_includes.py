#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run-cpp skill：从 C/C++ 源码提取外部头文件并映射到库名（仅标准库，正则+映射表）。

输出 JSON：{"headers": [...], "libs": [...], "unknown": [...]}
  headers —— 源码里出现的外部头（已过滤标准库/系统 SDK 自带头）
  libs    —— 按映射表得到的库名（去重排序），用于查缓存/决定下载
  unknown —— 没映射到库的外部头（供人工确认，可能漏表或项目内头）

用法：
  analyze_includes.py --file <路径> [--file <更多路径> ...]
  analyze_includes.py --code "<代码>"
  都不给时读 stdin
"""

import argparse
import json
import re
import sys

# ---------- 标准库头（C/C++ 标准，无需下载） ----------
STD_HEADERS = {
    "algorithm", "any", "array", "atomic", "barrier", "bit", "bitset", "charconv",
    "chrono", "codecvt", "compare", "complex", "concepts", "condition_variable",
    "coroutine", "deque", "exception", "execution", "expected", "filesystem",
    "format", "forward_list", "fstream", "functional", "future", "generator",
    "initializer_list", "iomanip", "ios", "iosfwd", "iostream", "istream",
    "iterator", "latch", "limits", "list", "locale", "map", "memory",
    "memory_resource", "mutex", "new", "numbers", "numeric", "optional", "ostream",
    "print", "queue", "random", "ranges", "ratio", "regex", "scoped_allocator",
    "semaphore", "set", "shared_mutex", "source_location", "span", "spanstream",
    "sstream", "stack", "stacktrace", "stdexcept", "stop_token", "streambuf",
    "string", "string_view", "strstream", "syncstream", "system_error", "thread",
    "tuple", "type_traits", "typeindex", "typeinfo", "unordered_map",
    "unordered_set", "utility", "valarray", "variant", "vector", "version",
    "cassert", "ccomplex", "cctype", "cerrno", "cfenv", "cfloat", "cinttypes",
    "ciso646", "climits", "clocale", "cmath", "csetjmp", "csignal", "cstdalign",
    "cstdarg", "cstdbool", "cstddef", "cstdint", "cstdio", "cstdlib", "cstring",
    "ctime", "cuchar", "cwchar", "cwctype",
    # C 标准
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
    "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
    "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbool.h", "stddef.h",
    "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h", "string.h", "tgmath.h",
    "threads.h", "time.h", "uchar.h", "wchar.h", "wctype.h",
}

# ---------- Windows SDK / 编译器自带头（无需下载） ----------
SDK_HEADERS = {
    "windows.h", "windowsx.h", "winsock2.h", "ws2tcpip.h", "mswsock.h", "ws2def.h",
    "ws2ipdef.h", "winsock.h", "winbase.h", "winuser.h", "wingdi.h", "winreg.h",
    "winsvc.h", "winver.h", "winerror.h", "winnt.h", "wincon.h", "wincrypt.h",
    "winnetwk.h", "winperf.h", "winspool.h", "wintrust.h", "wininet.h", "urlmon.h",
    "d3d9.h", "d3d10.h", "d3d11.h", "d3d12.h", "d3dcompiler.h", "dxgi.h",
    "dxgiformat.h", "dxgitype.h", "directxmath.h", "directxpackedvector.h",
    "directxcolors.h", "xaudio2.h", "xinput.h", "gdiplus.h", "gdiplustypes.h",
    "shellapi.h", "shlobj.h", "shlwapi.h", "commctrl.h", "commdlg.h", "imm.h",
    "mmsystem.h", "mmreg.h", "dsound.h", "ddraw.h", "dinput.h", "objbase.h",
    "ole2.h", "oleauto.h", "olectl.h", "rpc.h", "rpcndr.h", "security.h",
    "secext.h", "tchar.h", "process.h", "conio.h", "io.h", "direct.h", "fcntl.h",
    "sys/stat.h", "sys/types.h", "sys/timeb.h", "sys/locking.h", "sys/utime.h",
    "sys/param.h", "sys/select.h", "sys/ioctl.h", "sys/socket.h", "sys/un.h",
    "netinet/in.h", "netinet/tcp.h", "arpa/inet.h", "vcruntime.h", "corecrt.h",
    "eh.h", "malloc.h", "intrin.h", "sal.h", "specstrings.h", "strsafe.h",
    "intsafe.h", "gl/gl.h", "gl/glu.h", "gl/glext.h", "gl/glcorearb.h",
    "termios.h", "unistd.h", "dirent.h", "pthread.h", "getopt.h",
}

# ---------- 头文件 → 库名映射 ----------
# 1) 精确路径匹配（优先）
EXACT = {
    "curl/curl.h": "curl", "curl/easy.h": "curl",
    "openssl/ssl.h": "openssl", "openssl/evp.h": "openssl",
    "openssl/rsa.h": "openssl", "openssl/sha.h": "openssl",
    "openssl/crypto.h": "openssl", "openssl/aes.h": "openssl",
    "sdl2/sdl.h": "sdl2", "sdl2/sdl_ttf.h": "sdl2-ttf",
    "sdl2/sdl_image.h": "sdl2-image", "sdl2/sdl_mixer.h": "sdl2-mixer",
    "sdl2/sdl_net.h": "sdl2-net",
    "glfw/glfw3.h": "glfw",
    "glm/glm.hpp": "glm",
    "nlohmann/json.hpp": "nlohmann-json",
    "fmt/format.h": "fmt",
    "spdlog/spdlog.h": "spdlog",
    "opencv2/opencv.hpp": "opencv",
    "eigen3/eigen/dense": "eigen", "eigen3/eigen/core": "eigen",
    "zlib.h": "zlib", "bzlib.h": "bzip2", "lzma.h": "xz", "zstd.h": "zstd",
    "lz4.h": "lz4", "snappy.h": "snappy", "png.h": "libpng",
    "jpeglib.h": "libjpeg", "tiffio.h": "libtiff", "webp/decode.h": "libwebp",
    "sqlite3.h": "sqlite3",
    "cuda_runtime.h": "cuda", "cublas_v2.h": "cuda", "cufft.h": "cuda",
    "curand.h": "cuda", "cusolverDn.h": "cuda", "nvrtc.h": "cuda",
    "cudnn.h": "cudnn",
    "zmq.h": "libzmq",
    "hdf5.h": "hdf5", "netcdf.h": "netcdf-c",
    "fftw3.h": "fftw", "fftw3.f": "fftw",
    "gmp.h": "gmp", "gmpxx.h": "gmp", "mpfr.h": "mpfr",
    "lapacke.h": "lapack", "cblas.h": "openblas", "mkl.h": "mkl",
    "tinyxml2.h": "tinyxml2", "pugixml.hpp": "pugixml", "expat.h": "expat",
    "json-c/json.h": "json-c", "jansson.h": "jansson", "cjson/cjson.h": "cjson",
    "yaml-cpp/yaml.h": "yaml-cpp",
    "httplib.h": "cpp-httplib",
    "asio.hpp": "asio",
    "hiredis.h": "hiredis",
    "libpq-fe.h": "libpq",
    "mysql/mysql.h": "libmysqlclient",
    "portaudio.h": "portaudio", "sndfile.h": "libsndfile",
    "miniaudio.h": "miniaudio",
    "al/al.h": "openal-soft", "openal/al.h": "openal-soft",
    "freetype/freetype.h": "freetype", "ft2build.h": "freetype",
    "harfbuzz/hb.h": "harfbuzz",
    "raylib.h": "raylib",
    "irrlicht.h": "irrlicht",
    "freeimage.h": "freeimage",
    "stb_image.h": "stb", "stb_image_write.h": "stb", "stb_truetype.h": "stb",
    "glad/glad.h": "glad",
    "ktx.h": "ktx",
    "sodium.h": "libsodium",
    "botan/botan.h": "botan",
    "mbedtls/ssl.h": "mbedtls",
    "gnutls/gnutls.h": "gnutls",
    "secp256k1.h": "libsecp256k1",
    "qrencode.h": "qrencode",
    "libgit2/git2.h": "libgit2",
    "libssh2/libssh2.h": "libssh2",
    "libusb-1.0/libusb.h": "libusb-1.0",
    "hidapi/hidapi.h": "hidapi",
    "libarchive/archive.h": "libarchive",
    "libzip/zip.h": "libzip",
    "minizip/unzip.h": "minizip",
    "cpr/cpr.h": "cpr",
    "cpprest/http_client.h": "cpprestsdk",
    "pqxx/pqxx": "libpqxx",
    "mongoc/mongoc.h": "libmongoc",
    "bson/bson.h": "libbson",
    "leveldb/db.h": "leveldb",
    "rocksdb/db.h": "rocksdb",
    "lmdb.h": "lmdb",
    "event.h": "libevent", "ev.h": "libev", "uv.h": "libuv",
    "nanomsg/nn.h": "nanomsg",
    "mosquitto.h": "mosquitto",
    "mqttclient.h": "paho-mqtt",
    "rdkafka.h": "librdkafka",
    "srt/srt.h": "srt",
    "mpg123.h": "mpg123",
    "taglib/taglib.h": "taglib",
    "id3tag.h": "libid3tag",
    "mad.h": "libmad",
    "libass/ass.h": "libass",
    "x264.h": "x264", "x265.h": "x265",
    "libavcodec/avcodec.h": "ffmpeg", "libavformat/avformat.h": "ffmpeg",
    "libavutil/avutil.h": "ffmpeg", "libswscale/swscale.h": "ffmpeg",
    "libswresample/swresample.h": "ffmpeg", "libavfilter/avfilter.h": "ffmpeg",
    "libavdevice/avdevice.h": "ffmpeg",
    "openjpeg-2.5/openjpeg.h": "openjpeg",
    "poppler/poppler.h": "poppler",
    "mupdf/fitz.h": "mupdf",
    "pdfium/public/fpdfview.h": "pdfium",
    "tesseract/capi.h": "tesseract",
    "leptonica/allheaders.h": "leptonica",
    "cairo/cairo.h": "cairo",
    "pango/pango.h": "pango",
    "glib.h": "glib", "glib-2.0/glib.h": "glib",
    "gtk/gtk.h": "gtk",
    "wx/wx.h": "wxwidgets",
    "fl/fl.h": "fltk",
    "ncurses.h": "ncurses",
    "readline/readline.h": "readline",
    "libintl.h": "gettext",
    "iconv.h": "libiconv",
    "libconfig.h": "libconfig",
    "inih/ini.h": "inih",
    "toml++/toml.hpp": "tomlplusplus",
    "rapidjson/document.h": "rapidjson",
    "cereal/cereal.hpp": "cereal",
    "msgpack.hpp": "msgpack",
    "flatbuffers/flatbuffers.h": "flatbuffers",
    "protobuf/message.h": "protobuf",
    "grpc/grpc.h": "grpc",
    "thrift/thrift.h": "thrift",
    "xercesc/xercesc.hpp": "xerces-c",
    "libxml/parser.h": "libxml2",
    "xslt/xslt.h": "libxslt",
    "boost/asio.hpp": "boost",
    "websocketpp/websocketpp.hpp": "websocketpp",
    "poco/poco.h": "poco",
    "ace/ace.h": "ace",
    "dlib/dlib.h": "dlib",
    "torch/torch.h": "libtorch",
    "onnxruntime/core/session/onnxruntime_cxx_api.h": "onnxruntime",
    "openvino/openvino.hpp": "openvino",
    "ncnn/net.h": "ncnn",
    "tensorflow/core/public/session.h": "tensorflow",
    "mxnet/c_api.h": "mxnet",
    "caffe/caffe.hpp": "caffe",
    "darknet.h": "darknet",
    "ceres/ceres.h": "ceres",
    "sophus/se3.hpp": "sophus",
    "pcl/point_cloud.h": "pcl",
    "vtk/vtkrenderwindow.h": "vtk",
    "itk/itkimage.h": "itk",
    "flann/flann.hpp": "flann",
    "assimp/assimp.h": "assimp",
    "bullet/btbulletdynamics/btbulletdynamics.h": "bullet3",
    "box2d/box2d.h": "box2d",
    "sfml/graphics.hpp": "sfml", "sfml/audio.hpp": "sfml",
    "allegro5/allegro.h": "allegro",
    "cocos2d.h": "cocos2d",
    "godot_cpp/classes/node.hpp": "godot-cpp",
    "vulkan/vulkan.h": "vulkan",
    "cl/cl.h": "opencl",
    "egl/egl.h": "egl",
    "dxcapi.h": "dxc",
    "spirv/spirv.hpp": "spirv-tools",
    "glslang/glslang/public/glslang/interface.h": "glslang",
    "shaderc/shaderc.hpp": "shaderc",
    "wayland-client.h": "wayland",
    "xcb/xcb.h": "xcb",
    "x11/xlib.h": "x11",
    "asoundlib.h": "alsa-lib",
    "ogg/ogg.h": "libogg",
    "vorbis/vorbisfile.h": "libvorbis",
    "opus/opus.h": "opus",
    "flac/stream_decoder.h": "flac",
    "vpx/vpx_encoder.h": "libvpx",
    "aom/aom_encoder.h": "libaom",
    "mp4v2/mp4v2.h": "mp4v2",
    "matroska/matroska.hpp": "libmatroska",
    "ebml/ebml.h": "libebml",
    "gpac/gpac.h": "gpac",
    "libwmf/wmf.h": "libwmf",
    "gd/gd.h": "libgd",
    "imlib2/imlib2.h": "imlib2",
    "gli/gli.hpp": "gli",
    "apr/apr.h": "apr",
    "apache2/apr.h": "apr",
    "apreq/apreq.h": "apreq",
    "apr-1/apr.h": "apr",
    "apr-util-1/apr.h": "apr-util",
    "zmq.h": "libzmq",
    "librtmp/rtmp.h": "librtmp",
    "libnfs/libnfs.h": "libnfs",
    "curl/curl.h": "curl",
}

# 2) 目录前缀匹配（匹配时按前缀长度降序）
PREFIX = [
    ("boost/", "boost"),
    ("opencv2/", "opencv"),
    ("eigen3/", "eigen"),
    ("eigen/", "eigen"),
    ("nlohmann/", "nlohmann-json"),
    ("fmt/", "fmt"),
    ("spdlog/", "spdlog"),
    ("glm/", "glm"),
    ("sdl2/", "sdl2"),
    ("glfw/", "glfw"),
    ("curl/", "curl"),
    ("openssl/", "openssl"),
    ("torch/", "libtorch"),
    ("onnxruntime/", "onnxruntime"),
    ("openvino/", "openvino"),
    ("ncnn/", "ncnn"),
    ("tensorflow/", "tensorflow"),
    ("tensorrt/", "tensorrt"),
    ("pcl/", "pcl"),
    ("vtk-", "vtk"),
    ("vtk/", "vtk"),
    ("itk-", "itk"),
    ("itk/", "itk"),
    ("flann/", "flann"),
    ("ceres/", "ceres"),
    ("sophus/", "sophus"),
    ("pangolin/", "pangolin"),
    ("assimp/", "assimp"),
    ("bullet/", "bullet3"),
    ("box2d/", "box2d"),
    ("sfml/", "sfml"),
    ("allegro5/", "allegro"),
    ("godot_cpp/", "godot-cpp"),
    ("cocos/", "cocos2d"),
    ("protobuf/", "protobuf"),
    ("grpc/", "grpc"),
    ("flatbuffers/", "flatbuffers"),
    ("cereal/", "cereal"),
    ("rapidjson/", "rapidjson"),
    ("msgpack/", "msgpack"),
    ("yaml-cpp/", "yaml-cpp"),
    ("toml++/", "tomlplusplus"),
    ("tinyxml2/", "tinyxml2"),
    ("xercesc/", "xerces-c"),
    ("libxml2/", "libxml2"),
    ("libxml/", "libxml2"),
    ("xslt/", "libxslt"),
    ("websocketpp/", "websocketpp"),
    ("poco/", "poco"),
    ("ace/", "ace"),
    ("dlib/", "dlib"),
    ("mongoc/", "libmongoc"),
    ("bson/", "libbson"),
    ("leveldb/", "leveldb"),
    ("rocksdb/", "rocksdb"),
    ("redis-plus-plus/", "redis-plus-plus"),
    ("pqxx/", "libpqxx"),
    ("mysql/", "libmysqlclient"),
    ("json-c/", "json-c"),
    ("cjson/", "cjson"),
    ("libevent/", "libevent"),
    ("libarchive/", "libarchive"),
    ("libzip/", "libzip"),
    ("minizip/", "minizip"),
    ("libssh2/", "libssh2"),
    ("libgit2/", "libgit2"),
    ("libusb-1.0/", "libusb-1.0"),
    ("hidapi/", "hidapi"),
    ("freetype2/", "freetype"),
    ("freetype/", "freetype"),
    ("harfbuzz/", "harfbuzz"),
    ("poppler/", "poppler"),
    ("mupdf/", "mupdf"),
    ("pdfium/", "pdfium"),
    ("tesseract/", "tesseract"),
    ("leptonica/", "leptonica"),
    ("cairo/", "cairo"),
    ("pango/", "pango"),
    ("glib-2.0/", "glib"),
    ("glib/", "glib"),
    ("gtk-", "gtk"),
    ("gtk/", "gtk"),
    ("wx/", "wxwidgets"),
    ("fltk/", "fltk"),
    ("fl/", "fltk"),
    ("readline/", "readline"),
    ("libconfig/", "libconfig"),
    ("inih/", "inih"),
    ("libav", "ffmpeg"),
    ("libsw", "ffmpeg"),
    ("openjpeg", "openjpeg"),
    ("x264", "x264"),
    ("x265", "x265"),
    ("libvpx/", "libvpx"),
    ("libaom/", "libaom"),
    ("libogg/", "libogg"),
    ("ogg/", "libogg"),
    ("vorbis/", "libvorbis"),
    ("opus/", "opus"),
    ("flac/", "flac"),
    ("taglib/", "taglib"),
    ("libass/", "libass"),
    ("mp4v2/", "mp4v2"),
    ("libmatroska/", "libmatroska"),
    ("libebml/", "libebml"),
    ("librtmp/", "librtmp"),
    ("libnfs/", "libnfs"),
    ("apr-util", "apr-util"),
    ("apr-", "apr"),
    ("apreq/", "apreq"),
    ("libwmf/", "libwmf"),
    ("libgd/", "libgd"),
    ("gd/", "libgd"),
    ("imlib2/", "imlib2"),
    ("openal/", "openal-soft"),
    ("al/", "openal-soft"),
    ("vulkan/", "vulkan"),
    ("cl/", "opencl"),
    ("egl/", "egl"),
    ("glslang/", "glslang"),
    ("spirv/", "spirv-tools"),
    ("shaderc/", "shaderc"),
    ("wayland/", "wayland"),
    ("xcb/", "xcb"),
    ("x11/", "x11"),
    ("asound/", "alsa-lib"),
    ("alsa/", "alsa-lib"),
    ("nanomsg/", "nanomsg"),
    ("nanomsgxx/", "nanomsgxx"),
    ("srt/", "srt"),
    ("mpg123/", "mpg123"),
    ("id3tag/", "libid3tag"),
    ("mad/", "libmad"),
    ("glad/", "glad"),
    ("gli/", "gli"),
    ("stb/", "stb"),
    ("ktx/", "ktx"),
    ("sodium/", "libsodium"),
    ("botan/", "botan"),
    ("mbedtls/", "mbedtls"),
    ("gnutls/", "gnutls"),
    ("secp256k1/", "libsecp256k1"),
    ("qrencode/", "qrencode"),
    ("zlib/", "zlib"),
    ("bzip2/", "bzip2"),
    ("lz4/", "lz4"),
    ("zstd/", "zstd"),
    ("snappy/", "snappy"),
    ("xz/", "xz"),
    ("fftw/", "fftw"),
    ("gmp/", "gmp"),
    ("mpfr/", "mpfr"),
    ("lapacke/", "lapack"),
    ("openblas/", "openblas"),
    ("mkl/", "mkl"),
    ("cuda/", "cuda"),
    ("cudnn/", "cudnn"),
    ("nvjpeg/", "cuda"),
    ("nppi/", "cuda"),
    ("dnn/", "cuda"),
    ("thrust/", "cuda"),
    ("cub/", "cuda"),
    ("nvidia/", "cuda"),
    ("hdf5/", "hdf5"),
    ("netcdf/", "netcdf-c"),
    ("gsl/", "gsl"),
    ("sqlite3/", "sqlite3"),
    ("libpq/", "libpq"),
    ("zmq/", "libzmq"),
    ("cpprest/", "cpprestsdk"),
    ("cpr/", "cpr"),
    ("paho-mqtt/", "paho-mqtt"),
    ("mosquitto/", "mosquitto"),
    ("rdkafka/", "librdkafka"),
    ("kafka/", "librdkafka"),
    ("libuv/", "libuv"),
    ("libev/", "libev"),
    ("libevent/", "libevent"),
    ("mbedtls/", "mbedtls"),
]

# 3) Qt 头（<QApplication> <QtCore/QString> 等）
QT_RE = re.compile(r"^(?:qt\w*/)?Q[A-Za-z0-9_]+$")


def map_lib(header):
    h = header.strip().replace("\\", "/")
    hl = h.lower()
    if hl in EXACT:
        return EXACT[hl]
    for prefix, lib in sorted(PREFIX, key=lambda kv: -len(kv[0])):
        if hl.startswith(prefix):
            return lib
    if QT_RE.match(h):
        return "qt"
    return None


def extract_headers(code):
    return re.findall(r'#\s*include\s*[<"]([^>"]+)[>"]', code)


def analyze(code):
    seen = []
    for h in extract_headers(code):
        hl = h.strip().replace("\\", "/").lower()
        if not hl:
            continue
        if hl in STD_HEADERS or hl in SDK_HEADERS:
            continue
        if hl.startswith(".") or hl.startswith("/"):
            continue  # 相对/绝对路径头（项目内）
        if hl not in [x.lower() for x in seen]:
            seen.append(h.strip())
    libs = set()
    unknown = []
    for h in seen:
        lib = map_lib(h)
        if lib:
            libs.add(lib)
        else:
            unknown.append(h)
    return sorted(seen), sorted(libs), sorted(set(unknown))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="从 C/C++ 源码提取外部头并映射库名")
    p.add_argument("--file", action="append", help="源码文件路径（可多次）")
    p.add_argument("--code", help="内联代码字符串")
    args = p.parse_args(argv)

    code = ""
    if args.file:
        for f in args.file:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    code += "\n" + fh.read()
            except OSError as exc:
                print("cannot read %s: %s" % (f, exc), file=sys.stderr)
                return 2
    elif args.code:
        code = args.code
    else:
        code = sys.stdin.read()

    headers, libs, unknown = analyze(code)
    json.dump({"headers": headers, "libs": libs, "unknown": unknown},
              sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
