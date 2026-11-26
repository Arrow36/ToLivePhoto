# ToLivePhoto

Windows 原生桌面端 **视频转 iPhone 实况照片（Live Photo）工作台**，支持原始帧无损重排、RIFE 光流 AI 补帧（类 DLSS / 小黄鸭原理）、画面外扩高斯模糊/纯色留边、自定义实况封面，以及一键导出符合苹果相机规范的 `DCIM/100APPLE` 目录到 U 盘直插 iPhone 导入。

---

## 核心特性

1. **原生 iPhone Live Photo 封装（`HEIC + MOV` / `JPG + MOV`）**
   * 内置大端序（`MM`）EXIF 与 Apple `MakerNote`（`0x0011 ContentIdentifier`、`0x0014 ImageCaptureType`）写入器。
   * 生成 `ftyp → moov → mdat` 顺序的 QuickTime `.MOV`，包含 `com.apple.quicktime.live-photo.auto`、`vitality-score`、`still-image-time` 及 `mebx` 定时元数据轨。
   * 默认**不缩放原尺寸**，完整保留高分辨率视频与封面画质；支持一键控时 `≤ 2.95s` 与 NVIDIA NVENC GPU 硬件编码。
2. **原始帧自定义帧率与重排**
   * **原始帧重排模式**：100% 保留截取区间内的原始帧，不丢帧不插帧，通过重写时间戳实现自定义帧率（快捷预设 `24 / 25 / 30 / 60 fps`），并支持音频同步变速（`atempo`）或保持原速淡出。
3. **RIFE AI 光流补帧（Vulkan GPU 加速）**
   * 集成 `rife-ncnn-vulkan`（`rife-v4.25` 高画质 / `rife-v4.6` 极速模型），支持 `2x / 3x / 4x` 补帧与补帧后自定义播放帧率；软件内支持一键精简下载补帧引擎（约 22 MB）。
4. **画布交互、手动裁切与外扩背景**
   * 预览画布支持**鼠标滚轮以指针为中心缩放**、**按住左键拖拽平移**、**右键快捷菜单**。
   * 裁切框默认隐藏，点击 **`✂ 裁切画面`** 后出现；支持拖出原视频边界外扩留边（高斯模糊原图或纯色背景）。
5. **自定义相机编号与 U 盘直导 iPhone**
   * 支持自定义 4 字符相机前缀（如 `IMG_` / `CSI_`）+ 4 位编号，支持**导出后自动 +1** 与防覆盖冲突递增。
   * U 盘插入 iPhone 后，打开系统 **「照片」App → 右下角「导入」** 即可识别并合成为实况照片。

---

## 环境准备与运行

1. 安装 Python 3.11+ 与依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 确保系统已安装 `FFmpeg`（可通过 `winget install Gyan.FFmpeg` 安装，或放入 `bin/ffmpeg/`）。
3. 下载 RIFE 补帧引擎（可选，也可在软件界面内一键下载）：
   ```bash
   python scripts/setup_rife.py
   ```
4. 启动桌面端：
   双击 `启动_ToLivePhoto.bat` 或运行：
   ```bash
   python -m tolivephoto.app
   ```

## 测试

```bash
pytest -v
```

## 开源协议

[MIT License](LICENSE) © 2026 Arrow36
