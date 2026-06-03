import { useEffect, useState, useRef } from "react";
import { useAppStore } from "./stores/appStore";
import { useChatStore } from "./stores/chatStore";
import { api, BASE_URL } from "./lib/api";
import { SSEClient } from "./lib/sse";
import { checkBackendStatus, startBackend, stopBackend } from "./lib/backend";
import { Sidebar } from "./components/layout/Sidebar";
import { ChatArea } from "./components/chat/ChatArea";
import { RightPanel } from "./components/layout/RightPanel";
import { InputArea } from "./components/input/InputArea";
import { StatusBar } from "./components/layout/StatusBar";
import { SettingsDialog } from "./components/settings/SettingsDialog";
import { CommandPalette } from "./components/CommandPalette";
import "./App.css";

const sseClient = new SSEClient();

export default function App() {
  const { sidebarOpen, rightPanelOpen, setBackendOnline, theme, setTheme, toggleSidebar, toggleRightPanel } = useAppStore();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const streamReqRef = useRef<string | null>(null);
  const {
    addUserMessage,
    setBusy,
    setActiveRequestId,
    setStatusText,
    setCurrentAgent,
    addToolCall,
    appendPlanChunk,
    finalizeAnswer,
    addErrorMessage,
    resetStreaming,
    messages,
    isBusy,
    activeRequestId,
    planMode,
    setPlanMode,
    clearMessages,
    setCurrentPhase,
  } = useChatStore();

  // Command palette commands
  const commands = [
    {
      id: "new-chat",
      label: "新建对话",
      description: "清空当前对话历史",
      icon: "💬",
      action: clearMessages,
    },
    {
      id: "toggle-sidebar",
      label: sidebarOpen ? "隐藏左侧边栏" : "显示左侧边栏",
      description: "切换 Schema 浏览器面板",
      icon: "📋",
      shortcut: "⌘B",
      action: toggleSidebar,
    },
    {
      id: "toggle-right-panel",
      label: rightPanelOpen ? "隐藏右侧面板" : "显示右侧面板",
      description: "切换工具日志面板",
      icon: "🔧",
      shortcut: "⌘J",
      action: toggleRightPanel,
    },
    {
      id: "toggle-plan-mode",
      label: planMode ? "切换到执行模式" : "切换到规划模式",
      description: "控制 Agent 是否先生成计划",
      icon: "📝",
      shortcut: "⌘T",
      action: () => setPlanMode(!planMode),
    },
    {
      id: "toggle-theme",
      label: theme === "dark" ? "切换到浅色主题" : "切换到深色主题",
      description: "更改界面外观",
      icon: theme === "dark" ? "☀️" : "🌙",
      action: () => setTheme(theme === "dark" ? "light" : "dark"),
    },
    {
      id: "open-settings",
      label: "打开设置",
      description: "配置数据库、模型和外观",
      icon: "⚙️",
      shortcut: "⌘,",
      action: () => setSettingsOpen(true),
    },
    {
      id: "refresh-schema",
      label: "刷新 Schema",
      description: "重新加载数据库表结构",
      icon: "🔄",
      action: async () => {
        try {
          await api.refreshSchema();
          setStatusText("✅ Schema 已刷新");
        } catch (err) {
          setStatusText("❌ 刷新失败");
        }
      },
    },
    {
      id: "toggle-debug",
      label: "切换调试模式",
      description: "显示/隐藏工具调用日志",
      icon: "🐛",
      action: () => {
        const state = useAppStore.getState();
        useAppStore.setState({ debugMode: !state.debugMode });
      },
    },
  ];

  // Check backend on mount and start if needed
  useEffect(() => {
    const initBackend = async () => {
      const isOnline = await checkBackendStatus();
      if (!isOnline) {
        await startBackend();
        // Wait for backend to start
        for (let i = 0; i < 10; i++) {
          await new Promise(resolve => setTimeout(resolve, 500));
          const status = await checkBackendStatus();
          if (status) {
            setBackendOnline(true);
            return;
          }
        }
        setBackendOnline(false);
      } else {
        setBackendOnline(true);
      }
    };
    initBackend();

    // Cleanup on unmount
    return () => {
      stopBackend();
    };
  }, [setBackendOnline]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "b") {
        e.preventDefault();
        useAppStore.getState().toggleSidebar();
      }
      if (mod && e.key === "j") {
        e.preventDefault();
        useAppStore.getState().toggleRightPanel();
      }
      if (mod && e.key === "t") {
        e.preventDefault();
        useChatStore.getState().setPlanMode(!useChatStore.getState().planMode);
      }
      if (mod && e.key === ",") {
        e.preventDefault();
        setSettingsOpen(true);
      }
      if (mod && e.key === "k") {
        e.preventDefault();
        setCommandPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleSend = async (text: string) => {
    if (!text.trim()) return;

    addUserMessage(text);

    const derivePhase = (msg: string) => {
      if (msg.includes("分类")) return "classify";
      if (msg.includes("生成 SQL") || msg.includes("生成SQL")) return "sql_gen";
      if (msg.includes("批量数据采集") || msg.includes("执行")) return "data_collection";
      if (msg.includes("数据不足") || msg.includes("补查")) return "data_check";
      if (msg.includes("分析阶段") || msg.includes("分析查询")) return "analysis";
      if (msg.includes("审核")) return "review";
      if (msg.includes("修复")) return "fix";
      if (msg.includes("追问")) return "feedback";
      return "";
    };

    // If busy, send as feedback to the running task instead of starting a new SSE
    const fbReqId = streamReqRef.current || activeRequestId;
    if (isBusy && fbReqId) {
      useChatStore.getState().addInteractionMsg("user", text);
      setStatusText("💬 追问已发送");
      try {
        await fetch(BASE_URL + "/api/chat/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: fbReqId, confirmed: true, feedback: text }),
        });
      } catch {
        // ignore
      }
      return;
    }

    setBusy(true);
    resetStreaming();

    const history = messages.map((m) => ({
      role: m.role === "user" ? "user" : "assistant",
      content: m.content,
    }));

    let streamRequestId: string | null = null;

    await sseClient.send(text, planMode, history, {
      onMeta: (data) => {
        const reqId = data.request_id as string;
        console.log("[onMeta] request_id:", reqId);
        streamRequestId = reqId;
        streamReqRef.current = reqId;
        setActiveRequestId(reqId);
      },
      onStatus: (data) => {
        const msg = (data.message as string) || (data.text as string) || "";
        setStatusText(msg);
        const phase = derivePhase(msg);
        if (phase) setCurrentPhase(phase);
        if (data.agent_type) {
          setCurrentAgent(
            (data.agent_type as string) as never,
            (data.agent_label as string) || ""
          );
        }
      },
      onPlanChunk: (data) => {
        setCurrentPhase("planning");
        appendPlanChunk(data.text);
      },
      onPlanReady: (data) => {
        const planText = data.plan_text as string;
        const reqId = data.request_id as string;
        console.log("[onPlanReady] request_id:", reqId, "plan length:", planText?.length);
        if (reqId) {
          streamRequestId = reqId;
          streamReqRef.current = reqId;
          setActiveRequestId(reqId);
        }
        // Add plan as a message in the chat
        const state = useChatStore.getState();
        const msgId = Date.now().toString();
        useChatStore.setState({
          messages: [
            ...state.messages,
            {
              id: msgId,
              role: "agent",
              content: planText,
              agentType: "general",
              agentLabel: "执行计划",
              messageType: "plan",
              toolCalls: state.toolCalls.length > 0 ? [...state.toolCalls] : undefined,
              timestamp: Date.now(),
            },
          ],
          waitingConfirm: true,
          planText,
          planDecisions: (data.decisions as never[]) || [],
        });
        setStatusText("📋 请审核执行计划 — Enter 确认 / 输入修改意见后 Enter");
      },
      onToolCall: (data) => {
        addToolCall({
          name: data.name as string,
          args: data.args as Record<string, unknown>,
        });
        setStatusText("⚙ " + (data.name as string));
      },
      onToolResult: (data) => {
        // Update last tool call with result
        const state = useChatStore.getState();
        const calls = [...state.toolCalls];
        if (calls.length > 0) {
          calls[calls.length - 1] = {
            ...calls[calls.length - 1],
            rowCount: data.row_count as number,
            truncated: data.truncated as boolean,
          };
          useChatStore.setState({ toolCalls: calls });
        }
        setStatusText("↳ " + (data.row_count as number) + " 行结果");
      },
      onChart: (data) => {
        useAppStore.getState().addChart({
          filePath: data.file_path,
          image: data.image,
        });
        useChatStore.getState().addChart({
          filePath: data.file_path,
          image: data.image,
        });
        useAppStore.getState().setRightPanelTab("charts");
        setStatusText("📊 图表已生成");
      },
      onAnswer: (data) => {
        finalizeAnswer(
          data.answer as string,
          (data.turns as number) || 0,
          (data.tokens_used as number) || 0,
          (data.token_pct as number) || 0,
          (data.sql_queries as string[]) || [],
          (data.task_id as string) || ""
        );
      },
      onError: (data) => {
        addErrorMessage(data.message);
        const currentId = useChatStore.getState().activeRequestId;
        if (!currentId || currentId === streamRequestId) {
          setBusy(false);
          setActiveRequestId(null);
          streamReqRef.current = null;
        }
      },
      onDone: () => {
        const currentId = useChatStore.getState().activeRequestId;
        if (!currentId || currentId === streamRequestId) {
          setBusy(false);
          setActiveRequestId(null);
          streamReqRef.current = null;
          setStatusText("");
        }
      },
      onHeartbeat: (data) => {
        setStatusText(`⏳ 执行中 (${Math.floor(data.elapsed)}s)`);
      },
    });
  };

  const handleCancel = () => {
    sseClient.cancel();
    const reqId = streamReqRef.current || activeRequestId;
    if (reqId) {
      api.cancelQuery(reqId).catch(() => {});
    }
    streamReqRef.current = null;
    setBusy(false);
    setActiveRequestId(null);
    resetStreaming();
    setStatusText("⏹ 已取消");
  };

  const handleAttach = async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({
        title: "选择文件",
        multiple: false,
      });
      if (selected) {
        setStatusText("已选择文件: " + (selected as string).split("/").pop());
      }
    } catch (err) {
      console.error("文件选择失败:", err);
    }
  };

  const handleCapture = () => {
    setStatusText("截图功能暂未实现");
    setTimeout(() => setStatusText(""), 2000);
  };

  const handleRetry = (userMessage: string) => {
    // Retry just sends the same message again
    handleSend(userMessage);
  };

  const handleConfirmPlan = (confirmed: boolean, feedback?: string) => {
    const reqId = streamReqRef.current || useChatStore.getState().activeRequestId || activeRequestId;
    console.log("[confirmPlan] ref:", streamReqRef.current, "store:", useChatStore.getState().activeRequestId, "closure:", activeRequestId, "→ using:", reqId);
    if (reqId) {
      api.confirmPlan(reqId, confirmed, feedback)
        .then((res) => console.log("[confirmPlan] success:", res))
        .catch((err) => console.error("[confirmPlan] failed:", err));
    } else {
      console.warn("[confirmPlan] NO request ID from any source!");
    }
    useChatStore.getState().hidePlanConfirm();
  };

  return (
    <div className="flex h-screen flex-col bg-bg-primary text-text-primary">
      {/* Main content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar */}
        {sidebarOpen && <Sidebar onSettingsOpen={() => setSettingsOpen(true)} />}

        {/* Center: chat + input */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <ChatArea onRetry={handleRetry} />
          <InputArea onSend={handleSend} onCancel={handleCancel} onConfirmPlan={handleConfirmPlan} onAttach={handleAttach} onCapture={handleCapture} />
        </div>

        {/* Right panel */}
        {rightPanelOpen && <RightPanel />}
      </div>

      {/* Bottom status bar */}
      <StatusBar />

      {/* Settings dialog */}
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />

      {/* Command palette */}
      <CommandPalette
        open={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        commands={commands}
      />
    </div>
  );
}
