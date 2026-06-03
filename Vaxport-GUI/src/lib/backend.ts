import { invoke } from "@tauri-apps/api/core";

export async function checkBackendStatus(): Promise<boolean> {
  try {
    return await invoke("check_backend");
  } catch {
    return false;
  }
}

export async function startBackend(): Promise<void> {
  try {
    await invoke("start_backend");
  } catch (err) {
    console.error("Failed to start backend:", err);
  }
}

export async function stopBackend(): Promise<void> {
  try {
    await invoke("stop_backend");
  } catch (err) {
    console.error("Failed to stop backend:", err);
  }
}
