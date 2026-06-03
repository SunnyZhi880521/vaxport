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

#[tauri::command]
async fn start_backend(
    app: tauri::AppHandle,
    state: State<'_, BackendProcess>,
) -> Result<(), String> {
    let mut process_lock = state.process.lock().unwrap();

    if process_lock.is_some() {
        return Ok(());
    }

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