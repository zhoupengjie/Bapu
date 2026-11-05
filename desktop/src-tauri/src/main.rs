//! Bapu desktop shell.
//!
//! The web UI is built into this program (web/dist), so it opens even before
//! Python exists. The work is done by the local Python service (`bapu serve`):
//! this program starts it on a free port and tells the page where it is
//! (`service_status` / the "service" event); the page then calls its API.
//! Projects it is launched with (double-click, drag onto the window, a second
//! launch) are handed to the page, which opens them.
//!
//! Portable app (an `app\` folder next to Bapu.exe): until its own Python
//! inference environment is set up in `runtime\`, the page's 环境 tab installs
//! it (setup.rs: Python 3.12, Bapu's packages and the PyTorch build for the
//! chosen device, from the chosen download source) — without touching system
//! Python or the registry. Development build: uses the checkout's `.venv`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod load;
mod setup;
mod updater;

use std::fs::{self, OpenOptions};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, DragDropEvent, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

#[derive(Default)]
struct Backend {
    child: Mutex<Option<Child>>,
    port: AtomicU16,
    started: AtomicBool,
    /// (state, error): no_runtime | needs_sync | installing | starting | ready | failed
    status: Mutex<(String, Option<String>)>,
    /// a project to open once the page asks (launch argument)
    pending: Mutex<Option<String>>,
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

fn is_project_path(a: &str) -> bool {
    a.to_lowercase().ends_with(".bapu") || Path::new(a).is_dir()
}

/// The `.bapu` file (or project folder) among the launch arguments.
fn project_arg(args: &[String]) -> Option<String> {
    args.iter().skip(1).find(|a| is_project_path(a)).cloned()
}

/// What the page needs to know about the Python service; also sent as the "service" event.
fn status(app: &AppHandle) -> serde_json::Value {
    let st = app.state::<Backend>();
    let s = st.status.lock().unwrap().clone();
    let mode = Mode::detect();
    serde_json::json!({
        "state": s.0, "error": s.1, "port": st.port.load(Ordering::SeqCst),
        "portable": matches!(mode, Mode::Portable(_)), "log": mode.log_path().display().to_string(),
        "setup_running": setup::running(),
    })
}

fn set_status(app: &AppHandle, state: &str, error: Option<String>) {
    *app.state::<Backend>().status.lock().unwrap() = (state.to_string(), error);
    let _ = app.emit("service", status(app));
}

/// The runtime install failed: back to waiting for it (the 环境 page shows the error and offers a retry).
pub fn set_status_failed_setup(app: &AppHandle) {
    let home = Mode::detect();
    let state = match &home {
        Mode::Portable(h) if setup::ready(h) => "needs_sync",
        _ => "no_runtime",
    };
    set_status(app, state, None);
}

/// Hand a project to the page (it opens it through the API).
fn open_in_page(app: &AppHandle, path: String) {
    let st = app.state::<Backend>();
    if st.port.load(Ordering::SeqCst) == 0 {
        *st.pending.lock().unwrap() = Some(path); // picked up when the page asks
    } else {
        let _ = app.emit("open-project", path);
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// Start the service (once) and tell the page when it answers.
pub fn start_once(app: &AppHandle) {
    let state = app.state::<Backend>();
    if state.started.swap(true, Ordering::SeqCst) {
        return;
    }
    set_status(app, "starting", None);
    let handle = app.clone();
    thread::spawn(move || {
        let mode = Mode::detect();
        let port = free_port();
        let state = handle.state::<Backend>();
        match start_service(&mode, port) {
            Ok(child) => *state.child.lock().unwrap() = Some(child),
            Err(e) => {
                state.started.store(false, Ordering::SeqCst);
                return set_status(&handle, "failed", Some(e));
            }
        }
        if let Err(e) = wait_ready(port, &state.child, Duration::from_secs(180)) {
            state.started.store(false, Ordering::SeqCst);
            return set_status(&handle, "failed", Some(format!("{e}\n日志：{}", mode.log_path().display())));
        }
        state.port.store(port, Ordering::SeqCst);
        set_status(&handle, "ready", None);
    });
}

/// Decide at launch: start the service, or wait for the 环境 page to set up the runtime.
fn launch(app: &AppHandle) {
    let mode = Mode::detect();
    if let Mode::Portable(home) = &mode {
        updater::apply_staged(home); // a downloaded update, if Bapu was closed before it could be applied
        if !setup::ready(home) {
            return set_status(app, "no_runtime", None);
        }
        if setup::needs_sync(home) {
            // updated, and the new version needs other packages: the 环境 page brings the runtime up to date
            return set_status(app, "needs_sync", None);
        }
    }
    start_once(app);
}

#[tauri::command]
fn service_status(app: AppHandle) -> serde_json::Value {
    status(&app)
}

/// The project Bapu was launched with (once).
#[tauri::command]
fn take_launch_project(app: AppHandle) -> Option<String> {
    app.state::<Backend>().pending.lock().unwrap().take()
}

#[tauri::command]
fn setup_info() -> serde_json::Value {
    match Mode::detect() {
        Mode::Portable(home) => setup::info(&home),
        Mode::Dev(_) => serde_json::json!({}),
    }
}

#[tauri::command]
fn setup_probe() -> serde_json::Value {
    match Mode::detect() {
        Mode::Portable(home) => setup::probe(&home),
        Mode::Dev(_) => serde_json::json!({}),
    }
}

/// Install or sync the runtime; the service starts when it is done.
#[tauri::command]
fn setup_install(app: AppHandle, variant: String, source: String, choice: String, measured: serde_json::Value) -> Result<(), String> {
    let Mode::Portable(home) = Mode::detect() else {
        return Err("开发版用 uv sync 准备环境".into());
    };
    // a reinstall from the 环境 page: the running service holds the runtime's files
    stop_service(&app);
    let st = app.state::<Backend>();
    st.started.store(false, Ordering::SeqCst);
    st.port.store(0, Ordering::SeqCst);
    set_status(&app, "installing", None);
    setup::install(app, home, variant, source, choice, measured)
}

/// Pause the runtime install; 继续 runs setup_install again and skips what is already downloaded.
#[tauri::command]
fn setup_pause() {
    setup::pause();
}

/// Settings the 环境 page changes before the Python service runs (threads, download source).
#[tauri::command]
fn setup_settings(changes: serde_json::Value) -> Result<(), String> {
    let Mode::Portable(home) = Mode::detect() else { return Ok(()) };
    let allowed: serde_json::Map<String, serde_json::Value> = changes.as_object().cloned().unwrap_or_default()
        .into_iter().filter(|(k, _)| k == "threads" || k == "source").collect();
    setup::save_settings(&home, serde_json::Value::Object(allowed));
    Ok(())
}

/// CPU, memory and network for the sidebar, also while the Python service is not running.
#[tauri::command]
fn sys_load(app: AppHandle) -> serde_json::Value {
    app.state::<load::Load>().sample()
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
            if let Some(p) = project_arg(&argv) {
                open_in_page(app, p);
            } else if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend::default())
        .manage(load::Load::default())
        .invoke_handler(tauri::generate_handler![service_status, take_launch_project, setup_info, setup_probe, setup_install,
            setup_pause, setup_settings, sys_load])
        .setup(|app| {
            let args: Vec<String> = std::env::args().collect();
            *app.state::<Backend>().pending.lock().unwrap() = project_arg(&args);
            launch(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                let app = window.app_handle().clone();
                if setup::running() {
                    api.prevent_close();
                    app.dialog()
                        .message("运行环境正在安装。现在退出的话，下次打开 Bapu 时在环境页点继续，已经下载的部分不用重下。确定退出吗？")
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
                if let Some(p) = paths.iter().map(|p| p.to_string_lossy().to_string()).find(|p| p.to_lowercase().ends_with(".bapu")) {
                    open_in_page(window.app_handle(), p);
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
            if let Mode::Portable(home) = Mode::detect() {
                updater::apply_staged(&home); // the service is stopped: app\ can be replaced now
            }
        }
    });
}
