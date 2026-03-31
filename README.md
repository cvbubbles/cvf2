## Recommended Setup

The recommended dependency manager for this project is `vcpkg`.

1. Install `vcpkg`.
2. Use `vcpkg` to install the required libraries.
3. Update `VCPKG_ROOT` in [CMakePresets.json](/f:/dev2/cvf2/CMakePresets.json) to match your local `vcpkg` path.
4. Configure and build the project with CMake presets.

The current preset file contains:

```json
"environment": {
  "VCPKG_ROOT": "F:/vcpkg/vcpkg"
}
```

If your local `vcpkg` is installed in a different directory, change that path before configuring the project.

## Dependencies

This project currently depends on the following libraries:

- `opencv`
- `assimp`
- `freeglut`
- `glm`
- `nlohmann-json`


## Install Dependencies With vcpkg

For the current Windows x64 preset, install the dependencies with:

```powershell
vcpkg install assimp:x64-windows freeglut:x64-windows glm:x64-windows nlohmann-json:x64-windows opencv:x64-windows
```

## Configure And Build

After `vcpkg` is ready and `VCPKG_ROOT` has been updated:

```powershell
cmake --preset msvc-x64
cmake --build --preset release
```
