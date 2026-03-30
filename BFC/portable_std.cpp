#include "BFC/portable.h"
#include <vector>
#include <string>
#include <cstring>
#include <cstdlib>

// Filesystem detection
#if defined(__cpp_lib_filesystem) || (defined(__cplusplus) && __cplusplus >= 201703L)
    #include <filesystem>
    namespace fs = std::filesystem;
#elif defined(__cpp_lib_experimental_filesystem) || (defined(__cplusplus) && __cplusplus >= 201402L)
    #include <experimental/filesystem>
    namespace fs = std::experimental::filesystem;
#else
    #error "C++17 filesystem or experimental filesystem required"
#endif

#ifndef _WIN32
#include <limits.h>
#include <unistd.h>
#endif

_FF_BEG

_BFC_API std::string& WCS2MBS(const wchar_t *wcs, std::string &mbs, int code_page)
{
    // Minimal implementation: assume default locale/UTF-8
    // For robust cross-platform WCS<->MBS, explicit iconv or similar is often needed
    // if code_page implies specifically Windows ANSI codepages. 
    // Here we just use std::wcstombs
    if(!wcs) return mbs;
    std::mbstate_t state = std::mbstate_t();
    std::size_t len = std::wcsrtombs(nullptr, &wcs, 0, &state);
    if(len == static_cast<std::size_t>(-1)) return mbs;
    
    std::vector<char> buf(len + 1);
    const wchar_t* src = wcs; // wcsrtombs increments src
    std::wcsrtombs(buf.data(), &src, len, &state);
    mbs.assign(buf.data());
    return mbs;
}

_BFC_API std::wstring&  MBS2WCS(const char *mbs, std::wstring &wcs, int code_page)
{
    if(!mbs) return wcs;
    std::mbstate_t state = std::mbstate_t();
    std::size_t len = std::mbsrtowcs(nullptr, &mbs, 0, &state);
    if(len == static_cast<std::size_t>(-1)) return wcs;

    std::vector<wchar_t> buf(len + 1);
    const char* src = mbs;
    std::mbsrtowcs(buf.data(), &src, len, &state);
    wcs.assign(buf.data());
    return wcs;
}

_BFC_API bool  initPortable(int argc, char *argv[])
{
    return true;
}

_BFC_API string_t getEnvVar(const string_t &var)
{
    const char* val = std::getenv(var.c_str());
    return val ? string_t(val) : string_t();
}

_BFC_API bool setEnvVar(const string_t &var, const string_t &value)
{
#ifdef _WIN32
    return _putenv_s(var.c_str(), value.c_str()) == 0;
#else
    return setenv(var.c_str(), value.c_str(), 1) == 0;
#endif
}

_BFC_API bool  makeDirectory(const string_t &dirName)
{
    std::error_code ec;
    return fs::create_directories(fs::path(dirName), ec);
}

_BFC_API bool copyFile(const string_t &src, const string_t &tar, bool overWrite)
{
    std::error_code ec;
    auto options = overWrite ? fs::copy_options::overwrite_existing : fs::copy_options::none;
    return fs::copy_file(fs::path(src), fs::path(tar), options, ec);
}

_BFC_API string_t getExePath()
{
#ifdef __linux__
    char result[PATH_MAX];
    ssize_t count = readlink("/proc/self/exe", result, PATH_MAX);
    return std::string(result, (count > 0) ? count : 0);
#elif defined(__APPLE__)
    // macOS implementation would go here (requires <mach-o/dyld.h> and _NSGetExecutablePath)
    return ""; 
#else
    // Fallback or handle verify other OS
    return "";
#endif
}

_BFC_API string_t  getCurrentDirectory()
{
    std::error_code ec;
    return fs::current_path(ec).string();
}

_BFC_API void	setCurrentDirectory(const string_t& dir)
{
    std::error_code ec;
    fs::current_path(fs::path(dir), ec);
}

_BFC_API bool copyFilesToClipboard(const string_t files[], int count)
{
    // Clipboard operations are typically OS/Environment specific (X11, Wayland, etc.)
    // Standard C++ does not support this.
    return false;
}

_BFC_API string_t getTempPath()
{
    std::error_code ec;
    return fs::temp_directory_path(ec).string();
}

_BFC_API string_t  getTempFile(const string_t &preFix, const string_t &path, bool bUnique)
{
    fs::path dir = path.empty() ? fs::temp_directory_path() : fs::path(path);
    
    // Simple improved unique generation
    static std::atomic<int> counter{0};
    std::string name = preFix + std::to_string(std::time(nullptr)) + "_" + std::to_string(counter++) + ".tmp";
    fs::path p = dir / name;
    
    // If unique required, we could loop checking existence, but for this basic impl:
    return p.string();
}

_BFC_API bool  pathExist(const string_t &path)
{
    std::error_code ec;
    return fs::exists(fs::path(path), ec);
}

_BFC_API bool  isDirectory(const string_t &path)
{
    std::error_code ec;
    return fs::is_directory(fs::path(path), ec);
}

_BFC_API string_t getFullPath(const string_t &path)
{
    std::error_code ec;
    return fs::absolute(fs::path(path), ec).string();
}

// Helper to check for hidden files (starts with dot)
inline bool isHidden(const fs::path &p)
{
    std::string filename = p.filename().string();
    return !filename.empty() && filename[0] == '.';
}

_BFC_API void   listFiles(const string_t &path, std::vector<string_t> &files, bool recursive, bool includeHidden)
{
    std::vector<string_t> v;
    fs::path root(path);
    std::error_code ec;

    if (!fs::exists(root, ec) || !fs::is_directory(root, ec)) return;

    if (!recursive)
    {
        for (auto const& entry : fs::directory_iterator(root, fs::directory_options::skip_permission_denied, ec))
        {
            if (fs::is_regular_file(entry.status()) && (includeHidden || !isHidden(entry.path())))
                v.push_back(entry.path().filename().string());
        }
    }
    else
    {
        for (auto const& entry : fs::recursive_directory_iterator(root, fs::directory_options::skip_permission_denied, ec))
        {
            if (fs::is_regular_file(entry.status()) && (includeHidden || !isHidden(entry.path())))
            {
                // Return relative path to match portable_win32 behavior
                v.push_back(fs::relative(entry.path(), root).string());
            }
        }
    }

    files.swap(v);
}

_BFC_API void listSubDirectories(const string_t &path, std::vector<string_t> &dirs, bool recursive, bool includeHidden)
{
    std::vector<string_t> v;
    fs::path root(path);
    std::error_code ec;

    if (!fs::exists(root, ec) || !fs::is_directory(root, ec)) return;

    if (!recursive)
    {
        for (auto const& entry : fs::directory_iterator(root, fs::directory_options::skip_permission_denied, ec))
        {
            if (fs::is_directory(entry.status()) && (includeHidden || !isHidden(entry.path())))
                v.push_back(entry.path().filename().string());
        }
    }
    else
    {
        for (auto const& entry : fs::recursive_directory_iterator(root, fs::directory_options::skip_permission_denied, ec))
        {
            if (fs::is_directory(entry.status()) && (includeHidden || !isHidden(entry.path())))
            {
                v.push_back(fs::relative(entry.path(), root).string());
            }
        }
    }

    dirs.swap(v);
}

_BFC_API void removeDirectoryRecursively(const string_t &path)
{
    std::error_code ec;
    fs::remove_all(fs::path(path), ec);
}

_BFC_API void connectNetDrive(const std::string &remotePath, const std::string &localName, const std::string &userName, const std::string &passWD)
{
    // Not implemented for portable std
	throw std::runtime_error("connectNetDrive is not implemented in portable_std");
}

_FF_END


