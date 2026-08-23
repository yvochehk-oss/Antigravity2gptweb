import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { 
  Sparkles, 
  Send, 
  RefreshCw, 
  X, 
  FileText, 
  TrendingDown, 
  ShieldAlert, 
  Bot, 
  User, 
  CheckCircle,
  HelpCircle,
  Copy
} from 'lucide-react';
import { AssistantMessage, SystemSettings } from '../types';

interface AiAssistantDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  messages: AssistantMessage[];
  onSendMessage: (query: string) => void;
  isAiThinking: boolean;
  settings?: SystemSettings;
}

export function AiAssistantDrawer({
  isOpen,
  onClose,
  messages,
  onSendMessage,
  isAiThinking,
  settings
}: AiAssistantDrawerProps) {
  const [inputValue, setInputValue] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const isDeepMode = settings?.aiDeepAnalysisMode ?? true;

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isAiThinking]);

  const handleSend = () => {
    if (!inputValue.trim()) return;
    onSendMessage(inputValue);
    setInputValue('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const copyToClipboard = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const quickPrompts = [
    '分析建筑劳务税务稽查风险详情',
    '预测下季度工程成本超支趋势',
    '生成全项目增值税进销项报告',
    '检查四流合一不匹配异常清单',
  ];

  if (!isOpen) return null;

  return (
    <aside className="w-[300px] sm:w-[320px] md:w-[320px] lg:w-[340px] xl:w-[360px] flex-shrink-0 flex flex-col h-full overflow-hidden bg-[#131b2e]/95 backdrop-blur-xl border-l border-[#4cd7f6]/30 shadow-2xl z-20">
      {/* 助手头部看板（固定顶部，永不跟随左侧画布滚动） */}
      <div className="px-3 py-3 border-b border-[#444653]/40 flex items-center justify-between bg-[#171f33]/90 flex-shrink-0 select-none">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="relative w-8 h-8 rounded-lg bg-[#03b5d3]/20 flex-shrink-0 flex items-center justify-center border border-[#4cd7f6]/50 shadow-[0_0_10px_rgba(76,215,246,0.3)]">
            <Sparkles className="w-4 h-4 text-[#4cd7f6]" />
            <span className="absolute inset-0 rounded-lg border border-[#4cd7f6] animate-slow-pulse"></span>
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-[14px] font-bold text-[#dae2fd] flex items-center gap-1">
              <span className="truncate">锐宝智能助手</span>
              <span className="text-[9px] font-semibold bg-[#1e40af] text-[#dde1ff] px-1 py-0.2 rounded flex-shrink-0">
                决策大脑
              </span>
            </h3>
            <p className="text-[10px] text-[#4cd7f6] truncate flex items-center gap-1">
              <span>财税风控智能专家</span>
              {isDeepMode && (
                <span className="text-[9px] text-[#10B981] bg-[#10B981]/15 px-1 rounded border border-[#10B981]/30">
                  深度穿透
                </span>
              )}
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-[#8e909f] hover:text-[#dae2fd] hover:bg-[#222a3d] transition-colors cursor-pointer flex-shrink-0"
          title="收起智能助手"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 消息对话区域（独立内部滚动，物理隔离） */}
      <div className="flex-1 overflow-y-auto overscroll-contain p-3 space-y-3 scrollbar-hide">
        <div className="text-center text-[10px] font-mono-num text-[#8e909f]/70 my-0.5">
          今日 14:32 · 安全审计链路已建立
        </div>

        {messages.map((msg) => {
          const isAi = msg.sender === 'ai';
          return (
            <div
              key={msg.id}
              className={`flex gap-2 max-w-[98%] ${isAi ? '' : 'self-end flex-row-reverse ml-auto'}`}
            >
              {/* 头像 */}
              <div className={`w-6 h-6 rounded-md flex-shrink-0 flex items-center justify-center mt-1 border ${
                isAi 
                  ? 'bg-[#03b5d3]/20 border-[#4cd7f6]/40 text-[#4cd7f6]' 
                  : 'bg-[#1e40af]/30 border-[#b8c4ff]/40 text-[#dde1ff]'
              }`}>
                {isAi ? <Bot className="w-3.5 h-3.5" /> : <User className="w-3.5 h-3.5" />}
              </div>

              {/* 消息气泡 */}
              <div className="flex flex-col space-y-1 min-w-0 flex-1">
                <div className={`rounded-xl p-2.5 text-[12px] leading-relaxed border transition-all ${
                  isAi
                    ? 'bg-[#171f33]/90 border-[#444653]/40 text-[#dae2fd] rounded-tl-sm shadow-md'
                    : 'bg-[#1e40af]/40 border-[#4cd7f6]/30 text-[#dde1ff] rounded-tr-sm'
                }`}>
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>

                  {/* 消息内推荐操作快捷按钮 */}
                  {msg.suggestedActions && msg.suggestedActions.length > 0 && (
                    <div className="mt-2.5 pt-2 border-t border-[#444653]/30 flex flex-wrap gap-1">
                      {msg.suggestedActions.map((action, aIdx) => (
                        <button
                          key={aIdx}
                          onClick={() => onSendMessage(action)}
                          className="px-2 py-0.5 rounded text-[10.5px] bg-[#03b5d3]/15 hover:bg-[#03b5d3]/25 text-[#4cd7f6] border border-[#4cd7f6]/30 transition-colors cursor-pointer"
                        >
                          {action}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between px-1 text-[9.5px] font-mono-num text-[#8e909f]">
                  <span>{msg.timestamp}</span>
                  {isAi && (
                    <button
                      onClick={() => copyToClipboard(msg.id, msg.content)}
                      className="hover:text-[#4cd7f6] flex items-center gap-1 cursor-pointer"
                    >
                      {copiedId === msg.id ? (
                        <>
                          <CheckCircle className="w-2.5 h-2.5 text-[#10B981]" />
                          <span className="text-[#10B981]">已复制</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-2.5 h-2.5" />
                          <span>复制解答</span>
                        </>
                      )}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}

        {/* 思考中动态卡片 */}
        {isAiThinking && (
          <div className="flex gap-2 max-w-[95%]">
            <div className="w-6 h-6 rounded-md bg-[#03b5d3]/20 border border-[#4cd7f6]/40 text-[#4cd7f6] flex-shrink-0 flex items-center justify-center mt-1">
              <RefreshCw className="w-3.5 h-3.5 animate-spin text-[#4cd7f6]" />
            </div>
            <div className="bg-[#1e40af]/15 border border-[#4cd7f6]/40 rounded-xl rounded-tl-sm p-2.5 text-[11.5px] text-[#4cd7f6] flex items-center gap-1.5">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-[#4cd7f6] animate-ping"></span>
              <span>正在穿透工程财税数仓与台账...</span>
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* 快捷指令预设 */}
      <div className="px-2.5 py-2 border-t border-[#444653]/30 bg-[#0b1326]/60">
        <div className="text-[10px] font-bold text-[#8e909f] mb-1 flex items-center gap-1">
          <HelpCircle className="w-2.5 h-2.5 text-[#4cd7f6]" />
          <span>快捷财税指令</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {quickPrompts.map((prompt, pIdx) => (
            <button
              key={pIdx}
              onClick={() => onSendMessage(prompt)}
              className="px-2 py-0.5 rounded-full text-[10.5px] bg-[#171f33] hover:bg-[#222a3d] text-[#c4c5d5] hover:text-[#4cd7f6] border border-[#444653]/40 transition-colors cursor-pointer"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      {/* 底部输入框 */}
      <div className="p-2.5 bg-[#171f33]/90 border-t border-[#444653]/40">
        <div className="relative bg-[#0b1326] border border-[#444653]/40 focus-within:border-[#4cd7f6] rounded-lg flex items-end overflow-hidden transition-colors shadow-inner">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="向锐宝助手提问（如：跨区预缴税率、四流合一合规）..."
            rows={2}
            className="w-full bg-transparent border-none text-[#dae2fd] text-[12px] focus:ring-0 resize-none p-2 placeholder:text-[#8e909f]/60 font-mono-num"
          />
          <button
            onClick={handleSend}
            disabled={!inputValue.trim() || isAiThinking}
            className="p-2 m-1 rounded-md bg-[#03b5d3] hover:bg-[#03b5d3]/80 disabled:opacity-30 disabled:hover:bg-[#03b5d3] text-[#001f26] font-bold transition-all cursor-pointer"
            title="发送指令"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}

