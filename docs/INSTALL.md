# 灵感画室 · 本地安装与复刻

画室由浏览器界面、轻量 Python 服务和同一台电脑上的 ComfyUI 组成。GitHub 保存代码、工作流和安装说明；图片由你自己的 GPU 生成。把网页放到 GitHub Pages 不能代替 ComfyUI 或运行模型。

当前以 Windows + NVIDIA GPU 为主要验证平台。画室服务只需要 Python 与 Pillow；PyTorch、CUDA 及模型节点由 ComfyUI 环境负责。安装画室不会替换已有 ComfyUI 的 Python 包，也不会安装显卡驱动。

## 1. 准备环境

需要：

- 一个支持 Qwen-Image-2.1 的 ComfyUI，能在本机浏览器中正常打开。
- Python 3.10 或更新版本，含 `venv` 和 `pip`。建议为画室使用常规 Python 安装，不把 ComfyUI 的嵌入式 Python 当作通用虚拟环境解释器。
- 三份模型共 **14,244,402,881 字节（约 14.24 GB / 13.27 GiB）**，另需为 ComfyUI、Python 依赖、临时下载和图片预留空间。
- 可连接官方模型下载地址的网络。模型和依赖准备好后，本项目的生图工作流不调用云端付费 API。

本项目开发时验证过以下组合。它是可参考的成功配置，不是所有设备的最低配置保证。

| 项目 | 已验证环境 |
| --- | --- |
| 系统 / GPU / 内存 | Windows，RTX 5080 Laptop 16 GB 显存，32 GB 内存 |
| ComfyUI | 0.39.0；提交 `8010b48d2c1be986613f8e8c761527d205bd756f` |
| ComfyUI 前端 | 1.55.14 |
| 推理环境 | Python 3.13.14，PyTorch 2.11.0+cu128 |
| 模型组合 | Turbo INT8 ConvRot + Qwen3-VL-8B W4A8 + RGBA VAE |
| 已验证任务 | 文字生图、单图修改、双参考图创作，768 / 1024 档 |

显存占用会随图像比例、参考图数量和其他程序变化。首次先用 768 档、单张输出，并关闭其他占用 GPU 的生成任务。本项目尚未验证 AMD、Intel、macOS 或纯 CPU 推理；已有兼容 ComfyUI 的其他平台可以尝试连接，但需要自行验证该量化模型的算子支持。

### 还没有 ComfyUI

Windows 可按照 [ComfyUI 官方便携版安装说明](https://docs.comfy.org/installation/comfyui_portable_windows) 下载适配显卡的包、解压并启动。便携版目录中的 `ComfyUI` 子目录才是稍后要填写的目录，里面应有 `main.py`、`models`、`input` 和 `output`。

希望自行管理 Python 环境的用户，可遵循 [官方手动安装说明](https://docs.comfy.org/installation/manual_install)。在独立环境内安装与显卡、驱动兼容的 PyTorch 和 ComfyUI 依赖，先确认 ComfyUI 能启动，再接入画室。不要在现有可用环境中直接照抄另一块显卡的 CUDA 安装命令。

如需复核本项目的 ComfyUI 基线，可查看 [已验证提交](https://github.com/Comfy-Org/ComfyUI/tree/8010b48d2c1be986613f8e8c761527d205bd756f)。较新版本可能兼容，最终以节点检查和一次真实出图为准；仅有版本号不能证明量化算子能在当前 GPU 上执行。

## 2. 下载项目并配置画室

从本仓库选择 **Code → Download ZIP** 后解压，或使用 Git 克隆：

```powershell
git clone https://github.com/hellobigstar/inspiration-studio.git
cd inspiration-studio
```

在项目根目录打开 PowerShell，运行：

```powershell
.\setup.ps1
```

安装脚本会创建项目内的 `.venv`、安装 `requirements.txt`，并根据输入生成本机的 `config.json`。它不会下载十几 GB 的模型，也不会替你启动 ComfyUI。若 PowerShell 策略阻止脚本运行，使用本节下方的手动 Python 命令，无需更改策略。

脚本默认后端地址为 `http://127.0.0.1:8189`。如果自己的 ComfyUI 使用常见的 8188 端口，请按下面的示例传入 `-BackendUrl`。已有 `config.json` 时，脚本会保留它；修改已有路径或端口需直接编辑配置，不会由新参数覆盖。

已经知道后端位置时，也可以直接传参：

```powershell
.\setup.ps1 -ComfyUIDir 'D:\AI\ComfyUI_windows_portable\ComfyUI' -BackendUrl 'http://127.0.0.1:8188'
```

脚本优先选择 `py -3`，其次选择 `python`。需要指定某个常规 Python 安装时，补充 `-Python 'C:\Python312\python.exe'`，将路径替换为自己实际安装的位置。

配置示例：

```json
{
  "comfyui_dir": "D:/AI/ComfyUI_windows_portable/ComfyUI",
  "backend_url": "http://127.0.0.1:8188",
  "port": 8190,
  "data_dir": "data",
  "max_upload_mb": 20
}
```

| 字段 | 含义 |
| --- | --- |
| `comfyui_dir` | 该 ComfyUI 实例的实际目录，必须与下面的服务地址对应 |
| `backend_url` | 已启动的 ComfyUI 地址；默认常见为 8188，也可以使用自己配置的 8189 等端口 |
| `port` | 画室网页端口，默认 8190，不能与 ComfyUI 端口相同 |
| `data_dir` | 画室任务记录和工作流目录；相对路径相对于 `config.json` 所在目录，通常就是项目根目录 |
| `max_upload_mb` | 每张参考图允许上传的大小，默认 20 MB |

路径建议使用 JSON 支持的正斜杠 `/`，或将反斜杠写成 `\\`。项目服务面向同机使用；`backend_url` 不是外网模型服务地址。

`config.json` 含你的本机路径，不应提交到公开仓库。迁移到另一台电脑时重新运行配置即可。

### 手动安装：不运行 PowerShell 脚本

在项目根目录的 PowerShell 中执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath .\config.json)) {
    Copy-Item -LiteralPath .\config.example.json -Destination .\config.json
}
notepad .\config.json
```

上面的命令会保留已有 `config.json`。没有 `py` 启动器但已有常规 Python 时，可将首行改成 `python -m venv .venv`。在打开的配置中填写真实的 ComfyUI 目录、后端地址和画室端口，保存后继续下载模型和检查。

手动启动同样不需要激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\studio\server.py --check
.\.venv\Scripts\python.exe -X utf8 .\studio\server.py --open-browser
```

`--check` 只检查，不启动画室网页；`--open-browser` 在服务监听成功后打开浏览器。使用另一份配置时，在命令末尾加入 `--config '配置文件路径'`。这两个入口也读取 `QWEN_STUDIO_CONFIG` 环境变量。

## 3. 单独下载模型

先阅读 [模型许可说明](MODEL-LICENSE.md)，再在项目根目录预览下载计划：

```powershell
.\.venv\Scripts\python.exe .\download_models.py
```

默认只显示计划，不联网下载。确认目录与许可后，显式开始下载；出现提示时输入 `yes`：

```powershell
.\.venv\Scripts\python.exe .\download_models.py --download
```

下载器从 Comfy-Org 官方 Hugging Face 仓库获取文件，并根据项目的模型清单验证文件大小和 SHA-256。仓库本身不包含这些权重。已存在且校验正确的文件可以复用；不能只凭文件名或文件大小确认权重完整。只校验本地文件、不联网时使用：

```powershell
.\.venv\Scripts\python.exe .\download_models.py --check-only
```

默认的计划预览不需要配置文件；下载或校验时，下载器读取根目录的 `config.json`，或 `QWEN_STUDIO_CONFIG` 指定的配置。需要切换配置时传入 `--config 路径`。自动化环境在阅读并接受模型许可后，可以使用 `--download --yes` 跳过输入确认。

也可以从以下官方链接手动下载，保持文件名不变，放入 `comfyui_dir` 下对应位置。该目录布局来自 [Comfy-Org 官方模型说明](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)。

| 官方下载 | 目标目录 | 字节数 |
| --- | --- | ---: |
| [qwen_image_2.1_turbo_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors) | `models/diffusion_models/` | 7,256,787,829 |
| [qwen3vl_8b_w4a8.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_w4a8.safetensors) | `models/text_encoders/` | 6,312,105,364 |
| [qwen_image_2.1_vae_bf16.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors) | `models/vae/` | 675,509,688 |

2026-10-13 核对的官方 SHA-256：

```text
8144a0182964aa8403ad6dbc011db837304167d235161b9aabaa8532e967314c  qwen_image_2.1_turbo_int8_convrot.safetensors
7754425e55e7bea2bfde4dde59a4cc236cb44e5ee9c215ea66ef8d47012824eb  qwen3vl_8b_w4a8.safetensors
bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9  qwen_image_2.1_vae_bf16.safetensors
```

下载后重启 ComfyUI，让它重新识别模型。使用 ComfyUI 的额外模型路径功能时，还需让工作流中的名称与该实例实际返回的模型名称一致；首次复刻建议使用上表的标准目录。

## 4. 检查并启动

1. 用自己的 ComfyUI 启动脚本启动后端，确认 `backend_url` 能打开。
2. 在画室项目根目录检查配置、模型与节点：

   ```powershell
   .\.venv\Scripts\python.exe .\studio\server.py --check
   ```

3. 双击 `start.cmd`，或在 PowerShell 中运行：

   ```powershell
   .\start.cmd
   ```

4. 打开 [本机画室](http://127.0.0.1:8190)，选择文字生图、768 档，先完成一次生成。

检查通过表示已满足可检查的前置条件，首次真实出图才会检验 CUDA 算子和显存。画室不自动管理后端进程；停止画室不会停止 ComfyUI，关闭浏览器也不会释放模型显存。结束生成后通过原有 ComfyUI 启动窗口停止后端。

## 5. 第一次使用

| 模式 | 输入与建议 |
| --- | --- |
| 文字生图 | 不上传图片；描述主体、构图、光线、风格，以及需要出现的文字 |
| 参考图创作 | 上传 1–2 张图片，按顺序称“图1”“图2”，说明各取哪一部分 |
| 图片修改 | 上传 1 张原图；分开填写“修改要求”和“保留要求” |

保留要求是给模型的指令，不是像素锁定。完成后可用滑杆对比原图，检查文字、人物和局部细节。当前版本没有手绘蒙版工具。

768 / 1024 表示分辨率档位：文字生图为方图；有参考图时按第一张图的比例生成，总像素接近档位的平方，宽高会对齐模型要求。固定种子方便同环境下复现；改变模型、依赖或 GPU 环境仍可能改变结果。

本工作流使用 Euler、CFG=1 和官方 Turbo 8 步采样表：

```text
1,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0
```

Turbo 的 8 步与 CFG=1 来自 [官方 Turbo 模型说明](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo)。不要把它直接替换成基础模型的采样设置。

## 6. 数据与复现文件

- 上传图片：`ComfyUI/input/qwen-studio/`。
- 生成图片：`ComfyUI/output/QwenStudio/`。
- 任务历史、参数及 JSON 工作流：配置的 `data_dir`。
- 浏览器中的未提交草稿：当前浏览器的本地存储。

下载的 PNG 内含提示词和工作流；分享前检查是否含有不想公开的提示词。JSON 工作流可以拖回 ComfyUI，跨电脑使用参考图工作流时，还需带上原图并在图片节点重新选择。公开自己的 Fork 时保留 `.gitignore`，不要提交本机配置、上传图片、任务记录、模型或运行日志。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 后端未连接 | 先打开配置中的 `backend_url`；确认端口和目录属于同一个 ComfyUI 实例 |
| 缺少 `TextEncodeQwenImage21` / `QwenImage21Cache` 等节点 | 当前 ComfyUI 太旧或依赖未就绪；升级独立 ComfyUI 环境后重新运行检查 |
| 模型不在下拉列表 | 检查目录、完整文件名和下载校验；安装后重启 ComfyUI |
| CUDA 驱动或注意力内核错误 | 本工作流已用 `ModelAttentionBackend` 固定 `pytorch attention`；保留此节点，并检查 GPU、驱动与 ComfyUI 的 PyTorch 是否兼容 |
| 显存不足 | 使用 768 档和单参考图，关闭其他 GPU 任务；仍失败时检查 ComfyUI 日志与硬件条件 |
| 页面可以打开但一直等待 | 检查 ComfyUI 是否有其他正在运行或排队的任务；首次模型加载可能明显慢于热运行 |
| 移到别的电脑后图片丢失 | 工作流只引用原图路径；同时迁移原图，或在节点中重新选图 |
| 下载中断 | 再运行下载器，续传对应 `.part` 文件 |
| 已有模型或 `.part` 校验失败 | 下载器会保留文件并提示具体路径；自行将该文件移到其他位置后再重试，不要改名冒充有效权重 |

提交问题时附上操作系统、显卡/显存、ComfyUI 版本、检查输出和错误日志片段即可；先删去个人路径、提示词和图片等不打算公开的信息。
