import { api } from "../lib/api";

/**
 * 获取系统用户名
 * 优先从后端 API 获取，回退到默认值
 */
export async function getSystemUsernameAsync(): Promise<string> {
  try {
    const status = await api.getStatus();
    if (status && typeof status.username === "string" && status.username) {
      return status.username;
    }
  } catch (error) {
    console.warn("通过 API 获取用户名失败:", error);
  }

  return "用户";
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
