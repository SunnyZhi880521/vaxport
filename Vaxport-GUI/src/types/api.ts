/** API response types */

export interface BackendStatus {
  version: string;
  model: string;
  backend: string;
  pg_status: string;
  pg_host: string;
  pg_database: string;
}

export interface SchemaNode {
  name: string;
  type: "schema" | "table" | "column";
  children?: SchemaNode[];
  dataType?: string;
}

export interface SkillInfo {
  name: string;
  description: string;
  tier?: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  backend: string;
}

export interface AppConfig {
  model: string;
  backend: string;
  pg_host: string;
  pg_database: string;
  pg_user: string;
  api_key_redacted: string;
  db_names: string[];
  active_db: string;
  skills_count: number;
  auto_plan: boolean;
  plan_confirm: boolean;
}

export interface SessionInfo {
  session_id: string;
  message_count: number;
  created_at: string;
}
