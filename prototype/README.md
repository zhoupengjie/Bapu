# 原型脚本

首个端到端试验里实际跑过的脚本，按原样保存，留作重构成 `src/bapu/` 的参考。
脚本之间用命令行参数传路径，没有统一的配置。用法见各脚本开头的 docstring。
所有脚本都只输出计数、时间和音符编号，不打印歌词文字。

用 `uv run python -I prototype/<脚本> ...` 运行。缓存路径：
audio-separator 模型目录 `.cache/models/audio-separator`，SOME 的 checkpoint 在
`.cache/models/some/0119_continuous256_5spk/`，SOME 代码在 `third_party/SOME`，
MMS 对齐模型需要设置 `TORCH_HOME=.cache/torch`。

| 顺序 | 脚本 | 作用 |
|---|---|---|
| 1 | `separate.py` | BS-RoFormer 分离人声和伴奏 |
| 1a | `check.py`、`fitgain.py` | 检查分离结果能否还原原曲（补回归一化增益后的 SNR） |
| 2 | `stage.py` | 运行任意一个 audio-separator 模型，并关闭归一化（用于 Karaoke 和去混响） |
| 2a | `check_chain.py` | 检查主唱 / 和声 / 去混响能否还原输入，并列出和声密集的时间段 |
| 3 | `tempo.py` | 从伴奏估算拍速和拍点 |
| 4 | `some_infer.py` | 用 SOME 转出 MIDI，同时导出带小数音高的 JSON |
| 4a | `check_midi.py` | 检查 MIDI：重叠、音域、时长、离网格的距离 |
| 5 | `split_check.py` | 按起音证据列出可能被切开的音符组 |
| 6 | `ass_parse.py` | 解析 `.ass` 字幕得到带时间的歌词行（只输出计数） |
| 7 | `align_lyrics.py` | 按字幕时间把音符分到每一句，比较音节数和音符数 |
| 8a | `build_fill.py` → `export_paste.py` | v1：用起音规则放 `-`，生成可粘贴的歌词文件 |
| 8b | `force_align.py` → `merge_plans.py` | v2：MMS 强制对齐，按置信度和人工版本合并（推荐） |

`force_align.py`、`merge_plans.py` 等脚本读取的 SV 音符数据，是 synthv-agent-bridge 的
`get_track_notes`（`dense: always`）返回的 JSON。
