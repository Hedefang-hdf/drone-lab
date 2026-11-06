# PX4 SITL 在 macOS (Apple Silicon) 上的搭建踩坑记录

环境：macOS + Apple Silicon + PX4 main 分支 + 自建 Python venv。
本机已完整编译出 `build/px4_sitl_default/bin/px4`，SITL 可起飞悬停。以下是踩过的坑。

## 坑 1：Zenoh Kconfig 目录生成失败

现象：`make px4_sitl` 报 `Failed to generate Zenoh Kconfig topics catalog (1)`，
但手工在 venv 里跑同样的脚本却是成功的。

原因：CMake 抓错了 Python——用了系统 Python（没有 `em` / `genmsg`）。
`CMakeLists.txt` 只认仓库内的 `.venv`。

解法：把仓库内 `.venv` 软链到真实的 venv：

```bash
ln -s ~/PX4/venv ~/PX4/px4/.venv
```

## 坑 2：kconfiglib 缺子命令

现象：`No module named defconfig`，boardconfig 不生成。

原因：PyPI 上的 kconfiglib 9.4.2 wheel 残缺，缺
`defconfig.py / savedefconfig.py / olddefconfig.py / setconfig.py / guiconfig.py`。

解法：下载 14.1.0 的 wheel，解压覆盖进 venv 的 site-packages。

## 坑 3：C++ 标准库头文件找不到

现象：编译报 `fatal error: 'cstdlib' file not found`。

原因：macOS Command Line Tools 的
`/Library/Developer/CommandLineTools/usr/include/c++/v1/` 只剩空壳，
且遮住了 SDK 里真正的头文件。

解法（二选一）：

```bash
# 方法 A：显式指定 isystem
cmake -B build -S . -DCMAKE_CXX_FLAGS="-isystem $(xcrun --show-sdk-path)/usr/include/c++/v1"

# 方法 B：环境变量挂上 SDK
export SDKROOT="$(xcrun --show-sdk-path)"
```

## 坑 4：新版 PX4 没有 `none_iris`

老教程的 `make px4_sitl none_iris` 在新版 Makefile 已不存在。
用环境变量选机型：

```bash
PX4_SIM_MODEL=sihsim_quadx ./bin/px4
# 对应 SYS_AUTOSTART=10040（sihsim 四旋翼）
```

## 现在的启动方式

```bash
cd build/px4_sitl_default
PX4_SIM_MODEL=sihsim_quadx ./bin/px4
# 出现 pxh> 和 Ready for takeoff 即就绪
```

命令行起飞：

```bash
commander takeoff    # pxh> 里执行
```
