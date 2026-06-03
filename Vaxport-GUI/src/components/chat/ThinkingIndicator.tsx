interface Props {
  label: string;
}

export function ThinkingIndicator({ label }: Props) {
  return (
    <div className="mb-4 flex items-center gap-3 rounded-lg bg-bg-secondary px-4 py-3">
      <div className="flex gap-1">
        <span className="h-2 w-2 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "0ms" }} />
        <span className="h-2 w-2 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "150ms" }} />
        <span className="h-2 w-2 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "300ms" }} />
      </div>
      <span className="text-sm text-text-secondary">
        {label || "Agent"} 思考中...
      </span>
    </div>
  );
}
