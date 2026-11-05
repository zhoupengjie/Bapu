//! First launch of the portable app: Bapu sets up its own Python inference
//! environment in `<home>\runtime\` with the bundled `tools\uv.exe`.
//!
//! 1. Python 3.12 (uv's standalone build, relocatable; not on PATH, not in the registry)
//! 2. Bapu's packages, pinned from uv.lock (`app\requirements.txt`, no dependency resolution)
//! 3. PyTorch for the chosen device (`app\torch.json`: CUDA, CPU, Intel XPU)
//! 4. a check that imports torch and Bapu and reports the device
//!
//! Packages go straight into that Python (no venv: a venv records absolute
//! paths, and the whole program folder should be movable). uv's download cache
//! lives in `runtime\.uv-cache` while installing and is removed afterwards.

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

pub fn running() -> bool {
    RUNNING.load(Ordering::SeqCst)
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
    false
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

fn torch_spec(home: &Path) -> Result<Value, String> {
    let p = home.join("app").join("torch.json");
    let text = fs::read_to_string(&p).map_err(|e| format!("读不了 {}：{e}", p.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} 格式不对：{e}", p.display()))
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
    let has_amd = all.iter().any(|n| n.contains("AMD") || n.contains("Radeon"));
    let driver_ok = nvidia.iter().any(|(_, d)| d.is_some_and(|d| d >= 570));
    let recommended = if driver_ok { "cuda" } else if has_intel_arc { "xpu" } else { "cpu" };
    let mut variants = Vec::new();
    for (id, v) in spec["variants"].as_object().cloned().unwrap_or_default() {
        let (available, note) = match id.as_str() {
            "cuda" if nvidia.is_empty() => (false, "没有检测到 NVIDIA 显卡".to_string()),
            "cuda" if !driver_ok => (true, "显卡驱动版本低于 570，CUDA 12.8 可能用不了，请先更新驱动".to_string()),
            "xpu" if !has_intel_arc => (false, "没有检测到 Intel Arc 显卡".to_string()),
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
        "variants": variants,
        "recommended": recommended,
    })
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

fn steps(ctx: &Ctx, variant: &str) -> Result<Value, String> {
    let home = &ctx.home;
    let spec = torch_spec(home)?;
    let v = spec["variants"].get(variant).ok_or_else(|| format!("没有这种安装方式：{variant}"))?;
    let py_version = spec["python"].as_str().unwrap_or("3.12");
    let total = 4;

    ctx.step(1, total, &format!("安装 Python {py_version}"));
    ctx.run(ctx.uv().args(["python", "install", py_version, "--no-bin", "--no-registry"]))?;
    // uv also makes `cpython-3.12-…`, a link to the patch release by absolute path; it would
    // dangle once the program folder moves, and Bapu does not need it
    if let Ok(entries) = fs::read_dir(runtime(home)) {
        for e in entries.flatten() {
            let p = e.path();
            if fs::symlink_metadata(&p).is_ok_and(|m| m.file_type().is_symlink() || is_junction(&p)) {
                let _ = fs::remove_dir(&p);
            }
        }
    }
    let py = runtime_python(home).ok_or("Python 装好了却找不到 python.exe")?;
    let py_s = py.display().to_string();

    ctx.step(2, total, "安装 Bapu 用到的包");
    let req = home.join("app").join("requirements.txt");
    ctx.run(ctx.uv().args(["pip", "install", "--python", &py_s, "--break-system-packages", "--no-deps", "-r"]).arg(&req))?;

    ctx.step(3, total, &format!("安装 PyTorch（{}）", v["label"].as_str().unwrap_or(variant)));
    let mut cmd = ctx.uv();
    cmd.args(["pip", "install", "--python", &py_s, "--break-system-packages", "--reinstall-package", "torch"]);
    for p in spec["packages"].as_array().cloned().unwrap_or_default() {
        cmd.arg(p.as_str().unwrap_or_default());
    }
    cmd.args(["--index-url", v["index"].as_str().unwrap_or_default()]);
    if v["deps"].as_bool().unwrap_or(false) {
        cmd.args(["--extra-index-url", "https://pypi.org/simple", "--index-strategy", "unsafe-best-match"]);
    } else {
        cmd.arg("--no-deps");
    }
    ctx.run(&mut cmd)?;

    ctx.step(4, total, "检查运行环境");
    let check = "import json, torch, bapu.cli\nfrom bapu.device import describe\nprint('BAPU_CHECK ' + json.dumps(describe()))";
    let lines = ctx.run(Command::new(&py).args(["-c", check])
        .env("PYTHONPATH", home.join("app").join("src")).env("PYTHONNOUSERSITE", "1").env("PYTHONUTF8", "1")
        .current_dir(home))?;
    let dev: Value = lines.iter().find_map(|l| l.strip_prefix("BAPU_CHECK ")).and_then(|j| serde_json::from_str(j).ok())
        .ok_or("检查没有返回结果")?;
    let ready = json!({
        "variant": variant, "label": v["label"], "python": py_version, "device": dev,
        "installed_at": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
    });
    fs::write(runtime(home).join("ready.json"), serde_json::to_vec_pretty(&ready).unwrap()).map_err(|e| e.to_string())?;
    let _ = fs::remove_dir_all(runtime(home).join(".uv-cache"));
    Ok(ready)
}

/// Install in the background; the page follows setup-step / setup-log / setup-done / setup-error.
pub fn install(app: AppHandle, home: PathBuf, variant: String) -> Result<(), String> {
    if RUNNING.swap(true, Ordering::SeqCst) {
        return Err("已经在安装了".into());
    }
    CANCELLED.store(false, Ordering::SeqCst);
    let log_path = home.join("data").join("logs").join("setup.log");
    let _ = fs::create_dir_all(log_path.parent().unwrap());
    let log = OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| {
        RUNNING.store(false, Ordering::SeqCst);
        format!("写不了 {}：{e}（程序文件夹要有写入权限）", log_path.display())
    })?;
    let ctx = Ctx { app: app.clone(), home, log: Arc::new(Mutex::new(log)) };
    thread::spawn(move || {
        let result = steps(&ctx, &variant);
        RUNNING.store(false, Ordering::SeqCst);
        match result {
            Ok(ready) => {
                ctx.line("== 完成");
                let _ = ctx.app.emit("setup-done", ready);
                crate::start_once(&ctx.app);
            }
            Err(e) => {
                ctx.line(&format!("== 失败：{e}"));
                let _ = ctx.app.emit("setup-error", json!({ "message": e, "log": log_path.display().to_string() }));
            }
        }
    });
    Ok(())
}
