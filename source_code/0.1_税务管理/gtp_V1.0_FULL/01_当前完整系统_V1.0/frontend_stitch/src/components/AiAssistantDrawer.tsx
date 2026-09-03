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
import { AiExecutionMetadata, AssistantMessage, SystemSettings } from '../types';

interface AiAssistantDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  messages: AssistantMessage[];
  onSendMessage: (query: string) => void;
  isAiThinking: boolean;
  settings?: SystemSettings;
}

function endpointLabel(endpoint: AiExecutionMetadata['effectiveEndpoint']): string {
  if (!endpoint) return '—';
  const parts = [endpoint.id !== undefined ? `#${endpoint.id}` : '', endpoint.name ?? '', endpoint.model ?? ''];
  return parts.filter(Boolean).join(' · ') || '—';
}

function AiMetadataPanel({ metadata }: { metadata?: AiExecutionMetadata }) {
  if (!metadata || Object.keys(metadata).length === 0) return null;
  const attempts = Array.isArray(metadata.attempts) ? metadata.attempts : [];
  return (
    <div className="mt-2 space-y-1 border-t border-default pt-2 text-[10px] text-secondary">
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {(metadata.selectedEndpoint || metadata.effectiveEndpoint) && <span>端点：{endpointLabel(metadata.effectiveEndpoint ?? metadata.selectedEndpoint)}</span>}
        {metadata.status && <span>状态：{metadata.status}</span>}
        {metadata.fallback !== undefined && <span>fallback：{metadata.fallback ? '是' : '否'}</span>}
        {metadata.degraded !== undefined && <span>DEGRADED：{metadata.degraded ? '是' : '否'}</span>}
      </div>
      {attempts.length > 0 && <div>尝试摘要：{attempts.map((attempt, index) => `${index + 1}. ${endpointLabel(attempt.endpoint)}${attempt.status ? `/${attempt.status}` : ''}${attempt.error ? `：${attempt.error}` : ''}`).join('；')}</div>}
    </div>
  );
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
    <aside className="z-20 flex h-full w-[300px] flex-shrink-0 flex-col overflow-hidden border-l border-default bg-surface sm:w-[320px] md:w-[320px] lg:w-[340px] xl:w-[360px]">
      {/* 助手头部看板（固定顶部，永不跟随左侧画布滚动） */}
      <div className="flex flex-shrink-0 select-none items-center justify-between border-b border-default bg-surface-2 px-3 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-default bg-brand-muted">
            <Sparkles className="h-4 w-4 text-brand" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="flex items-center gap-1 text-[14px] font-bold text-primary">
              <span className="truncate">锐宝智能助手</span>
              <span className="flex-shrink-0 rounded bg-brand-muted px-1 py-0.2 text-[9px] font-semibold text-brand">
                决策大脑
              </span>
            </h3>
            <p className="flex truncate items-center gap-1 text-[10px] text-secondary">
              <span>财税风控智能专家</span>
              {isDeepMode && (
                <span className="rounded border border-success/30 bg-success/10 px-1 text-[9px] text-success">
                  深度穿透
                </span>
              )}
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="flex-shrink-0 cursor-pointer rounded-md p-1 text-muted transition-colors hover:bg-surface hover:text-primary"
          title="收起智能助手"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* 消息对话区域（独立内部滚动，物理隔离） */}
      <div className="scrollbar-hide flex-1 space-y-3 overflow-y-auto overscroll-contain p-3">
        <div className="my-0.5 text-center text-[10px] font-mono-num text-muted">
          当前会话 · AI 响应仅来自已接通的后端接口
        </div>

        {messages.map((msg) => {
          const isAi = msg.sender === 'ai';
          return (
            <div
              key={msg.id}
              className={`flex max-w-[98%] gap-2 ${isAi ? '' : 'ml-auto self-end flex-row-reverse'}`}
            >
              {/* 头像 */}
              <div className={`mt-1 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md border ${
                isAi 
                  ? 'border-default bg-surface-2 text-brand' 
                  : 'border-brand/30 bg-brand-muted text-primary'
              }`}>
                {isAi ? <Bot className="h-3.5 w-3.5" /> : <User className="h-3.5 w-3.5" />}
              </div>

              {/* 消息气泡 */}
              <div className="flex min-w-0 flex-1 flex-col space-y-1">
                <div className={`rounded-xl border p-2.5 text-[12px] leading-relaxed ${
                  isAi
                    ? 'rounded-tl-sm border-default bg-surface-2 text-primary'
                    : 'rounded-tr-sm border-brand/30 bg-brand-muted text-primary'
                }`}>
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                  {isAi && <AiMetadataPanel metadata={msg.aiMetadata} />}

                  {/* 消息内推荐操作快捷按钮 */}
                  {msg.suggestedActions && msg.suggestedActions.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap gap-1 border-t border-default pt-2">
                      {msg.suggestedActions.map((action, aIdx) => (
                        <button
                          key={aIdx}
                          onClick={() => onSendMessage(action)}
                          className="cursor-pointer rounded border border-brand/30 bg-brand-muted px-2 py-0.5 text-[10.5px] text-brand transition-colors hover:bg-brand/20"
                        >
                          {action}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between px-1 text-[9.5px] font-mono-num text-muted">
                  <span>{msg.timestamp}</span>
                  {isAi && (
                    <button
                      onClick={() => copyToClipboard(msg.id, msg.content)}
                      className="flex cursor-pointer items-center gap-1 hover:text-brand"
                    >
                      {copiedId === msg.id ? (
                        <>
                          <CheckCircle className="h-2.5 w-2.5 text-success" />
                          <span className="text-success">已复制</span>
                        </>
                      ) : (
                        <>
                          <Copy className="h-2.5 w-2.5" />
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
          <div className="flex max-w-[95%] gap-2">
            <div className="mt-1 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md border border-default bg-surface-2 text-brand">
              <RefreshCw className="h-3.5 w-3.5 animate-spin text-brand" />
            </div>
            <div className="flex items-center gap-1.5 rounded-xl rounded-tl-sm border border-brand/30 bg-brand-muted p-2.5 text-[11.5px] text-brand">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand"></span>
              <span>正在穿透工程财税数仓与台账...</span>
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* 快捷指令预设 */}
      <div className="border-t border-default bg-bg px-2.5 py-2">
        <div className="mb-1 flex items-center gap-1 text-[10px] font-bold text-muted">
          <HelpCircle className="h-2.5 w-2.5 text-brand" />
          <span>快捷财税指令</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {quickPrompts.map((prompt, pIdx) => (
            <button
              key={pIdx}
              onClick={() => onSendMessage(prompt)}
              className="cursor-pointer rounded-full border border-default bg-surface-2 px-2 py-0.5 text-[10.5px] text-secondary transition-colors hover:border-brand/30 hover:text-brand"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      {/* 底部输入框 */}
      <div className="border-t border-default bg-surface-2 p-2.5">
        <div className="relative flex items-end overflow-hidden rounded-lg border border-default bg-bg transition-colors focus-within:border-brand">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="向锐宝助手提问（如：跨区预缴税率、四流合一合规）..."
            rows={2}
            className="w-full resize-none border-none bg-transparent p-2 text-[12px] text-primary placeholder:text-muted focus:ring-0 font-mono-num"
          />
          <button
            onClick={handleSend}
            disabled={!inputValue.trim() || isAiThinking}
            className="m-1 cursor-pointer rounded-md bg-brand p-2 font-bold text-white transition-colors hover:bg-brand-hover disabled:opacity-30"
            title="发送指令"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}
