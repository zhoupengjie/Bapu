# Claude Code guidance

Bapu turns a song recording plus user-supplied lyrics into a vocal score with
lyrics (MIDI and a SynthV `.svp` project). Read `docs/pipeline-notes.md` first:
it records the validated pipeline, parameters, measured results and pitfalls.
`prototype/` holds the working scripts from the first end-to-end run.

## Rules

- Lyrics come only from files the user supplies. Never fetch lyrics from the
  web or transcribe them from video, and never print lyric text to logs, chat or
  reports; report line numbers, note indices, counts and times instead.
- Never commit model weights, audio, lyrics, or generated projects. Weights live
  in `.cache/` (gitignored) and are downloaded on first use with their license
  shown; `third_party/SOME` is an external checkout.
- Uncertain automatic decisions (merged notes, `-` sustain placement, low
  alignment confidence) must surface in the review report with times. User
  corrections must persist across reruns.
- Keep each stage's output on disk so a parameter change only reruns later
  stages. Keep models swappable per stage.
- Use 1-based note and line indices in anything shown to the user.

## Environment

- `uv sync`; Python 3.12; torch/torchaudio/torchvision 2.11 / 0.26 from the
  CUDA 12.8 index (RTX 50-series). Resolution is limited to win32 and
  linux x86_64 in `pyproject.toml`.
- Set `TORCH_HOME=.cache/torch` for the MMS aligner, pass
  `.cache/models/audio-separator` as audio-separator's model dir, and use
  `.cache/models/some/0119_continuous256_5spk/` for SOME.
- torchaudio 2.11 `load()` needs torchcodec; read audio with soundfile + soxr.

## Related project

`D:\Projects\test_project\synthv-agent-bridge` is a separate, network-free MCP
Runtime for a running SynthV instance. Bapu may hand results to it (MIDI with
lyric events via `import_monophonic_score`), but Bapu code must not live there.
