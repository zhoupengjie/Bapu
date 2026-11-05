//! First launch of the portable app: Bapu sets up its own Python inference
//! environment in `<home>\runtime\` with the bundled `tools\uv.exe`.
//!
//! 1. Python 3.12 (uv's standalone build, relocatable; not on PATH, not in the registry)
//! 2. Bapu's packages, pinned from uv.lock (`app\requirements.txt`, no dependency resolution)
//! 3. PyTorch for the chosen device (`app\torch.json`: CUDA, AMD ROCm, CPU, Intel XPU;
//!    on macOS Apple chips with Metal (MPS), or CPU, both PyPI's own build)
//! 4. a check that imports torch and Bapu and reports the device
//!
//! Packages go straight into that Python (no venv: a venv records absolute
//! paths, and the whole program folder should be movable). uv's download cache
//! lives in `runtime\.uv-cache` while installing and is removed only once the
//! install succeeded: a paused or failed install keeps what it downloaded, and
//! the next attempt skips those packages.

use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

static RUNNING: AtomicBool = AtomicBool::new(false);
static CANCELLED: AtomicBool = AtomicBool::new(false);
static CHILD: Mutex<Option<Child>> = Mutex::new(None);
static PAUSED: AtomicBool = AtomicBool::new(false);

pub fn running() -> bool {
    RUNNING.load(Ordering::SeqCst)
}

/// Stop the install to continue later (the 环境 page's 暂停): like cancel, reported as paused.
pub fn pause() {
    PAUSED.store(true, Ordering::SeqCst);
    cancel();
}

pub fn cancel() {
    CANCELLED.store(true, Ordering::SeqCst);
    if let Some(mut c) = CHILD.lock().unwrap().take() {
        let _ = c.kill();
    }
}

fn runtime(home: &Path) -> PathBuf {
    home.join("runtime")
}

/// The installed Python (`runtime\cpython-3.12.*\python.exe`), newest first.
pub fn runtime_python(home: &Path) -> Option<PathBuf> {
    let mut found: Vec<PathBuf> = fs::read_dir(runtime(home)).ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().starts_with("cpython-3.12")))
        .map(|p| if cfg!(windows) { p.join("python.exe") } else { p.join("bin").join("python3") })
        .filter(|p| p.exists())
        .collect();
    found.sort();
    found.pop()
}

pub fn ready(home: &Path) -> bool {
    runtime(home).join("ready.json").exists() && runtime_python(home).is_some()
}

/// A Windows directory junction (not reported as a symlink by std).
fn is_junction(p: &Path) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const REPARSE_POINT: u32 = 0x400;
        return fs::symlink_metadata(p).is_ok_and(|m| m.file_attributes() & REPARSE_POINT != 0);
    }
    #[allow(unreachable_code)]
    {
        let _ = p;
        false
    }
}

fn quiet(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

fn output(program: &str, args: &[&str]) -> Option<String> {
    let out = quiet(Command::new(program).args(args).stdin(Stdio::null())).output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).to_string())
}

/// Graphics adapters: NVIDIA ones from nvidia-smi (with the driver version), all from Windows.
fn gpus() -> (Vec<(String, Option<u32>)>, Vec<String>) {
    let nvidia = output("nvidia-smi", &["--query-gpu=name,driver_version", "--format=csv,noheader"])
        .map(|s| {
            s.lines()
                .filter_map(|l| {
                    let mut it = l.split(',').map(str::trim);
                    let name = it.next()?.to_string();
                    let major = it.next().and_then(|v| v.split('.').next()?.parse().ok());
                    (!name.is_empty()).then_some((name, major))
                })
                .collect()
        })
        .unwrap_or_default();
    let all = if cfg!(windows) {
        output("powershell", &["-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
            .map(|s| s.lines().map(str::trim).filter(|l| !l.is_empty()).map(str::to_string).collect())
            .unwrap_or_default()
    } else {
        Vec::new()
    };
    (nvidia, all)
}

/// The Mac's chip ("Apple M2 Pro"); None on other systems and on Intel Macs.
fn apple_chip() -> Option<String> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    output("/usr/sbin/sysctl", &["-n", "machdep.cpu.brand_string"])
        .map(|s| s.trim().to_string())
        .filter(|s| s.starts_with("Apple"))
}

/// Radeon cards (and Ryzen AI graphics) AMD's Windows PyTorch can run on: RDNA 3 / RDNA 4.
fn amd_supported(name: &str) -> bool {
    ["RX 9", "RX 7", "R9700", "W7900", "W7800", "W7700", "8060S", "8050S", "8040S", "890M", "880M"]
        .iter().any(|p| name.contains(p))
}

/// The ones on AMD's own list (ROCm 7.2.1 support matrix and release notes).
fn amd_listed(name: &str) -> bool {
    ["RX 9070", "RX 9060", "R9700", "RX 7900 XTX", "W7900", "RX 7700", "8060S", "8050S", "890M", "880M"]
        .iter().any(|p| name.contains(p))
}

/// Windows 11 is build 22000 or later (AMD's PyTorch is for Windows 11 only).
fn windows_11() -> bool {
    if !cfg!(windows) {
        return false;
    }
    output("cmd", &["/c", "ver"])
        .and_then(|v| v.rsplit('.').nth(1).and_then(|b| b.trim().parse::<u32>().ok()))
        .is_some_and(|build| build >= 22000)
}

/// The torch / torchaudio / torchvision pins of a variant (AMD's build brings its own versions).
fn pins(spec: &Value, variant: &str) -> Value {
    let own = &spec["variants"][variant]["packages"];
    if own.is_array() { own.clone() } else { spec["packages"].clone() }
}

fn torch_spec(home: &Path) -> Result<Value, String> {
    let p = home.join("app").join("torch.json");
    let text = fs::read_to_string(&p).map_err(|e| format!("读不了 {}：{e}", p.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} 格式不对：{e}", p.display()))
}

/// Environment the runtime's Python needs besides PYTHONPATH, for `variant` (None: the installed one).
/// A variant with a fixed `device` in torch.json gets BAPU_DEVICE (macOS 只用 CPU: the same PyPI
/// build as Metal, which would otherwise pick Metal). macOS: `app` is a link into the signed app
/// bundle, so bytecode goes to the runtime folder instead of next to the code (a file written into
/// the bundle would break its signature), and ops Metal lacks run on the CPU (read when torch is imported).
pub fn runtime_env(cmd: &mut Command, home: &Path, variant: Option<&str>) {
    let variant = variant.map(str::to_string).or_else(|| read_ready(home)?["variant"].as_str().map(str::to_string));
    let device = variant.and_then(|v| torch_spec(home).ok()?["variants"][v.as_str()]["device"].as_str().map(str::to_string));
    if let Some(d) = device.filter(|_| std::env::var_os("BAPU_DEVICE").is_none()) {
        cmd.env("BAPU_DEVICE", d);
    }
    if cfg!(target_os = "macos") {
        cmd.env("PYTHONPYCACHEPREFIX", runtime(home).join("pycache"))
            .env("PYTORCH_ENABLE_MPS_FALLBACK", "1");
    }
}

fn writable(dir: &Path) -> bool {
    let probe = dir.join(".write-test");
    let ok = fs::create_dir_all(dir).is_ok() && fs::write(&probe, b"ok").is_ok();
    let _ = fs::remove_file(&probe);
    ok
}

/// What the setup page shows: the detected GPUs and the PyTorch builds to choose from.
pub fn info(home: &Path) -> Value {
    let (nvidia, all) = gpus();
    let spec = torch_spec(home).unwrap_or(json!({}));
    let has_intel_arc = all.iter().any(|n| n.contains("Intel") && (n.contains("Arc") || n.contains("Iris Xe")));
    let amd: Vec<&String> = all.iter().filter(|n| n.contains("AMD") || n.contains("Radeon")).collect();
    let has_amd = !amd.is_empty();
    let amd_ok = amd.iter().any(|n| amd_supported(n));
    let amd_on_list = amd.iter().any(|n| amd_listed(n));
    let win11 = windows_11();
    let driver_ok = nvidia.iter().any(|(_, d)| d.is_some_and(|d| d >= 570));
    let apple = apple_chip();
    let recommended = if cfg!(target_os = "macos") {
        if apple.is_some() { "mps" } else { "cpu" }
    } else if driver_ok { "cuda" } else if amd_ok && win11 { "rocm" } else if has_intel_arc { "xpu" } else { "cpu" };
    let mut variants = Vec::new();
    for (id, v) in spec["variants"].as_object().cloned().unwrap_or_default() {
        let (available, note) = match id.as_str() {
            "mps" if apple.is_none() => (false, "只在 Apple 芯片的 Mac 上可用".to_string()),
            "cuda" if nvidia.is_empty() => (false, "没有检测到 NVIDIA 显卡".to_string()),
            "cuda" if !driver_ok => (true, "显卡驱动版本低于 570，CUDA 12.8 可能用不了，请先更新驱动".to_string()),
            "xpu" if !has_intel_arc => (false, "没有检测到 Intel Arc 显卡".to_string()),
            "rocm" if !has_amd => (false, "没有检测到 AMD 显卡".to_string()),
            "rocm" if !amd_ok => (false, "这张 AMD 显卡还用不了：AMD 的 Windows 版 PyTorch 只支持 RX 7000 / 9000 系列".to_string()),
            "rocm" if !win11 => (false, "AMD 的 Windows 版 PyTorch 需要 Windows 11".to_string()),
            "rocm" if !amd_on_list => (true, "这张显卡不在 AMD 官方的支持列表里，可能用不了".to_string()),
            _ => (true, String::new()),
        };
        variants.push(json!({
            "id": id, "label": v["label"], "detail": v["detail"], "size_gb": v["size_gb"],
            "experimental": v["experimental"], "available": available, "note": note,
            "recommended": id == recommended,
        }));
    }
    json!({
        "home": home.display().to_string(),
        "writable": writable(&runtime(home)),
        "nvidia": nvidia.iter().map(|(n, d)| json!({"name": n, "driver": d})).collect::<Vec<_>>(),
        "adapters": all,
        "amd": has_amd,
        "amd_supported": amd_ok && win11,
        "apple": apple,
        "variants": variants,
        "recommended": recommended,
        "sources": mirrors(home).map(|m| json!({
            "official": { "label": m["official"]["label"], "detail": m["official"]["detail"] },
            "china": { "label": m["china"]["label"], "detail": m["china"]["detail"] },
        })).unwrap_or(json!({})),
        "settings": fs::read(home.join("data").join("settings.json")).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()),
        "previous": read_ready(home),
        "models": models(home),
        "threads": threads(home),
        // a paused or failed install left packages in uv's cache: the next one skips them
        "partial": runtime(home).join(".uv-cache").is_dir() && !ready(home),
    })
}

/// The models in the bundled registry (id, stage, name, bytes), so the 环境 page can
/// list them before the Python service runs.
fn models(home: &Path) -> Value {
    let p = home.join("app").join("src").join("bapu").join("models").join("registry.toml");
    let Some(t) = fs::read_to_string(&p).ok().and_then(|s| s.parse::<toml::Table>().ok()) else { return json!([]) };
    let list = t.get("model").and_then(|m| m.as_array()).cloned().unwrap_or_default();
    Value::Array(list.iter().map(|m| {
        let files = m.get("files").and_then(|f| f.as_array()).cloned().unwrap_or_default();
        let size = m.get("archive").and_then(|a| a.get("size")).and_then(|s| s.as_integer())
            .unwrap_or_else(|| files.iter().filter_map(|f| f.get("size").and_then(|s| s.as_integer())).sum());
        let s = |k: &str| m.get(k).and_then(|v| v.as_str()).unwrap_or_default().to_string();
        let lic = |k: &str| m.get("license").and_then(|l| l.get(k)).and_then(|v| v.as_str()).unwrap_or_default().to_string();
        json!({ "id": s("id"), "stage": s("stage"), "name": s("name"), "size": size, "builtin": files.is_empty(),
                "license": { "name": lic("name"), "url": lic("url"), "commercial": lic("commercial") } })
    }).collect())
}

/// Parallel downloads (the 环境 page's 下载线程), shared with the Python side.
fn threads(home: &Path) -> u64 {
    fs::read(home.join("data").join("settings.json")).ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|s| s["threads"].as_u64())
        .unwrap_or(8)
        .clamp(1, 16)
}

struct Ctx {
    app: AppHandle,
    home: PathBuf,
    log: Arc<Mutex<fs::File>>,
}

impl Ctx {
    fn line(&self, text: &str) {
        let _ = writeln!(self.log.lock().unwrap(), "{text}");
        let _ = self.app.emit("setup-log", text);
    }

    fn step(&self, index: usize, total: usize, title: &str) {
        self.line(&format!("== {index}/{total} {title}"));
        let _ = self.app.emit("setup-step", json!({ "index": index, "total": total, "title": title }));
    }

    fn uv(&self) -> Command {
        let rt = runtime(&self.home);
        let mut c = Command::new(self.home.join("tools").join(if cfg!(windows) { "uv.exe" } else { "uv" }));
        c.env("UV_CACHE_DIR", rt.join(".uv-cache"))
            .env("UV_PYTHON_INSTALL_DIR", &rt)
            .env("UV_PYTHON_INSTALL_REGISTRY", "0")
            .env("UV_PYTHON_PREFERENCE", "only-managed")
            .env("UV_NO_CONFIG", "1")
            .env("UV_LINK_MODE", "copy")
            .env("NO_COLOR", "1")
            .env("UV_CONCURRENT_DOWNLOADS", threads(&self.home).to_string())
            .current_dir(&self.home);
        c
    }

    /// Run a command, streaming its output to the page and the log.
    fn run(&self, cmd: &mut Command) -> Result<Vec<String>, String> {
        if CANCELLED.load(Ordering::SeqCst) {
            return Err("已取消".into());
        }
        let mut child = quiet(cmd).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .spawn().map_err(|e| format!("启动不了 {:?}：{e}", cmd.get_program()))?;
        let out = child.stdout.take().unwrap();
        let err = child.stderr.take().unwrap();
        *CHILD.lock().unwrap() = Some(child);
        let lines = Arc::new(Mutex::new(Vec::new()));
        let pump = |r: Box<dyn std::io::Read + Send>, ctx: (AppHandle, Arc<Mutex<fs::File>>, Arc<Mutex<Vec<String>>>)| {
            thread::spawn(move || {
                for l in BufReader::new(r).lines().map_while(Result::ok) {
                    let _ = writeln!(ctx.1.lock().unwrap(), "{l}");
                    let _ = ctx.0.emit("setup-log", &l);
                    ctx.2.lock().unwrap().push(l);
                }
            })
        };
        let t1 = pump(Box::new(out), (self.app.clone(), self.log.clone(), lines.clone()));
        let t2 = pump(Box::new(err), (self.app.clone(), self.log.clone(), lines.clone()));
        let _ = t1.join();
        let _ = t2.join();
        let status = CHILD.lock().unwrap().take().map(|mut c| c.wait());
        if CANCELLED.load(Ordering::SeqCst) {
            return Err("已取消".into());
        }
        match status {
            Some(Ok(s)) if s.success() => Ok(lines.lock().unwrap().clone()),
            Some(Ok(s)) => Err(format!("{:?} 失败（{s}）", cmd.get_program())),
            _ => Err("安装进程异常退出".into()),
        }
    }
}

fn mirrors(home: &Path) -> Result<Value, String> {
    let p = home.join("app").join("src").join("bapu").join("mirrors.json");
    let text = fs::read_to_string(&p).map_err(|e| format!("读不了 {}：{e}", p.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} 格式不对：{e}", p.display()))
}

fn sha256_file(p: &Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    let data = fs::read(p).ok()?;
    Some(format!("{:x}", Sha256::digest(&data)))
}

fn read_ready(home: &Path) -> Option<Value> {
    serde_json::from_slice(&fs::read(runtime(home).join("ready.json")).ok()?).ok()
}

/// After an update: the package list or the PyTorch pins changed since the runtime was set up.
pub fn needs_sync(home: &Path) -> bool {
    let Some(ready) = read_ready(home) else { return false };
    let req = sha256_file(&home.join("app").join("requirements.txt"));
    let variant = ready["variant"].as_str().unwrap_or("cpu").to_string();
    let pins = torch_spec(home).ok().map(|s| pins(&s, &variant));
    let pins_changed = ready.get("torch").is_some() && pins.as_ref() != Some(&ready["torch"]);
    req.is_some() && (ready["requirements_sha"].as_str() != req.as_deref() || pins_changed)
}

/// The settings file the Python side reads (download source etc.).
pub fn save_settings(home: &Path, changes: Value) {
    let p = home.join("data").join("settings.json");
    let mut s: Value = fs::read(&p).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(json!({}));
    if let (Some(obj), Some(ch)) = (s.as_object_mut(), changes.as_object()) {
        for (k, v) in ch {
            obj.insert(k.clone(), v.clone());
        }
    }
    let _ = fs::create_dir_all(p.parent().unwrap());
    let _ = fs::write(&p, serde_json::to_vec_pretty(&s).unwrap());
}

/// Bytes per second for the first 512 KB of `url` (None: unreachable).
fn speed(url: &str) -> Option<f64> {
    let t = std::time::Instant::now();
    let r = ureq::get(url).set("Range", "bytes=0-524287").timeout(std::time::Duration::from_secs(8)).call().ok()?;
    let mut buf = Vec::new();
    use std::io::Read;
    r.into_reader().take(524_288).read_to_end(&mut buf).ok()?;
    let dt = t.elapsed().as_secs_f64().max(1e-3);
    (!buf.is_empty()).then(|| buf.len() as f64 / dt)
}

/// Official servers vs China mirrors: which is faster from here.
pub fn probe(home: &Path) -> Value {
    let m = mirrors(home).unwrap_or(json!({}));
    // macOS gets PyTorch from PyPI, so it times a PyPI file there
    let probe = if cfg!(target_os = "macos") && m["probe_macos"].is_object() { &m["probe_macos"] } else { &m["probe"] };
    let (o, c) = thread::scope(|s| {
        let a = s.spawn(|| speed(probe["official"].as_str().unwrap_or_default()));
        let b = s.spawn(|| speed(probe["china"].as_str().unwrap_or_default()));
        (a.join().ok().flatten(), b.join().ok().flatten())
    });
    let best = match (o, c) {
        (_, Some(c)) if o.map_or(true, |o| c > o * 1.2) => "china",
        _ => "official",
    };
    json!({ "official": o, "china": c, "best": best })
}

fn steps(ctx: &Ctx, variant: &str, source: &str, sync: bool) -> Result<Value, String> {
    let home = &ctx.home;
    let spec = torch_spec(home)?;
    let mirror = mirrors(home)?;
    let src = mirror.get(source).ok_or_else(|| format!("没有这种下载源：{source}"))?;
    let v = spec["variants"].get(variant).ok_or_else(|| format!("没有这种安装方式：{variant}"))?;
    let py_version = spec["python"].as_str().unwrap_or("3.12");
    let before = read_ready(home);
    // a runtime set up before ready.json recorded the pins came from the same pins: leave torch alone
    let torch_changed = !sync || before.as_ref().map_or(true, |b| {
        (b.get("torch").is_some() && b["torch"] != pins(&spec, variant)) || b["variant"].as_str() != Some(variant)
    });
    let total = 4;
    let uv = || {
        let mut c = ctx.uv();
        if let Some(m) = src["python"].as_str() {
            c.env("UV_PYTHON_INSTALL_MIRROR", m);
        }
        c
    };
    ctx.line(&format!("== 下载源：{}", src["label"].as_str().unwrap_or(source)));

    ctx.step(1, total, &format!("安装 Python {py_version}"));
    if sync && runtime_python(home).is_some() {
        ctx.line("Python 已经装好，跳过");
    } else {
        // --no-registry: Windows only (elsewhere there is no registry; UV_PYTHON_INSTALL_REGISTRY=0 as well)
        let mut cmd = uv();
        cmd.args(["python", "install", py_version, "--no-bin"]);
        if cfg!(windows) {
            cmd.arg("--no-registry");
        }
        ctx.run(&mut cmd)?;
    }
    // uv also makes `cpython-3.12-…`, a link to the patch release by absolute path; it would
    // dangle once the program folder moves, and Bapu does not need it
    if let Ok(entries) = fs::read_dir(runtime(home)) {
        for e in entries.flatten() {
            let p = e.path();
            if fs::symlink_metadata(&p).is_ok_and(|m| m.file_type().is_symlink() || is_junction(&p)) {
                // a directory link / junction on Windows; a symlink elsewhere is unlinked like a file
                let _ = if cfg!(windows) { fs::remove_dir(&p) } else { fs::remove_file(&p) };
            }
        }
    }
    let py = runtime_python(home).ok_or("Python 装好了却找不到 python.exe")?;
    let py_s = py.display().to_string();
    let pypi = src["pypi"].as_str().unwrap_or("https://pypi.org/simple");

    ctx.step(2, total, if sync { "更新 Bapu 用到的包" } else { "安装 Bapu 用到的包" });
    let req = home.join("app").join("requirements.txt");
    ctx.run(uv().args(["pip", "install", "--python", &py_s, "--break-system-packages", "--no-deps", "--index-url", pypi, "-r"]).arg(&req))?;

    ctx.step(3, total, &format!("安装 PyTorch（{}）", v["label"].as_str().unwrap_or(variant)));
    if torch_changed && v["wheels"].is_array() {
        // AMD's build: direct links (no mirror), the ROCm SDK first, then torch; the SDK's small
        // `rocm` source package is built with setuptools from the package index
        for group in v["wheels"].as_array().cloned().unwrap_or_default() {
            let mut cmd = uv();
            cmd.args(["pip", "install", "--python", &py_s, "--break-system-packages", "--no-deps", "--index-url", pypi,
                      "--reinstall-package", "torch"]);
            for url in group.as_array().cloned().unwrap_or_default() {
                cmd.arg(url.as_str().unwrap_or_default());
            }
            ctx.run(&mut cmd)?;
        }
    } else if torch_changed && v["from_pypi"].as_bool().unwrap_or(false) {
        // macOS: the package index's own build (PyPI's wheels for Apple chips include Metal);
        // the China source's PyPI mirror has the same files
        let mut cmd = uv();
        cmd.args(["pip", "install", "--python", &py_s, "--break-system-packages", "--reinstall-package", "torch",
                  "--no-deps", "--index-url", pypi]);
        for p in pins(&spec, variant).as_array().cloned().unwrap_or_default() {
            cmd.arg(p.as_str().unwrap_or_default());
        }
        ctx.run(&mut cmd)?;
    } else if torch_changed {
        let torch = &src["torch"];
        let url = torch[variant].as_str().ok_or_else(|| format!("{source} 没有 {variant} 的 PyTorch"))?;
        let mut cmd = uv();
        cmd.args(["pip", "install", "--python", &py_s, "--break-system-packages", "--reinstall-package", "torch"]);
        for p in pins(&spec, variant).as_array().cloned().unwrap_or_default() {
            cmd.arg(p.as_str().unwrap_or_default());
        }
        let deps = v["deps"].as_bool().unwrap_or(false);
        match (torch["mode"].as_str(), deps) {
            (Some("find-links"), false) => cmd.args(["--no-index", "--find-links", url, "--no-deps"]),
            (Some("find-links"), true) => cmd.args(["--index-url", pypi, "--find-links", url]),
            (_, false) => cmd.args(["--index-url", url, "--no-deps"]),
            (_, true) => cmd.args(["--index-url", url, "--extra-index-url", pypi, "--index-strategy", "unsafe-best-match"]),
        };
        ctx.run(&mut cmd)?;
    } else {
        ctx.line("PyTorch 版本没变，跳过");
    }

    ctx.step(4, total, "检查运行环境");
    // Importing torch to describe the device takes a few seconds. After an update that left
    // PyTorch alone the answer cannot have changed, so the one recorded at install time is kept;
    // the service start that follows is the check that the packages import.
    let kept = before.as_ref().filter(|_| !torch_changed).map(|b| b["device"].clone()).filter(|d| d.is_object());
    let dev: Value = if let Some(d) = kept {
        ctx.line("PyTorch 没动，沿用上次的检查结果");
        d
    } else {
        let check = "import json, torch, bapu.cli\nfrom bapu.device import describe\nprint('BAPU_CHECK ' + json.dumps(describe()))";
        let mut cmd = Command::new(&py);
        cmd.args(["-c", check])
            .env("PYTHONPATH", home.join("app").join("src")).env("PYTHONNOUSERSITE", "1").env("PYTHONUTF8", "1")
            .current_dir(home);
        runtime_env(&mut cmd, home, Some(variant));
        let lines = ctx.run(&mut cmd)?;
        lines.iter().find_map(|l| l.strip_prefix("BAPU_CHECK ")).and_then(|j| serde_json::from_str(j).ok())
            .ok_or("检查没有返回结果")?
    };
    let ready = json!({
        "variant": variant, "label": v["label"], "python": py_version, "device": dev, "source": source,
        "torch": pins(&spec, variant), "requirements_sha": sha256_file(&req),
        "installed_at": before.as_ref().and_then(|b| b["installed_at"].as_u64())
            .unwrap_or_else(|| std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)),
    });
    fs::write(runtime(home).join("ready.json"), serde_json::to_vec_pretty(&ready).unwrap()).map_err(|e| e.to_string())?;
    let _ = fs::remove_dir_all(runtime(home).join(".uv-cache"));
    Ok(ready)
}

/// Install (or, after an update, sync) in the background; the page follows
/// setup-step / setup-log / setup-done / setup-error. `source` is official or
/// china; `choice` is what the user picked (auto / official / china).
pub fn install(app: AppHandle, home: PathBuf, variant: String, source: String, choice: String, measured: Value) -> Result<(), String> {
    if RUNNING.swap(true, Ordering::SeqCst) {
        return Err("已经在安装了".into());
    }
    CANCELLED.store(false, Ordering::SeqCst);
    PAUSED.store(false, Ordering::SeqCst);
    let sync = read_ready(&home).is_some();
    save_settings(&home, json!({ "source": choice, "resolved": source, "measured": measured }));
    let log_path = home.join("data").join("logs").join("setup.log");
    let _ = fs::create_dir_all(log_path.parent().unwrap());
    let log = OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| {
        RUNNING.store(false, Ordering::SeqCst);
        format!("写不了 {}：{e}（程序文件夹要有写入权限）", log_path.display())
    })?;
    let ctx = Ctx { app: app.clone(), home, log: Arc::new(Mutex::new(log)) };
    thread::spawn(move || {
        let result = steps(&ctx, &variant, &source, sync);
        RUNNING.store(false, Ordering::SeqCst);
        match result {
            Ok(ready) => {
                ctx.line("== 完成");
                let _ = ctx.app.emit("setup-done", ready);
                crate::start_once(&ctx.app);
            }
            Err(_) if PAUSED.load(Ordering::SeqCst) => {
                ctx.line("== 已暂停（下载过的包留在缓存里，继续时跳过）");
                let _ = ctx.app.emit("setup-paused", ());
                crate::set_status_failed_setup(&ctx.app);
            }
            Err(e) => {
                ctx.line(&format!("== 失败：{e}"));
                let _ = ctx.app.emit("setup-error", json!({ "message": e, "log": log_path.display().to_string() }));
                crate::set_status_failed_setup(&ctx.app);
            }
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn radeon_cards_for_amds_pytorch() {
        for ok in ["AMD Radeon RX 9070 XT", "AMD Radeon RX 7900 XTX", "AMD Radeon RX 7800 XT", "AMD Radeon AI PRO R9700",
                   "AMD Radeon PRO W7900", "AMD Radeon(TM) 8060S Graphics", "AMD Radeon(TM) 890M Graphics"] {
            assert!(amd_supported(ok), "{ok}");
        }
        for no in ["AMD Radeon RX 6800 XT", "AMD Radeon RX 580 Series", "AMD Radeon(TM) Graphics", "AMD Radeon RX 5700 XT"] {
            assert!(!amd_supported(no), "{no}");
        }
        assert!(amd_listed("AMD Radeon RX 9070 XT") && !amd_listed("AMD Radeon RX 7800 XT"));
    }

    #[test]
    fn rocm_brings_its_own_torch_pins() {
        let spec = json!({ "packages": ["torch==2.11.0"], "variants": { "cuda": {}, "rocm": { "packages": ["torch==2.9.1"] } } });
        assert_eq!(pins(&spec, "cuda"), json!(["torch==2.11.0"]));
        assert_eq!(pins(&spec, "rocm"), json!(["torch==2.9.1"]));
    }
}
