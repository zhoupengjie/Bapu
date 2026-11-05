# Bapu（扒谱）

**song → singable score with lyrics.** 把一首歌的录音变成带歌词、可直接在歌声合成软件里打开的人声工程。

```
歌曲音频 ──分离──► 人声 ──主唱/去混响──► 干声主唱 ──转谱──► 音符
                                                          │
用户提供的歌词（字幕 / LRC / 文本）──强制对齐──────────────┤
                                                          ▼
                                        乐谱 JSON（音符 + 歌词 + 语言 + 拍速 + 置信度）
                                                          │
                                   导出：.svp 工程 / 带歌词的 MIDI / 审查报告
```

## 状态

原型阶段。整条流程已经在一首粤语歌上手动跑通，脚本在 [`prototype/`](prototype/)，
每一步的参数、结果和踩过的坑记录在 [`docs/pipeline-notes.md`](docs/pipeline-notes.md)。

## 环境

- Windows / Linux x86_64，NVIDIA GPU（RTX 50 系需要 CUDA 12.8 版 PyTorch）
- Python 3.12，用 [uv](https://docs.astral.sh/uv/) 管理

```bash
uv sync
```

## 用法（v0.1）

```bash
uv run bapu models list                     # 注册表里的模型、下载状态、许可证
uv run bapu models pull <模型ID>             # 先显示许可证，确认后下载并校验 SHA-256
uv run bapu run "<歌曲>.wav" --lyrics "<字幕>.ass"   # 听写 + 参考歌词；输出到 work/<文件名>/，没变的步骤用缓存
uv run bapu run "<歌曲>.wav"                       # 没有参考歌词时全部来自听写（每句都要确认）
uv run bapu run "<歌曲>.wav" --lang yue,zh,en       # 歌里的语言，可多选（默认 yue,en）
uv run bapu models info <模型ID>             # 手动下载：地址、目录、文件名、SHA-256
uv run bapu run "<歌曲>.wav" --set assign.gap_pen=0.4   # 改参数只重跑这一步和之后的步骤
uv run bapu run "<歌曲>.wav" --model lead.dereverb_model=none
uv run bapu status work/<文件名>
uv run bapu corrections from-svp work/<文件名> "<工程>.svp"   # 把在 SV 里改过的地方读成修正
uv run bapu eval work/<文件名> "<工程>.svp" --lines 2,10      # 自动版本和 SV 里的版本逐句对比
uv run bapu export work/<文件名>                # 导出 MIDI（带歌词）、.svp 工程（人声 + 伴奏 + 主唱干声）、粘贴文件
uv run bapu serve                            # 本机网页界面 + API：http://127.0.0.1:8765/
uv run bapu open "<工程文件夹>\<歌名>.bapu"     # 打开工程（服务没开会先在后台启动）
uv run bapu register                         # 双击 .bapu 用 Bapu 打开（只改当前用户的文件关联；--remove 取消）
uv run pytest                                # 单元测试（只用虚构数据）
```

- 原曲可以是 WAV、FLAC、MP3，也可以是 MP4 / M4A / MOV / MKV 等音频或视频文件：soundfile 读不了的格式，
  在第一步用 PyAV（自带 FFmpeg，不用另装）取出第一条音轨，存成 `01_separate/source.wav` 再分离。
- 界面：左侧是 新建工程 / 打开工程 / 保存工程（Ctrl+S）、五个步骤（环境 → 导入 → 自动处理 → 检查 → 导出）和
  实时负载（CPU、内存、GPU、显存、下载、上传）；浅色 / 深色 / 跟随系统，左上角切换。界面有简体中文和 English，第一次按系统语言选（中文系统用中文，其余用英文），
  左上角可以切换；文字在 `web/src/locales/`，代码里写简体中文原文并用 `t()` 包起来。一次只处理一个工程。
  “导入”一页选原曲、可选的参考歌词和语言；“检查”只列出可能不准的地方（点一下就听那一句），不在 Bapu 里改，到 SV 里改。
- 导出可以对齐 SV 的网格（导出页“对齐网格”，默认 1/16；命令行 `bapu export --grid 16`）：先把整首歌（音符、伴奏轨、
  主唱干声轨一起）往后移不到一拍，让歌的拍子落在 SV 的拍线上，再把音符的头尾吸附到网格；音频只移动不拉伸，
  所以和音符始终对得上。吸附不会让音符重叠、变空或同时开始。拍子按测出的固定速度算，速度有变化的歌越往后越偏。
- 侧栏的 GPU / 显存：NVIDIA 用驱动的 NVML；AMD、Intel 用 Windows 自带的 GPU 性能计数器（和任务管理器一样）；
  Apple 芯片读 IORegistry 的 GPU 统计（显存一栏是 GPU 占用的统一内存）。
- 工程：每首歌一个工程文件夹，自动放在原曲旁边（`<歌名> - Bapu`）。外面只有 `<歌名>.bapu`
  （小 JSON：名字、原曲和歌词的位置）和导出的 `.svp` / `.mid`；复制进来的原曲和歌词、修正、每一步的结果、审查报告
  都在旁边的 `.bapu/` 文件夹里。导出不会覆盖不是 Bapu 写的同名文件（例如在 SV 里改过再保存的），会另存为 `(2)`。
  旧的工程（中间结果直接放在工程文件夹里）第一次打开时自动整理进 `.bapu/`，已经算好的步骤不用重算。
  整个文件夹可以移动；双击 `.bapu`（先运行一次 `bapu register`）或 `bapu open` 打开。最近打开过的工程记在 `work/projects.json`。
  网页里选文件和文件夹用的是系统对话框（由本机服务弹出），所以能拿到真实路径；拖进页面的文件会上传后复制进工程。
- 便携版（给用户用的）：`uv run python scripts/build_portable.py --out "<文件夹>"`，生成约 40 MB 的程序文件夹
  （`Bapu.exe`、`tools/uv.exe`、`app/` 里的代码和网页、依赖清单）。用户双击 `Bapu.exe`：
  1. 界面内置在 `Bapu.exe` 里，直接打开，没有单独的“第一次使用”页面。流程第一步“环境”是一张清单：Python 环境、
     流程要用的 7 个模型、Bapu 本身；装好的打勾，缺的点一次“下载”按顺序补齐，每项有进度条和网速，可以暂停、继续。
     Python 环境：检测显卡，选 PyTorch 版本（NVIDIA CUDA 12.8 / AMD ROCm 7.2.1（实验，RX 7000 / 9000 和部分 Ryzen AI，Windows 11，AMD 驱动 26.2.2 以上，PyTorch 2.9.1，从 repo.radeon.com 下载，没有国内镜像）/ Intel XPU（实验）/ CPU），用自带的 uv 把 Python 3.12 和全部依赖装进
     `runtime/`（不建 venv，整个文件夹可以移动；不写注册表、不进 PATH、不碰系统里的 Python）。uv 的下载缓存在装好后才删，
     暂停或失败后再装会跳过已经下载的包。装好后 `Bapu.exe` 启动 Python 服务，接着下载模型。日志在 `data/logs/`。
  2. 模型下载支持断点续传和多线程：大文件按“下载线程”数（1–16，默认 8，在“环境”页改）分成几段同时下，
     每段的进度记在 `<文件>.part.json`，暂停、关掉 Bapu 或断网后从断点接着下；服务器不支持分段时退回单线程续传。
     下完照样核对 SHA-256。uv 同时下载的包数也用这个设置。
  下载源：装运行环境时可以选“自动 / 官方源 / 国内镜像”（自动会测速）。国内镜像用阿里云 PyPI 和 PyTorch、npmmirror（Python）、
  hf-mirror（HuggingFace）和 ghfast.top（GitHub 加速），见 `src/bapu/mirrors.json`；模型下载和更新也跟着这个设置，
  在“环境”页可以改。不管从哪里下载都按 SHA-256 校验，镜像只决定从哪里取文件。MMS 对齐模型没有国内镜像。
- 更新：“环境”页的清单里有 Bapu 本身（每天自动检查一次，侧栏会提示新版本）。只下载程序部分（约 20 MB），关闭 Bapu 时由 `Bapu.exe` 校验签名后替换
  `Bapu.exe`、`app/`、`tools/`；`runtime/`、`models/`、`data/` 不动。新版本的依赖有变化时，下次启动只补装变了的包。
  发布新版本：`uv run python scripts/release.py --notes "…" --publish`（GitHub 发布页，附 `latest.json` 和 Ed25519 签名；
  签名私钥在 `~/.bapu/release-signing-key`，不进仓库，公钥内置在 `desktop/src-tauri/src/updater.rs`）。
  程序文件夹要放在有写入权限的位置（不要放 C:\Program Files）。重新运行构建脚本只替换 `app/`、`tools/` 和 `Bapu.exe`，
  `runtime/`、`models/`、`data/` 保留。
- macOS（只支持 Apple 芯片，M1 及以后，macOS 14 以上；PyTorch 不再出 Intel Mac 的版本）：在 Mac 上运行
  `uv run python scripts/build_portable.py` 生成 `dist/Bapu.app` 和安装盘 `dist/Bapu-mac-<版本>.dmg`；GitHub 上发布新版本时，
  `.github/workflows/macos.yml` 会在 macOS 机器上跑测试、构建并把 dmg 附到发布页（也可以手动运行）。和 Windows 版的区别：
  Python 环境、模型和数据放在 `~/Library/Application Support/Bapu`（不写进 Bapu.app，否则签名会失效）；推理用 Metal（MPS，
  实验），PyTorch 是 2.13.0（audio-separator 在 Apple 芯片上要求 2.13 以上）；只做了临时签名、没有公证，第一次要右键→打开；
  程序内更新只提示新版本，到发布页下载。还没在真的 Mac 上测过。
- 桌面窗口（Tauri，`desktop/`）开发：`cd desktop && npm install && npm run build`，生成
  `desktop/src-tauri/target/release/Bapu.exe`；旁边没有 `app/` 时它用这个代码目录的 `.venv`。它在后台启动
  `bapu serve --parent-pid`（随机端口，窗口关了服务就退出），选文件用系统对话框，外部链接用默认浏览器打开，
  `.bapu` 拖到窗口上就打开；已经开着时，再打开的 `.bapu` 在原窗口打开。
- 开发时如果模型不在 `.cache/models`（比如已经移到便携版里），设 `BAPU_CACHE_DIR=<便携版文件夹>` 共用同一份模型。
- 网页界面：第一次先 `cd web && npm install && npm run build`，之后 `uv run bapu serve` 就会同时提供界面和 API。
  开发前端时用 `npm --prefix web run dev`（http://127.0.0.1:5173，/api 代理到 8765）。
- 输出：`score.json`（乐谱）、`review.md` / `review.json`（审查报告，只有时间、编号和置信度）。
- `--set`、`--model` 保存在工作目录的 `params.json`；`--lyrics` 记在 `manifest.json`，下次可以省略。
- 修正写在工作目录的 `corrections.yaml`，按时间定位，重跑不会覆盖；格式见 `src/bapu/corrections.py`。
- 整首歌回归测试：设置 `BAPU_REGRESSION_AUDIO` 和 `BAPU_REGRESSION_LYRICS` 后运行 `uv run pytest -m regression`。

模型权重不随项目分发，各自有不同的许可（见笔记的“许可”一节），第一次用到时下载到 `.cache/`，不提交到仓库。
SOME 的推理代码已移植进 `src/bapu/backends/some/`（MIT，保留了原作者的版权声明），不需要另外检出。

## 原则

- 歌词来自用户自己的录音（本机听写）和用户提供的歌词文件，工具不联网抓歌词；日志和报告里不出现歌词文字。
- 处理他人作品前需要用户确认拥有相应权利；输出文件默认不提交。
- 自动判断不确定的地方必须进审查报告，由人来确认；人的修正要能在重跑时保留。

## 许可证

Bapu 的代码用 [MIT](LICENSE) 许可：可以自由使用、修改和商用，保留版权声明即可。
`src/bapu/backends/some/` 移植自 openvpi/SOME，同样是 MIT，保留了原作者的版权声明。

模型不属于代码，各有自己的许可证，下载前会显示并要你确认。其中有的只允许非商用
（例如 MMS 对齐模型是 CC BY-NC 4.0），有的没有声明许可证：即使代码是 MIT，
用整套流程商用时也要以各个模型的许可证为准。uv（随便携版分发）是 MIT / Apache-2.0。
