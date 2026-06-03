import { Command } from '@tauri-apps/plugin-shell';

/**
 * 获取系统用户名（异步版本，适用于 Tauri 环境）
 * 通过执行系统命令 whoami 获取当前用户名
 */
export async function getSystemUsernameAsync(): Promise<string> {
  try {
    // 在 Tauri 环境中使用 shell 插件执行 whoami
    const output = await Command.create('whoami').execute();
    if (output.code === 0 && output.stdout) {
      return output.stdout.trim();
    }
  } catch (error) {
    console.warn('通过 shell 获取用户名失败:', error);
  }

  // 回退到默认值
  return '用户';
}

/**
 * 缓存用户名，避免重复获取
 */
let cachedUsername: string | null = null;
let usernamePromise: Promise<string> | null = null;

/**
 * 获取用户名（带缓存，异步）
 */
export async function getUsernameAsync(): Promise<string> {
  if (cachedUsername !== null) {
    return cachedUsername;
  }

  if (usernamePromise === null) {
    usernamePromise = getSystemUsernameAsync().then((name) => {
      cachedUsername = name;
      return name;
    });
  }

  return usernamePromise;
}

/**
 * 获取用户名（同步，可能返回缓存值或默认值）
 */
export function getUsername(): string {
  if (cachedUsername !== null) {
    return cachedUsername;
  }
  // 同步调用时，如果还没有缓存，先返回默认值，同时启动异步获取
  getUsernameAsync();
  return '用户';
}
