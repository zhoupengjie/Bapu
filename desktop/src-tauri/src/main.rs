//! Bapu desktop shell.
//!
//! The work is done by the local Python service (`bapu serve`): this program
//! starts it on a free port, shows its web UI in a window, opens the `.bapu`
//! project it was launched with (double-click, drag onto the window, or a
//! second launch while it is open), and stops the service when it closes.
//!
//! Portable app (an `app\` folder next to Bapu.exe): on first launch it sets up
//! its own Python inference environment in `runtime\` with the bundled uv —
//! Python 3.12, Bapu's packages and the PyTorch build for the GPU the user
//! picks — without touching system Python, the registry or the user profile.
//! Development build: uses the checkout's `.venv`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod setup;

use std::fs::{self, OpenOptions};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, DragDropEvent, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

#[derive(Default)]
struct Backend {
    child: Mutex<Option<Child>>,
    port: AtomicU16,
    started: AtomicBool,
}

/// Where Bapu runs from.
#[derive(Clone, Debug)]
pub enum Mode {
    /// `<folder>\Bapu.exe` with `app\`, `runtime\`, `models\`, `data\` beside it.
    Portable(PathBuf),
    /// A development checkout with a `.venv`.
    Dev(PathBuf),
}

impl Mode {
    pub fn detect() -> Mode {
        if let Some(dir) = std::env::current_exe().ok().and_then(|e| e.parent().map(Path::to_path_buf)) {
            if dir.join("app").join("src").join("bapu").is_dir() {
                return Mode::Portable(strip_verbatim(dir));
            }
        }
        let p = std::env::var_os("BAPU_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(".."));
        Mode::Dev(fs::canonicalize(&p).map(strip_verbatim).unwrap_or(p))
    }

    pub fn home(&self) -> &Path {
        match self {
            Mode::Portable(h) | Mode::Dev(h) => h,
        }
    }

    fn python(&self) -> Option<PathBuf> {
        match self {
            Mode::Portable(h) => setup::runtime_python(h),
            Mode::Dev(h) => {
                let p = if cfg!(windows) { h.join(".venv").join("Scripts").join("python.exe") } else { h.join(".venv").join("bin").join("python") };
                p.exists().then_some(p)
            }
        }
    }

    fn log_path(&self) -> PathBuf {
        match self {
            Mode::Portable(h) => h.join("data").join("logs").join("serve.log"),
            Mode::Dev(h) => h.join(".cache").join("desktop-serve.log"),
        }
    }
}

/// `\\?\D:\...` -> `D:\...` (Python and cmd do not like the verbatim prefix).
pub fn strip_verbatim(p: PathBuf) -> PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC") => PathBuf::from(rest),
        _ => p,
    }
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0").and_then(|l| l.local_addr()).map(|a| a.port()).unwrap_or(8766)
}

fn base(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// `bapu serve` on `port`; it exits by itself when this process ends (`--parent-pid`).
fn start_service(mode: &Mode, port: u16) -> Result<Child, String> {
    let py = mode.python().ok_or_else(|| match mode {
        Mode::Portable(_) => "运行环境还没有装好".to_string(),
        Mode::Dev(h) => format!("找不到 Bapu 的 Python 环境：先在 {} 里运行 uv sync。", h.display()),
    })?;
    let home = mode.home();
    let log_path = mode.log_path();
    let _ = fs::create_dir_all(log_path.parent().unwrap());
    let log = OpenOptions::new().create(true).append(true).open(&log_path)
        .map_err(|e| format!("写不了日志 {}：{e}", log_path.display()))?;
    let mut cmd = Command::new(py);
    cmd.args(["-m", "bapu.cli", "serve", "--port", &port.to_string()])
        .args(["--parent-pid", &std::process::id().to_string()])
        .current_dir(home)
        .stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .env("PYTHONUTF8", "1");
    if let Ok(exe) = std::env::current_exe() {
        cmd.env("BAPU_APP_EXE", strip_verbatim(exe));
    }
    if let Mode::Portable(h) = mode {
        cmd.args(["--work", "data", "--web", "app/web"])
            .env("PYTHONPATH", h.join("app").join("src"))
            .env("PYTHONNOUSERSITE", "1")
            .env("BAPU_HOME", h)
            .env("BAPU_CACHE_DIR", h); // models\ is <cache>\models
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().map_err(|e| format!("启动不了 Bapu 服务：{e}"))
}

fn get_json(url: &str, timeout: Duration) -> Option<serde_json::Value> {
    ureq::get(url).timeout(timeout).call().ok()?.into_json().ok()
}

fn wait_ready(port: u16, child: &Mutex<Option<Child>>, limit: Duration) -> Result<(), String> {
    let end = Instant::now() + limit;
    while Instant::now() < end {
        if get_json(&format!("{}/api/system", base(port)), Duration::from_secs(2)).is_some() {
            return Ok(());
        }
        if let Some(c) = child.lock().unwrap().as_mut() {
            if let Ok(Some(status)) = c.try_wait() {
                return Err(format!("Bapu 服务退出了（{status}）"));
            }
        }
        thread::sleep(Duration::from_millis(300));
    }
    Err("Bapu 服务一直没有响应".into())
}

/// Register a project with the service; its id for `#/p/<id>`.
fn open_project(port: u16, path: &str) -> Result<String, String> {
    let body = serde_json::json!({ "path": path }).to_string();
    let r = ureq::post(&format!("{}/api/projects/open", base(port)))
        .set("Content-Type", "application/json")
        .timeout(Duration::from_secs(30))
        .send_string(&body);
    let v: serde_json::Value = match r {
        Ok(r) => r.into_json().map_err(|e| e.to_string())?,
        Err(ureq::Error::Status(_, r)) => {
            let v: serde_json::Value = r.into_json().unwrap_or_default();
            return Err(v["error"].as_str().unwrap_or("打不开这个工程").to_string());
        }
        Err(e) => return Err(e.to_string()),
    };
    v["id"].as_str().map(str::to_string).ok_or_else(|| "服务没有返回工程编号".into())
}

fn is_project_path(a: &str) -> bool {
    a.to_lowercase().ends_with(".bapu") || Path::new(a).is_dir()
}

/// The `.bapu` file (or project folder) among the launch arguments.
fn project_arg(args: &[String]) -> Option<String> {
    args.iter().skip(1).find(|a| is_project_path(a)).cloned()
}

/// Show the UI, opening `project` if given.
fn show(app: &AppHandle, port: u16, project: Option<String>) {
    let mut url = format!("{}/", base(port));
    if let Some(p) = project {
        match open_project(port, &p) {
            Ok(id) => url.push_str(&format!("#/p/{id}")),
            Err(e) => {
                app.dialog().message(format!("打不开 {p}\n\n{e}")).title("Bapu").kind(MessageDialogKind::Error).show(|_| {});
            }
        }
    }
    if let Some(w) = app.get_webview_window("main") {
        if let Ok(u) = Url::parse(&url) {
            let _ = w.navigate(u);
        }
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn splash_error(app: &AppHandle, message: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let text = serde_json::to_string(message).unwrap_or_default();
        let _ = w.eval(&format!("window.showError && window.showError({text})"));
    }
}

/// Start the service (once) and show the UI when it answers.
pub fn start_once(app: &AppHandle) {
    let state = app.state::<Backend>();
    if state.started.swap(true, Ordering::SeqCst) {
        return;
    }
    let handle = app.clone();
    thread::spawn(move || {
        let mode = Mode::detect();
        let port = free_port();
        let state = handle.state::<Backend>();
        match start_service(&mode, port) {
            Ok(child) => *state.child.lock().unwrap() = Some(child),
            Err(e) => {
                state.started.store(false, Ordering::SeqCst);
                return splash_error(&handle, &e);
            }
        }
        if let Err(e) = wait_ready(port, &state.child, Duration::from_secs(180)) {
            state.started.store(false, Ordering::SeqCst);
            return splash_error(&handle, &format!("{e}\n日志：{}", mode.log_path().display()));
        }
        state.port.store(port, Ordering::SeqCst);
        let args: Vec<String> = std::env::args().collect();
        show(&handle, port, project_arg(&args));
    });
}

/// Called by the start page: set up the runtime first, or start right away.
#[tauri::command]
fn boot(app: AppHandle) -> serde_json::Value {
    let mode = Mode::detect();
    if let Mode::Portable(home) = &mode {
        if !setup::ready(home) {
            return serde_json::json!({ "setup": true, "info": setup::info(home) });
        }
    }
    start_once(&app);
    serde_json::json!({ "setup": false })
}

#[tauri::command]
fn setup_install(app: AppHandle, variant: String) -> Result<(), String> {
    let Mode::Portable(home) = Mode::detect() else {
        return Err("开发版用 uv sync 准备环境".into());
    };
    setup::install(app, home, variant)
}

/// Is a project being processed? (closing the window would stop it)
fn busy(port: u16) -> bool {
    get_json(&format!("{}/api/system", base(port)), Duration::from_secs(1))
        .map(|v| v["running"].as_array().is_some_and(|a| !a.is_empty()) || v["queue"].as_u64().unwrap_or(0) > 0)
        .unwrap_or(false)
}

fn stop_service(app: &AppHandle) {
    if let Some(mut c) = app.state::<Backend>().child.lock().unwrap().take() {
        let _ = c.kill();
        let _ = c.wait();
    }
}

fn main() {
    let app = tauri::Builder::default()
        // must come first: a second launch (double-click another .bapu) goes to this window
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            let port = app.state::<Backend>().port.load(Ordering::SeqCst);
            let project = project_arg(&argv);
            let app = app.clone();
            thread::spawn(move || {
                if port != 0 {
                    show(&app, port, project);
                } else if let Some(w) = app.get_webview_window("main") {
                    let _ = w.set_focus();
                }
            });
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![boot, setup_install])
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                let app = window.app_handle().clone();
                if setup::running() {
                    api.prevent_close();
                    app.dialog()
                        .message("运行环境正在安装，现在退出的话下次启动要重新装。确定退出吗？")
                        .title("Bapu").kind(MessageDialogKind::Warning)
                        .buttons(MessageDialogButtons::OkCancelCustom("退出".into(), "继续安装".into()))
                        .show(move |quit| if quit { setup::cancel(); app.exit(0) });
                    return;
                }
                let port = app.state::<Backend>().port.load(Ordering::SeqCst);
                if port != 0 && busy(port) {
                    api.prevent_close();
                    app.dialog()
                        .message("还有工程在处理，关掉 Bapu 会中断它（已经算完的步骤会保留）。确定退出吗？")
                        .title("Bapu").kind(MessageDialogKind::Warning)
                        .buttons(MessageDialogButtons::OkCancelCustom("退出".into(), "继续处理".into()))
                        .show(move |quit| if quit { stop_service(&app); app.exit(0) });
                }
            }
            // a .bapu dropped onto the window opens it; other files go to the page (tauri://drag-drop)
            WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) => {
                let app = window.app_handle().clone();
                let port = app.state::<Backend>().port.load(Ordering::SeqCst);
                if let Some(p) = paths.iter().map(|p| p.to_string_lossy().to_string()).find(|p| p.to_lowercase().ends_with(".bapu")) {
                    if port != 0 {
                        thread::spawn(move || show(&app, port, Some(p)));
                    }
                }
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("Bapu 窗口启动失败");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            setup::cancel();
            stop_service(app);
        }
    });
}
