import { useState, useEffect } from "react";
import { ChevronRight, ChevronDown, Database, Table, Columns, Eye, Search } from "lucide-react";
import { cn } from "../../lib/utils";
import { api } from "../../lib/api";
import { useAppStore } from "../../stores/appStore";

interface SchemaNode {
  name: string;
  type: "schema" | "table" | "view" | "matview" | "column";
  dataType?: string;
  rowCount?: number;
  children?: SchemaNode[];
}

interface ApiColumn {
  name: string;
  data_type: string;
}

interface ApiTableEntry {
  name: string;
  columns: ApiColumn[];
  rows_estimate: number;
  size_tag: string;
  type: string;
}

interface ApiSchema {
  name: string;
  tables: ApiTableEntry[];
  views: ApiTableEntry[];
  matviews: ApiTableEntry[];
}

interface ApiDatabase {
  name: string;
  schemas: ApiSchema[];
}

function transformApiResponse(data: any): SchemaNode[] {
  const databases: ApiDatabase[] = data.databases || [];

  if (databases.length === 0) return [];

  if (databases.length === 1) {
    return transformDatabase(databases[0]);
  }

  return databases.map((db) => ({
    name: db.name,
    type: "schema" as const,
    children: transformDatabase(db),
  }));
}

function transformDatabase(db: ApiDatabase): SchemaNode[] {
  return (db.schemas || []).map((schema) => ({
    name: schema.name,
    type: "schema" as const,
    children: [
      ...(schema.tables || []).map((t) => tableEntryToNode(t, "table")),
      ...(schema.views || []).map((v) => tableEntryToNode(v, "view")),
      ...(schema.matviews || []).map((mv) => tableEntryToNode(mv, "matview")),
    ],
  }));
}

function tableEntryToNode(entry: ApiTableEntry, type: "table" | "view" | "matview"): SchemaNode {
  return {
    name: entry.name,
    type,
    rowCount: entry.rows_estimate,
    children: (entry.columns || []).map((col) => ({
      name: col.name,
      type: "column" as const,
      dataType: col.data_type,
    })),
  };
}

function countAllTables(schemas: SchemaNode[]): number {
  let count = 0;
  for (const node of schemas) {
    if (node.children) {
      for (const child of node.children) {
        if (child.type === "table" || child.type === "view" || child.type === "matview") {
          count++;
        }
      }
    }
  }
  return count;
}

export function SchemaBrowser() {
  const { backendOnline } = useAppStore();
  const [schemas, setSchemas] = useState<SchemaNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (backendOnline) {
      loadSchemas();
    }
  }, [backendOnline]);

  const loadSchemas = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.getSchemas();
      setSchemas(transformApiResponse(data));
    } catch (err) {
      console.error("Failed to load schemas:", err);
      setError("数据库连接失败");
    } finally {
      setLoading(false);
    }
  };

  const toggleExpand = (path: string) => {
    const newExpanded = new Set(expanded);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpanded(newExpanded);
  };

  const filterNodes = (nodes: SchemaNode[], query: string): SchemaNode[] => {
    if (!query) return nodes;
    const lowerQuery = query.toLowerCase();

    return nodes
      .map((node) => {
        const nameMatch = node.name.toLowerCase().includes(lowerQuery);
        const filteredChildren = node.children
          ? filterNodes(node.children, query)
          : [];

        if (nameMatch || filteredChildren.length > 0) {
          return { ...node, children: filteredChildren.length > 0 ? filteredChildren : node.children };
        }
        return null;
      })
      .filter(Boolean) as SchemaNode[];
  };

  const filteredSchemas = filterNodes(schemas, searchQuery);

  return (
    <div className="flex h-full flex-col">
      {/* Search */}
      <div className="mb-3">
        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索表名/列名..."
            className="w-full rounded-lg border border-border-subtle bg-bg-tertiary py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-purple focus:outline-none"
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="py-8 text-center text-sm text-text-muted">
            加载中...
          </div>
        ) : filteredSchemas.length === 0 ? (
          <div className="py-8 text-center text-sm text-text-muted">
            {searchQuery ? "未找到匹配项" : "暂无数据"}
          </div>
        ) : (
          filteredSchemas.map((schema) => (
            <TreeNode
              key={schema.name}
              node={schema}
              path={schema.name}
              expanded={expanded}
              onToggle={toggleExpand}
              level={0}
            />
          ))
        )}
      </div>

      {/* Footer info */}
      <div className="mt-2 border-t border-border-subtle pt-2 text-xs text-text-muted">
        {error ? (
          <span className="text-red-400">{error}</span>
        ) : (
          `${schemas.length} 个 Schema · ${countAllTables(schemas)} 张表`
        )}
      </div>
    </div>
  );
}

interface TreeNodeProps {
  node: SchemaNode;
  path: string;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  level: number;
}

function TreeNode({ node, path, expanded, onToggle, level }: TreeNodeProps) {
  const isExpanded = expanded.has(path);
  const hasChildren = node.children && node.children.length > 0;

  const Icon =
    node.type === "schema"
      ? Database
      : node.type === "view" || node.type === "matview"
      ? Eye
      : node.type === "table"
      ? Table
      : Columns;

  const iconColor =
    node.type === "schema"
      ? "text-accent-purple"
      : node.type === "view" || node.type === "matview"
      ? "text-accent-green"
      : node.type === "table"
      ? "text-accent-blue"
      : "text-accent-cyan";

  return (
    <div>
      <button
        onClick={() => hasChildren && onToggle(path)}
        className={cn(
          "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm hover:bg-bg-hover",
          hasChildren ? "cursor-pointer" : "cursor-default"
        )}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
      >
        {hasChildren ? (
          isExpanded ? (
            <ChevronDown size={12} className="text-text-muted" />
          ) : (
            <ChevronRight size={12} className="text-text-muted" />
          )
        ) : (
          <span className="w-3" />
        )}
        <Icon size={14} className={iconColor} />
        <span
          className={cn(
            "truncate",
            node.type === "column" ? "text-text-muted" : "text-text-secondary"
          )}
        >
          {node.name}
        </span>
        {node.type === "view" && (
          <span className="text-xs text-text-muted">view</span>
        )}
        {node.type === "matview" && (
          <span className="text-xs text-text-muted">mv</span>
        )}
        {node.dataType && (
          <span className="ml-auto text-xs text-text-muted">{node.dataType}</span>
        )}
        {node.rowCount != null && (
          <span className="ml-auto text-xs text-text-muted">{node.rowCount} 行</span>
        )}
      </button>

      {isExpanded && hasChildren && (
        <div>
          {node.children!.map((child) => (
            <TreeNode
              key={child.name}
              node={child}
              path={`${path}.${child.name}`}
              expanded={expanded}
              onToggle={onToggle}
              level={level + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}
