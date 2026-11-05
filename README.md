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
- 工程：每首歌一个工程文件夹，由用户在新建时选（默认在原曲旁边，`<歌名> - Bapu`）。外面只有 `<歌名>.bapu`
  （小 JSON：名字、原曲和歌词的位置）和导出的 `.svp` / `.mid`；复制进来的原曲和歌词、修正、每一步的结果、审查报告
  都在隐藏的 `.bapu/` 文件夹里。导出不会覆盖不是 Bapu 写的同名文件（例如在 SV 里改过再保存的），会另存为 `(2)`。
  旧的工程（中间结果直接放在工程文件夹里）第一次打开时自动整理进 `.bapu/`，已经算好的步骤不用重算。
  整个文件夹可以移动；双击 `.bapu`（先运行一次 `bapu register`）或 `bapu open` 打开。最近打开过的工程记在 `work/projects.json`。
  网页里选文件和文件夹用的是系统对话框（由本机服务弹出），所以能拿到真实路径；拖进页面的文件会上传后复制进工程。
- 桌面窗口（Tauri，`desktop/`）：`cd desktop && npm install && npm run build`，生成
  `desktop/src-tauri/target/release/Bapu.exe`。它在后台用 `.venv` 启动 `bapu serve`（随机端口，窗口关了服务就退出），
  在窗口里显示同一套网页界面；选文件用系统对话框，外部链接用默认浏览器打开。建好后再运行一次 `bapu register`，
  双击 `.bapu` 就用这个窗口打开（已经开着时在原窗口打开）。`npm run bundle` 打 NSIS 安装包（还没处理 Python 环境的分发）。
- 网页界面：第一次先 `cd web && npm install && npm run build`，之后 `uv run bapu serve` 就会同时提供界面和 API。
  开发前端时用 `npm --prefix web run dev`（http://127.0.0.1:5173，/api 代理到 8765）。
- 输出：`score.json`（乐谱）、`review.md` / `review.json`（审查报告，只有时间、编号和置信度）。
- `--set`、`--model` 保存在工作目录的 `params.json`；`--lyrics` 记在 `manifest.json`，下次可以省略。
- 修正写在工作目录的 `corrections.yaml`，按时间定位，重跑不会覆盖；格式见 `src/bapu/corrections.py`。
- 整首歌回归测试：设置 `BAPU_REGRESSION_AUDIO` 和 `BAPU_REGRESSION_LYRICS` 后运行 `uv run pytest -m regression`。

模型权重不随项目分发，各自有不同的许可（见笔记的“许可”一节），第一次用到时下载到 `.cache/`，不提交到仓库。
SOME 的推理代码已移植进 `src/bapu/backends/some/`（MIT，保留了原作者的版权声明），不需要另外检出。

## 原则

- 歌词只来自用户提供的文件，工具不联网抓歌词。
- 处理他人作品前需要用户确认拥有相应权利；输出文件默认不提交。
- 自动判断不确定的地方必须进审查报告，由人来确认；人的修正要能在重跑时保留。
