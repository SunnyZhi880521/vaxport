use std::sync::{Arc, Mutex};
use tauri::State;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct BackendProcess {
    process: Arc<Mutex<Option<CommandChild>>>,
}

#[tauri::command]
async fn check_backend() -> Result<bool, String> {
    let client = reqwest::Client::new();
    match client.get("http://localhost:8931/api/status").send().await {
        Ok(resp) => Ok(resp.status().is_success()),
        Err(_) => Ok(false),
    }
}

fn kill_old_sidecar_on_port() {
    // 检查端口 8931 是否被占用，如果是则 kill 旧进程
    #[cfg(target_os = "macos")]
    {
        use std::process::Command;
        let output = Command::new("lsof")
            .args(&["-ti:8931"])
            .output()
            .ok();
        if let Some(out) = output {
            let pids = String::from_utf8_lossy(&out.stdout);
            for pid_str in pids.split_whitespace() {
                if let Ok(pid) = pid_str.parse::<u32>() {
                    let _ = Command::new("kill")
                        .args(&["-9", &pid.to_string()])
                        .output();
                    log::info!("[vaxport] Killed old sidecar process: {}", pid);
                }
            }
            // 等待进程退出
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
}

#[tauri::command]
async fn start_backend(
    app: tauri::AppHandle,
    state: State<'_, BackendProcess>,
) -> Result<(), String> {
    let mut process_lock = state.process.lock().unwrap();

    if process_lock.is_some() {
        return Ok(());
    }

    // 启动前清理旧进程
    kill_old_sidecar_on_port();

    let sidecar = app
        .shell()
        .sidecar("vaxport-api")
        .map_err(|e| format!("sidecar not found: {}", e))?;

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("Failed to start backend: {}", e))?;

    *process_lock = Some(child);

    // 异步读取 sidecar 输出，避免阻塞
    tokio::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            if let CommandEvent::Stdout(line) = event {
                log::info!("[vaxport-api] {}", String::from_utf8_lossy(&line));
            } else if let CommandEvent::Stderr(line) = event {
                log::error!("[vaxport-api] {}", String::from_utf8_lossy(&line));
            }
        }
    });

    Ok(())
}

#[tauri::command]
async fn stop_backend(state: State<'_, BackendProcess>) -> Result<(), String> {
    let mut process_lock = state.process.lock().unwrap();

    if let Some(mut child) = process_lock.take() {
        let _ = child.kill();
    }

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend_process = BackendProcess {
        process: Arc::new(Mutex::new(None)),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .manage(backend_process)
        .invoke_handler(tauri::generate_handler![
            check_backend,
            start_backend,
            stop_backend
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}