// Translations of the Simplified Chinese source strings (see i18n.ts): the 自动处理 page and step titles.
export default {
  en: {
    "人声分离": "Vocal separation", "提取主唱": "Lead vocal", "测速度": "Tempo", "转音符": "Notes", "合并音符": "Merge notes",
    "听写歌词": "Dictation", "整理歌词": "Lyrics", "对齐时间": "Alignment", "填字到音符": "Assign lyrics",
    "分离": "Separation", "主唱": "Lead vocal", "拍速": "Tempo", "转谱": "Transcription", "合并切开的音符": "Merge split notes",
    "听写": "Dictation", "歌词（听写 + 参考）": "Lyrics (dictation + reference)", "对齐": "Alignment", "分配字到音符": "Assign lyrics to notes",
    "用了 {n} 秒": "Took {n} s", "用了 {n} 分钟": "Took {n} min",
    "排队中…": "Queued…", "正在{step}…": "{step}…", "准备中…": "Preparing…", "处理完成": "Done",
    "处理出错了": "Processing failed", "已停止": "Stopped", "还没开始": "Not started", "总进度": "Overall progress",
    "还缺模型": "Models missing", "去环境": "Go to Setup", "停止": "Stop", "去检查": "Check results",
    "继续处理": "Continue", "开始处理": "Start",
    "已跳过，用的是你给的音符": "skipped: using the notes you gave",
    "音符来自": "Notes from", "{n} 个音符": "{n} notes", "第 {n} 轨": "track {n}",
    "改回自动转谱": "Back to automatic transcription",
    "自动转谱。也可以用 SV 工程里的音符，比如 SV2 从主唱干声提取的": "Automatic transcription. Or use the notes of an SV project, e.g. ones SV2 extracted from the dry lead",
    "这个工程里有几条音轨都有音符，选一条": "Several tracks in this project have notes; pick one",
    "（没有名字）": "(unnamed)", "用这条": "Use this track",
    "导出的 .svp 里带着主唱干声轨。在 SV2 里对它提取音高、保存，再把那个工程选进来：转谱会跳过，从合并往后重算。":
      "The exported .svp carries the dry-lead track. Extract the pitch from it in SV2, save, then choose that project here: transcription is skipped and everything from merging on is redone.",
    "需要音符文件：上传或给出 notes_path": "A notes file is needed", "这个工程里没有音符": "This project has no notes",
    "音符文件要是 .svp 或 .mid": "The notes file must be .svp or .mid", "这个 MIDI 里没有音符": "This MIDI has no notes",
  } as Record<string, string>,
};
