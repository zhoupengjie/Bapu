// Translations of the Simplified Chinese source strings (see i18n.ts): the 环境 step, with the
// step labels, the desktop shell's device choices, install steps and messages that reach it.
export default {
  en: {
    // checklist rows and their states
    "Python 环境": "Python runtime", "准备中": "Preparing", "第 {i}/{n} 步 · {title}": "Step {i}/{n} · {title}",
    "启动中": "Starting", "没装好": "Install failed", "可续传 · {size} GB": "Resumable · {size} GB", "可续传": "Resumable",
    "缺少": "Missing", "{part} · 已暂停": "{part} · Paused", "等待": "Waiting", "{part} · 可续传": "{part} · Resumable",
    "最新版": "Up to date", "关闭后换上": "Installs on close", "有新版本": "New version",
    // step labels (STAGE_LABEL in env.ts)
    "人声分离": "Vocal separation", "提取主唱": "Lead vocal", "合并音符": "Merge notes", "主唱提取": "Lead vocal", "去混响": "De-reverb",
    "测速度": "Tempo", "转音符": "Notes", "听写歌词": "Dictation", "对齐时间": "Alignment", "整理歌词": "Lyrics",
    "填字到音符": "Assign lyrics",
    // the hero
    "正在检查": "Checking", "还要约 {min} 分钟 · {speed}": "About {min} min left · {speed}", "马上就好": "Almost ready",
    "已下 {got} / {total} GB": "{got} / {total} GB downloaded", "正在启动": "Starting", "正在下载": "Downloading",
    "暂停": "Pause", "已暂停": "Paused", "已下 {got} / {total} GB，继续时接着下": "{got} / {total} GB downloaded; resuming picks up from here",
    "继续": "Resume", "Bapu 有新版本": "New version of Bapu", "关闭 Bapu 时自动换上": "Installs when you close Bapu", "更新": "Update",
    "环境就绪": "Setup complete", "用 {device} 推理": "Running on {device}", "下一步": "Next", "重试": "Retry",
    "继续下载": "Resume download", "下载": "Download", "还缺 {n} 项": "{n} items missing", "还要下 {size} GB": "{size} GB to download",
    // settings rows
    "收起": "Hide", "查看详情": "Details", "推理设备": "Device", "更改": "Change", "推荐": "Recommended", "约 {size} GB": "about {size} GB",
    "用这个重新安装": "Reinstall with this", "用这个安装": "Install with this", "下载源": "Download source", "自动": "Auto", "官方源": "Official",
    "国内镜像": "China mirror", "下载线程": "Download threads", "下载线程数": "Number of download threads", "减少": "Fewer", "增加": "More",
    "下载即表示同意各模型的许可证": "Downloading means you accept each model's license", "查看": "View", "{name}：": "{name}: ", "不可商用": "non-commercial only",
    // the AMD notice
    "检测到 AMD 显卡，但它还不能用来推理（{why}），只能用 CPU，会慢很多。": "An AMD graphics card was found, but it can't be used for inference yet ({why}), so Bapu runs on the CPU, which is much slower.",
    // device choices from the desktop shell (labels after plain(), notes)
    "NVIDIA 显卡": "NVIDIA GPU", "AMD 显卡": "AMD GPU", "Intel Arc 显卡": "Intel Arc GPU", "只用 CPU": "CPU only",
    "没有检测到 NVIDIA 显卡": "No NVIDIA GPU found",
    "显卡驱动版本低于 570，CUDA 12.8 可能用不了，请先更新驱动": "The graphics driver is older than 570, so CUDA 12.8 may not work. Update the driver first.",
    "没有检测到 Intel Arc 显卡": "No Intel Arc GPU found", "没有检测到 AMD 显卡": "No AMD GPU found",
    "这张 AMD 显卡还用不了：AMD 的 Windows 版 PyTorch 只支持 RX 7000 / 9000 系列": "This AMD GPU can't be used yet: AMD's PyTorch for Windows only supports the RX 7000 / 9000 series",
    "AMD 的 Windows 版 PyTorch 只支持 RX 7000 / 9000 系列": "AMD's PyTorch for Windows only supports the RX 7000 / 9000 series",
    "AMD 的 Windows 版 PyTorch 需要 Windows 11": "AMD's PyTorch for Windows needs Windows 11",
    "这张显卡不在 AMD 官方的支持列表里，可能用不了": "This GPU isn't on AMD's official support list and may not work",
    "没有安装 torch": "torch is not installed",
    // install steps from the desktop shell
    "安装 Python": "Install Python", "安装 Bapu 用到的包": "Install Bapu's packages", "更新 Bapu 用到的包": "Update Bapu's packages",
    "安装 PyTorch": "Install PyTorch", "检查运行环境": "Check the runtime",
    // messages from the desktop shell
    "启动失败": "Failed to start", "已经在安装了": "Already installing", "已取消": "Cancelled", "安装进程异常退出": "The installer quit unexpectedly",
    "检查没有返回结果": "The check returned no result", "运行环境还没有装好": "The runtime isn't installed yet", "Bapu 服务一直没有响应": "The Bapu service isn't responding",
    "开发版用 uv sync 准备环境": "Development builds are set up with uv sync",
    "有新版本 {v}，去下载": "Version {v} available — download",
    "Apple 芯片": "Apple chip",
    "只在 Apple 芯片的 Mac 上可用": "Only on Macs with Apple chips",
    "M1 及以后的 Mac，需要 macOS 14 以上；还没在 Mac 上测过": "Macs with M1 or later, macOS 14 or newer; not tested on a Mac yet",
  } as Record<string, string>,
};
