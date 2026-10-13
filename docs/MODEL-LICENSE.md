# 代码与模型的许可

本项目的 MIT 许可仅覆盖仓库中自行编写的画室界面、服务和脚本。模型权重、ComfyUI 和其他依赖各自遵循上游许可；本仓库不捆绑模型权重，也不授予模型的商业使用权。本项目是独立社区界面，与 Qwen 或 Comfy Org 没有官方隶属关系。

## Qwen-Image-2.1-Turbo

截至 2026-10-13，官方 [Turbo 模型页](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) 与 [Comfy-Org 模型包](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) 均标注 `qwen-research`。请阅读 [Qwen Research License 原文](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE)，不要套用其他 Qwen 型号的 Apache 2.0 条款。

与本项目使用方式直接相关的条款：

- 第 1(i)、2 条：授权范围为非商业研究或评估；商业使用需要另行向权利人取得许可。
- 第 3 条：如另行再分发模型或其衍生材料，需要满足上游许可证、修改标识及归属声明等要求。仅给出下载链接不等于获准更改权重的许可证。
- 第 4(c)、5(b) 条：不得将 “Qwen” 作为衍生作品或产品的主要名称；可合理描述所使用的模型，不因此取得商标许可。

上面是摘要，适用范围和完整条件以链接中的许可原文为准。下载器从上游获取权重并校验完整性，不改变这些条件。

## 其他组件

[ComfyUI](https://github.com/Comfy-Org/ComfyUI) 作为单独安装的本机后端使用；本项目不将其代码重新许可为 MIT。Pillow、Python 及 GPU 运行环境同样保留各自许可证。

## English summary

The MIT license covers this repository's original UI, service, and scripts only. Model weights are not bundled. Qwen-Image-2.1-Turbo and the selected Comfy-Org repack follow the upstream Qwen Research License, which limits use to non-commercial research or evaluation unless a separate commercial license is obtained. Review the [upstream agreement](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE), including its redistribution and naming provisions. This project is an independent community interface, not an official Qwen or Comfy Org product.
