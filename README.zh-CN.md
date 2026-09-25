# ComfyUI Image Ledger（原图台账）

[English](README.md) · [安全策略](SECURITY.md) · [参与贡献](CONTRIBUTING.md)

**在 ComfyUI 里批量图生视频，同一张原图不会被重复跑。**

Image Ledger 会记住哪些原图已经真正生成过最终视频。你从图库里挑图或随机抽一张，点「用这张跑」，跑完的原图会自动退出待选队列。现有的 `LoadImage` 工作流不用改。

> 当前版本：公开测试版 `0.2.0`。默认开启记账，但**默认不移动任何原图**。

![全局原图台账面板](docs/assets/global-panel-zh.png)

## 适合什么场景

- 有大量图生视频原图，不想重复跑同一张。
- 多个工作流共用一份完成记录。
- 想先在大图画廊里挑图，再自动写回 `LoadImage` 并排队。
- 想把刚才跑完的那张原图再跑一次，包括已经进了 `_used` 的文件。
- 只有最终视频确实保存成功后，才把原图算作完成。
- 需要按内容哈希去重、崩溃恢复、撤销、旧视频补录。

## 安装

**ComfyUI-Manager（推荐）：** 打开 Manager → Custom Nodes Manager，搜索 **Image Ledger**，安装后重启 ComfyUI。

**comfy-cli：**

```bash
comfy node install comfyui-image-ledger
```

**手动安装：** 把仓库克隆到 `ComfyUI/custom_nodes` 后重启 ComfyUI：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Rona1do/ComfyUI-Image-Ledger.git
```

无需额外安装 Python 包。SQLite、Pillow 和 aiohttp 均由 ComfyUI 环境提供；旧视频补录会优先复用 VideoHelperSuite 或 `imageio-ffmpeg` 的 FFmpeg。

## 最快上手：全局模式

1. 把原图库放在 `ComfyUI/input/AI` 下，并用一级文件夹分类。想用别的目录，可以在 `Settings → Image Ledger → Global tracking → Library folder` 里修改，例如填 `Sources` 就是 `ComfyUI/input/Sources`。`input` 下也可以放一个目录链接，指向其他磁盘上的图库。
2. 重启后，右下角会显示“全局原图台账”。点 `–` 可以收起。
3. 选择分类，然后浏览大图或随机抽取。面板会显示这个分类还剩多少张没跑。
4. 点击“用这张跑”，脚本会写回最可能的源图 `LoadImage` 并 Queue。
5. 工作流成功保存最终视频后，这张图才会记为完成。
6. 点击「重跑上一张」，会把刚才那张原图重新写回源图节点并排队。文件已经在 `_used` 里也可以。

脚本会优先选择标题含 `first frame`、`源图`、`原图`、`首帧` 的 `LoadImage`，并排除 `last frame`、`mask`、`末帧`、`遮罩`。只有一个启用中的 `LoadImage` 时会使用它作为兜底。

## 文件移动是可选功能

默认只记账，不移动文件。跑过的原图会留在原处，但浏览和随机抽都会跳过它们。需要自动归档时，在以下位置明确开启：

`Settings → Image Ledger → Global tracking → Move source`

开启后，`input/AI/portraits/001.png` 会移动到 `input/AI/portraits/_used/001.png`。

“效果不佳 / Reject”会移动到 `_used/_rejected`。面板可以撤销最近一次操作。对重要图库开启前，建议先用少量备份文件验证。

## 高级模式：节点式精确记账

```text
Image Ledger · Visual Queue.image
    → 原图生视频流程
    → VHS_VideoCombine.Filenames
    → Image Ledger · Commit Finished Video.filenames

Image Ledger · Visual Queue.job_ticket
    → Image Ledger · Commit Finished Video.job_ticket
```

Commit 节点只有在最终视频位于 ComfyUI output、文件存在且非空时才提交完成状态。请把它放在最终视频保存节点之后、清理节点之前。

## 数据与隐私

运行数据不写进项目目录：

```text
ComfyUI/user/default/image_ledger/ledger.sqlite3
ComfyUI/user/default/image_ledger/settings.json
ComfyUI/user/default/image_ledger/thumbs/
```

可用 `COMFYUI_IMAGE_LEDGER_DIR` 改数据目录，用 `COMFYUI_IMAGE_LEDGER_FFMPEG` 指定 FFmpeg。

项目没有遥测、登录或云端上传。它只读取 ComfyUI input/output 范围内的文件，写本地 SQLite 与缩略图缓存，并在用户明确开启后移动源图。

## 已知限制

- 推荐 Python 3.10+ 和近期版本的 ComfyUI。
- 原图库必须位于 `ComfyUI/input` 下（可以用目录链接指向任意位置），默认是 `input/AI`。
- 不开启移动时，靠路径识别已跑原图。把跑过的图改名复制一份，图库里仍会显示为待选，但记账时仍会按内容哈希去重。
- 自动记账需要执行结果中出现成功保存的视频条目。
- 旧视频只有保留兼容的 prompt metadata 才能恢复原图。
- ComfyUI 内部执行接口可能变化；反馈兼容问题时请附 ComfyUI 版本或提交日期。

测试命令和提交流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，后续计划见 [ROADMAP.md](ROADMAP.md)。

## 许可证

[MIT](LICENSE)。ComfyUI 是独立项目，本仓库不包含 ComfyUI 本体。
