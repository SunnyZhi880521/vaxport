# 跨平台打包指南（Windows / Ubuntu）

**日期**: 2026-06-05

当前项目仅在 macOS ARM64 上打包测试过。若需打包 Windows (exe) 或 Ubuntu (deb)，需解决以下平台相关问题。

## 1. Rust sidecar 进程清理代码（lib.rs:19-42）

**问题**: `kill_old_sidecar_on_port()` 使用 `#[cfg(target_os = "macos")]` 条件编译，仅在 macOS 生效。Windows 和 Linux 上该函数为空，旧进程残留问题会复现。

**Windows 修复**:
```rust
#[cfg(target_os = "windows")]
{
    use std::process::Command;
    // 使用 netstat 查找占用 8931 端口的进程
    let output = Command::new("cmd")
        .args(&["/C", "for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :8931') do @echo %a"])
        .output()
        .ok();
    if let Some(out) = output {
        let pids = String::from_utf8_lossy(&out.stdout);
        for pid_str in pids.split_whitespace() {
            if let Ok(pid) = pid_str.parse::<u32>() {
                let _ = Command::new("taskkill")
                    .args(&["/F", "/PID", &pid.to_string()])
                    .output();
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}
```

**Linux 修复**:
```rust
#[cfg(target_os = "linux")]
{
    use std::process::Command;
    // 使用 fuser 或 ss 查找端口
    let output = Command::new("fuser")
        .args(&["8931/tcp"])
        .output()
        .ok();
    if let Some(out) = output {
        let pids = String::from_utf8_lossy(&out.stdout);
        for pid_str in pids.split_whitespace() {
            if let Ok(pid) = pid_str.parse::<u32>() {
                let _ = Command::new("kill")
                    .args(&["-9", &pid.to_string()])
                    .output();
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}
```

## 2. Tauri sidecar 二进制命名（tauri.conf.json:33）

**问题**: Tauri 的 `externalBin` 要求 sidecar 二进制按平台三元组命名。当前仅有 `vaxport-api-aarch64-apple-darwin`。

**各平台命名规范**:
- **Windows x86_64**: `vaxport-api-x86_64-pc-windows-msvc.exe`
- **Windows ARM64**: `vaxport-api-aarch64-pc-windows-msvc.exe`
- **Linux x86_64**: `vaxport-api-x86_64-unknown-linux-gnu`
- **Linux ARM64**: `vaxport-api-aarch64-unknown-linux-gnu`
- **macOS x86_64**: `vaxport-api-x86_64-apple-darwin`
- **macOS ARM64**: `vaxport-api-aarch64-apple-darwin`

**打包步骤**:
1. 在目标平台上运行 PyInstaller 构建 sidecar 二进制
2. 将二进制重命名为上述格式
3. 放入 `Vaxport-GUI/src-tauri/binaries/` 目录
4. 运行 `cargo tauri build`

## 3. SSH 隧道 preexec_fn 参数（db.py:55）

**问题**: `subprocess.Popen` 的 `preexec_fn=os.setpgrp` 参数是 Unix 专用，Windows 上会抛出 `AttributeError`。

**修复**: 添加平台判断
```python
import sys

kwargs = {
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
}
if sys.platform != "win32":
    kwargs["preexec_fn"] = os.setpgrp

self._tunnel_process = subprocess.Popen(cmd, **kwargs)
```

## 4. SSH 命令可用性（db.py:42-48）

**问题**: SSH 隧道依赖系统 `ssh` 命令。macOS/Linux 默认安装，Windows 10 1809+ 内置 OpenSSH，但旧版 Windows 需手动安装。

**建议**: 
- 在 Windows 打包文档中注明需要 OpenSSH Client
- 或在 Python 中检测 `ssh` 命令是否存在，不存在时给出友好提示

## 5. PyInstaller spec 文件（vaxport.spec）

**问题**: 当前 spec 文件是为 macOS 优化的（生成单文件可执行文件）。Windows 和 Linux 需要调整。

**Windows 调整**:
1. 确保 `console=True`（保持当前设置）
2. 可选：添加 `icon='icon.ico'`
3. 如需打包为单文件 exe，保持当前设置；如需目录模式，改用 `COLLECT`

**Linux 调整**:
1. 添加 `strip=True` 减小体积
2. 可选：添加 `runtime_tmpdir='/tmp'`

**通用注意事项**:
- Windows 上 PyInstaller 可能遗漏 `python3.dll`，需在 `binaries` 中显式添加
- Linux 上 `psycopg2` 需要系统安装 `libpq-dev`，PyInstaller 会自动打包
- 所有平台的 `hiddenimports` 列表应保持一致

## 6. Textual TUI 驱动导入（tui/app.py:56）

**问题**: `from textual.drivers.linux_driver import LinuxDriver` 在 Windows 上会失败。

**现状**: 代码已有平台判断（line 205: `if sys.platform != "darwin"`），但导入本身在模块级别，Windows 上仍会失败。

**修复**: 延迟导入或条件导入
```python
import sys

if sys.platform != "win32":
    from textual.drivers.linux_driver import LinuxDriver
```

## 7. psycopg2 二进制兼容性

**问题**: `psycopg2` 的 C 扩展需要与目标平台的 PostgreSQL 客户端库匹配。

**建议**:
- 使用 `psycopg2-binary` 而非 `psycopg2`，它包含预编译的二进制
- 在各平台的 `requirements.txt` 中指定 `psycopg2-binary>=2.9.0`
- 测试时确保目标机器有对应的 PostgreSQL 客户端库（Windows: `libpq.dll`，Linux: `libpq.so`）

## 8. 打包流程总结

**Windows (exe)**:
```bash
# 在 Windows 机器上
cd vaxport
python -m PyInstaller vaxport.spec --clean --noconfirm
cp dist/vaxport-api.exe Vaxport-GUI/src-tauri/binaries/vaxport-api-x86_64-pc-windows-msvc.exe
cd Vaxport-GUI/src-tauri
cargo tauri build
# 输出: target/release/bundle/msi/vaxport_1.3.4_x64_en-US.msi
```

**Ubuntu (deb)**:
```bash
# 在 Ubuntu 机器上
sudo apt install libpq-dev
cd vaxport
python3 -m PyInstaller vaxport.spec --clean --noconfirm
cp dist/vaxport-api Vaxport-GUI/src-tauri/binaries/vaxport-api-x86_64-unknown-linux-gnu
cd Vaxport-GUI/src-tauri
cargo tauri build
# 输出: target/release/bundle/deb/vaxport_1.3.4_amd64.deb
```

## 9. 已知限制

1. **交叉编译不支持**: PyInstaller 不支持交叉编译，必须在目标平台上构建
2. **macOS Universal Binary**: 如需同时支持 x86_64 和 ARM64，需分别构建后使用 `lipo` 合并
3. **Windows ARM64**: Tauri 对 Windows ARM64 支持有限，建议优先支持 x86_64
4. **Linux 发行版碎片**: `.deb` 仅适用于 Debian/Ubuntu，其他发行版需打包 `.rpm` (Fedora/RHEL) 或 `.AppImage` (通用)
