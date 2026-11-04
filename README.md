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
uv run bapu run "<歌曲>.wav" --set assign.gap_pen=0.4   # 改参数只重跑这一步和之后的步骤
uv run bapu run "<歌曲>.wav" --model lead.dereverb_model=none
uv run bapu status work/<文件名>
uv run bapu corrections from-svp work/<文件名> "<工程>.svp"   # 把在 SV 里改过的地方读成修正
uv run bapu eval work/<文件名> "<工程>.svp" --lines 2,10      # 自动版本和 SV 里的版本逐句对比
uv run bapu export work/<文件名>                # 导出 MIDI（带歌词）、.svp 工程、粘贴文件到 export/
uv run bapu serve                            # 本机 API（http://127.0.0.1:8765/docs），给前端用
uv run pytest                                # 单元测试（只用虚构数据）
```

- 输出：`score.json`（乐谱）、`review.md` / `review.json`（审查报告，只有时间、编号和置信度）。
- `--set`、`--model` 保存在工作目录的 `params.json`；`--lyrics` 记在 `manifest.json`，下次可以省略。
- 修正写在工作目录的 `corrections.yaml`，按时间定位，重跑不会覆盖；格式见 `src/bapu/corrections.py`。
- 整首歌回归测试：设置 `BAPU_REGRESSION_AUDIO` 和 `BAPU_REGRESSION_LYRICS` 后运行 `uv run pytest -m regression`。

模型权重不随项目分发，各自有不同的许可（见笔记的“许可”一节）。本地缓存放在 `.cache/`，
SOME 的代码放在 `third_party/SOME`，两者都不提交到仓库。

## 原则

- 歌词只来自用户提供的文件，工具不联网抓歌词。
- 处理他人作品前需要用户确认拥有相应权利；输出文件默认不提交。
- 自动判断不确定的地方必须进审查报告，由人来确认；人的修正要能在重跑时保留。
