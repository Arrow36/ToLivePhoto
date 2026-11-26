# ToLivePhoto

Windows 原生桌面工具，将任意视频一键转换为 **iPhone 原生可识别、U盘直导合成** 的 **Live Photo（实况照片）**。

## 核心特性

- **真·iPhone Live Photo 配对规范**
  - 生成配对的 `HEIC + MOV`（或 `JPG + MOV`），内嵌大端序 `Apple iOS` `MakerNote (0x0011 ContentIdentifier)` 与 QuickTime `com.apple.quicktime.content.identifier`、`still-image-time` 元数据轨。
  - `ftyp -> moov -> mdat` 优化封装，U 盘插入 iPhone 后通过系统「照片」App 右下角「导入」即可直接合成实况照片。
- **全分辨率预览画布与拍立得相纸外扩**
  - 瓷白画布 (`#FEFDF9`) + 全分辨率 FFmpeg 硬件解码预览，支持滚轮无极缩放、左键拖拽平移、右键快捷菜单。
  - **多比例裁切 + 手动比例输入 + 外扩背景**（高斯模糊 / 纯白 / 纯黑 / 自定义取色）。
  - **拍立得相纸预设**：一键应用 `Instax Mini`、`Instax Square`、`Instax Wide`、`Polaroid 600` 真实相纸比例与非对称白边排版。
- **帧率重排与 AI 光流补帧（GPU Vulkan 加速）**
  - 默认保持原帧率；支持不丢帧原始帧重排，以及基于 `rife-ncnn-vulkan`（类 DLSS / 小黄鸭原理）的 GPU AI 光流补帧（默认 30 帧，可选 `24 / 25 / 30 / 60` 及倍率慢放）。
  - 全程采用 **`X秒Y帧`** 直观显示；超过 3 秒可一键点击 **`✂ 控时3秒`** 在主图附近自动裁切 3 秒片段，不改动目标帧率。
- **自定义主图、多视频队列与相机 EXIF 导入**
  - 支持多视频同时导入与队列批量导出，自动递增相机 DCF 编号（如 `CSI_7607` → `CSI_7608`）。
  - 支持导入外部高清图片作为实况封面，并提供「居中裁切封面 / 留边保留全图 / 视频随封面比例」三种比例处理。
  - 支持输出码率滑杆与实时文件大小预估；支持修改并从 `.CR3 / .CR2 / .NEF / .ARW / .DNG / .RAF / .JPG / .HEIC` 读取相机 EXIF 写入实况照片。

## 运行与安装

```bash
pip install -r requirements.txt
python -m tolivephoto.app
```

可选下载 RIFE Vulkan 补帧引擎（约 22 MB，含 `rife-v4.25` 与 `rife-v4.6` 模型）：

```bash
python scripts/setup_rife.py
```

## 测试

```bash
pytest -v
```
