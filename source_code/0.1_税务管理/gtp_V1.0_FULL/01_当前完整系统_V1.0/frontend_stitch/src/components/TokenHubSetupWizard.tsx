import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  X,
  KeyRound,
  Check,
  CheckCircle2,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
  Eye,
  EyeOff,
  Sparkles,
  CircleHelp,
  Trash2,
  Gift,
  UserPlus,
  Wrench,
} from 'lucide-react';
import {
  ApiError,
  fetchTokenHubSetupStatus,
  revokeTokenHubApiKey,
  saveTokenHubApiKey,
  testTokenHubApiKey,
  type TokenHubSetupStatus,
} from '../api';

interface TokenHubSetupWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: (status: TokenHubSetupStatus) => void;
}

type WizardStep = 'register' | 'activate' | 'free-credit' | 'create-key';

const STEPS: ReadonlyArray<{ id: WizardStep; title: string; subTitle: string }> = [
  {
    id: 'register',
    title: '注册用户',
    subTitle: '手机号或微信注册',
  },
  {
    id: 'activate',
    title: '开通服务',
    subTitle: '开通 TokenHub',
  },
  {
    id: 'free-credit',
    title: '领取免费额度',
    subTitle: '获取免费 Token',
  },
  {
    id: 'create-key',
    title: '建立 API',
    subTitle: '填入本系统',
  },
];

const TOKENHUB_URL = 'https://console.cloud.tencent.com/tokenhub';

interface StepLinkProps {
  href: string;
  label: string;
  hint?: string;
}

function StepLink({ href, label, hint }: StepLinkProps) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-brand)]/35 bg-[var(--color-brand)]/10 px-3 py-1.5 text-[12px] font-semibold text-[var(--color-brand)] transition-colors hover:bg-[var(--color-brand)]/20"
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {label}
      {hint ? <span className="text-[10px] font-normal text-[var(--color-text-secondary)]">· {hint}</span> : null}
    </a>
  );
}

function StepListItem({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 rounded-lg border border-default bg-[var(--color-surface)]/70 p-2.5 text-[12.5px] leading-relaxed text-[var(--color-text-primary)]">
      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-success)]" />
      <span>{children}</span>
    </li>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '请求失败，请稍后重试。';
}

export function TokenHubSetupWizard({ isOpen, onClose, onSaved }: TokenHubSetupWizardProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [status, setStatus] = useState<TokenHubSetupStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; models: string[] } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState('');
  const [savedFlash, setSavedFlash] = useState(false);

  const currentStep = STEPS[stepIndex] ?? STEPS[0];
  const isFirstStep = stepIndex === 0;
  const isLastStep = currentStep?.id === 'create-key';

  const reloadStatus = useCallback(async (signal?: AbortSignal) => {
    setLoadingStatus(true);
    setError('');
    try {
      const result = await fetchTokenHubSetupStatus(signal);
      setStatus(result);
      return result;
    } catch (err) {
      if ((err as { name?: string })?.name === 'AbortError') return null;
      setError(errorText(err));
      return null;
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return undefined;
    const controller = new AbortController();
    void reloadStatus(controller.signal);
    return () => controller.abort();
  }, [isOpen, reloadStatus]);

  useEffect(() => {
    if (!isOpen) {
      setStepIndex(0);
      setApiKey('');
      setShowKey(false);
      setTestResult(null);
      setError('');
      setSavedFlash(false);
    }
  }, [isOpen]);

  const handlePrev = () => {
    setError('');
    setStepIndex(idx => Math.max(0, idx - 1));
  };

  const handleNext = () => {
    setError('');
    setStepIndex(idx => Math.min(STEPS.length - 1, idx + 1));
  };

  const handleTest = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setError('请先粘贴 API Key。');
      return;
    }
    setTesting(true);
    setError('');
    setTestResult(null);
    try {
      const result = await testTokenHubApiKey({ apiKey: trimmed });
      if (result.ok) {
        setTestResult({
          ok: true,
          message: result.models.length > 0
            ? `连接成功（HTTP ${result.statusCode}），已识别 ${result.models.length} 个可用模型。`
            : `连接成功（HTTP ${result.statusCode}）。`,
          models: result.models,
        });
      } else {
        setTestResult({
          ok: false,
          message: result.error || `TokenHub 返回 HTTP ${result.statusCode}`,
          models: [],
        });
        if (result.formatWarning) {
          setError(result.formatWarning);
        }
      }
    } catch (err) {
      setTestResult({ ok: false, message: errorText(err), models: [] });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setError('请先粘贴 API Key。');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const result = await saveTokenHubApiKey({ apiKey: trimmed });
      setTestResult({
        ok: true,
        message: `已保存 TokenHub 端点（ID ${result.endpoint.id}）。`,
        models: result.models,
      });
      setSavedFlash(true);
      const refreshed = await reloadStatus();
      if (refreshed) onSaved?.(refreshed);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const handleRevoke = async () => {
    if (!status?.hasUserKey) return;
    const confirmed = typeof window !== 'undefined'
      ? window.confirm('确认撤销已保存的 TokenHub API Key？撤销后 AI 助手将停止调用云端模型。')
      : true;
    if (!confirmed) return;
    setRevoking(true);
    setError('');
    try {
      await revokeTokenHubApiKey();
      setTestResult(null);
      setApiKey('');
      await reloadStatus();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRevoking(false);
    }
  };

  const summaryBadge = useMemo(() => {
    if (!status) return { label: '加载中…', tone: 'muted' as const };
    if (status.configured && status.hasUserKey && status.endpoint?.enabled) {
      return { label: '已启用', tone: 'success' as const };
    }
    if (status.configured && status.endpoint) {
      return { label: '已保存但未启用', tone: 'warning' as const };
    }
    if (status.envFallback) {
      return { label: '使用环境变量兜底', tone: 'warning' as const };
    }
    return { label: '尚未配置', tone: 'danger' as const };
  }, [status]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tokenhub-wizard-title"
    >
      <div className="surface-card flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl text-primary shadow-2xl">
        <div className="flex items-center justify-between border-b border-default px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-[var(--color-brand)]/35 bg-[var(--color-brand)]/10 text-[var(--color-brand)]">
              <KeyRound className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h2 id="tokenhub-wizard-title" className="truncate text-[17px] font-bold text-primary">
                腾讯云 TokenHub 配置向导
              </h2>
              <p className="truncate text-[11.5px] text-secondary">
                四步完成 AI 大模型配置
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-lg p-1.5 text-secondary transition-colors hover:bg-surface-2 hover:text-primary"
            aria-label="关闭"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="border-b border-default bg-[var(--color-surface-2)]/60 px-6 py-3.5">
          <div className="flex flex-wrap items-center gap-2.5">
            {STEPS.map((step, idx) => {
              const isActive = idx === stepIndex;
              const isCompleted = idx < stepIndex;
              return (
                <div key={step.id} className="flex items-center gap-2.5">
                  <div
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-[12px] font-bold transition-colors ${
                      isActive
                        ? 'bg-[var(--color-brand)] text-white shadow'
                        : isCompleted
                          ? 'bg-[var(--color-success)] text-white'
                          : 'bg-[var(--color-surface)] text-secondary border border-default'
                    }`}
                  >
                    {isCompleted ? <Check className="h-3.5 w-3.5" /> : idx + 1}
                  </div>
                  <div className="min-w-0">
                    <p className={`text-[12.5px] font-semibold ${isActive ? 'text-primary' : 'text-secondary'}`}>
                      {step.title}
                    </p>
                    <p className="hidden truncate text-[10.5px] text-secondary md:block">{step.subTitle}</p>
                  </div>
                  {idx < STEPS.length - 1 ? (
                    <ChevronRight className="mx-1 h-4 w-4 flex-shrink-0 text-secondary opacity-40" />
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>

        <div className="scrollbar-hide flex-1 space-y-4 overflow-y-auto px-6 py-5 text-[13px] text-primary">
          {/* 第一步：注册用户 */}
          {currentStep?.id === 'register' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <UserPlus className="h-4 w-4 text-[var(--color-brand)]" />
                  第一步 · 注册用户
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  TokenHub 支持个人用户直接注册，无需企业认证。打开腾讯云 TokenHub 控制台完成注册。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  打开 <StepLink href={TOKENHUB_URL} label="腾讯云 TokenHub" hint="console.cloud.tencent.com/tokenhub" />
                </StepListItem>
                <StepListItem>
                  点击「注册/登录」，选择**个人用户**，支持**微信**或**手机号**注册
                </StepListItem>
                <StepListItem>
                  无需企业认证，无需实名认证，注册即可使用
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-[var(--color-info)]/30 bg-[var(--color-info)]/10 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <CircleHelp className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-info)]" />
                  <span>
                    若已有腾讯云账号，可直接登录复用。
                  </span>
                </p>
              </div>
            </section>
          )}

          {/* 第二步：开通服务 */}
          {currentStep?.id === 'activate' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <Wrench className="h-4 w-4 text-[var(--color-brand)]" />
                  第二步 · 开通服务
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  登录后进入 TokenHub 控制台，开通大模型服务。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  登录后进入 TokenHub 控制台
                </StepListItem>
                <StepListItem>
                  找到「开通服务」或「立即开通」按钮
                </StepListItem>
                <StepListItem>
                  按提示完成服务开通（通常即时生效）
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-warning)]" />
                  <span>
                    开通服务后才能领取免费额度，请先完成此步骤。
                  </span>
                </p>
              </div>
            </section>
          )}

          {/* 第三步：领取免费额度 */}
          {currentStep?.id === 'free-credit' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <Gift className="h-4 w-4 text-[var(--color-brand)]" />
                  第三步 · 领取免费模型额度
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  TokenHub 为新用户提供免费额度，领取后即可使用，无需付费。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  进入控制台后，找到「免费额度」或「新用户礼包」
                </StepListItem>
                <StepListItem>
                  点击「立即领取」
                </StepListItem>
                <StepListItem>
                  领取成功后即可使用免费 Token
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-[var(--color-success)]/30 bg-[var(--color-success)]/10 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-success)]" />
                  <span>
                    免费额度内不产生任何费用，用完后再按量付费或等待月度额度重置。
                  </span>
                </p>
              </div>
            </section>
          )}

          {/* 第四步：建立 API 并配置到系统 */}
          {currentStep?.id === 'create-key' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <KeyRound className="h-4 w-4 text-[var(--color-brand)]" />
                  第四步 · 建立 API 并配置到系统
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  在 TokenHub 创建 API Key，然后填入本系统完成配置。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  在 TokenHub 控制台进入「API 密钥管理」
                </StepListItem>
                <StepListItem>
                  创建新的 API Key（或使用已有的）
                </StepListItem>
                <StepListItem>
                  将 API Key 填入下方输入框
                </StepListItem>
              </ol>

              <div
                className={`rounded-xl border px-3.5 py-2.5 text-[12px] font-semibold ${
                  summaryBadge.tone === 'success'
                    ? 'border-[var(--color-success)]/40 bg-[var(--color-success)]/10 text-[var(--color-success)]'
                    : summaryBadge.tone === 'warning'
                      ? 'border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 text-[var(--color-warning)]'
                      : summaryBadge.tone === 'danger'
                        ? 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 text-[var(--color-danger)]'
                        : 'border-default bg-[var(--color-surface-2)] text-secondary'
                }`}
              >
                当前 TokenHub 状态：{summaryBadge.label}
                {status?.endpoint ? (
                  <span className="ml-2 font-normal text-secondary">
                    · 端点 #{status.endpoint.id} · {status.endpoint.model} · 更新于 {formatDateTime(status.endpoint.updatedAt)}
                  </span>
                ) : null}
              </div>

              <div className="space-y-2">
                <label htmlFor="tokenhub-api-key" className="flex items-center justify-between text-[12.5px] font-semibold text-primary">
                  <span>API Key</span>
                  <button
                    type="button"
                    onClick={() => setShowKey(prev => !prev)}
                    className="inline-flex items-center gap-1 text-[11px] font-normal text-secondary hover:text-primary"
                  >
                    {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    {showKey ? '隐藏' : '显示'}
                  </button>
                </label>
                <textarea
                  id="tokenhub-api-key"
                  rows={3}
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setTestResult(null);
                    setError('');
                  }}
                  placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                  maxLength={4096}
                  className="w-full resize-none rounded-lg border border-default bg-[var(--color-surface)] px-3 py-2 font-mono text-[12px] text-primary focus:border-[var(--color-brand)] focus:outline-none"
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="text-[11px] text-secondary">
                  粘贴后点击「测试连接」验证 Key 可用，再点「保存到本系统」启用 AI 决策中心。
                </p>
              </div>

              {testResult && (
                <div
                  className={`rounded-lg border px-3 py-2 text-[12px] ${
                    testResult.ok
                      ? 'border-[var(--color-success)]/40 bg-[var(--color-success)]/10 text-[var(--color-success)]'
                      : 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 text-[var(--color-danger)]'
                  }`}
                  role={testResult.ok ? 'status' : 'alert'}
                >
                  <p className="font-semibold">{testResult.ok ? '✅ 连接测试通过' : '❌ 连接测试失败'}</p>
                  <p className="mt-0.5 text-[11.5px] leading-relaxed">{testResult.message}</p>
                  {testResult.models.length > 0 ? (
                    <p className="mt-1 text-[11px] text-secondary">
                      已识别模型示例：{testResult.models.slice(0, 3).join('、')}
                    </p>
                  ) : null}
                </div>
              )}

              {error && (
                <div className="rounded-lg border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-[12px] text-[var(--color-danger)]" role="alert">
                  <AlertTriangle className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />
                  {error}
                </div>
              )}

              {status?.hasUserKey ? (
                <button
                  type="button"
                  onClick={() => void handleRevoke()}
                  disabled={revoking || saving}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-danger)]/35 px-3 py-1.5 text-[12px] font-semibold text-[var(--color-danger)] hover:bg-[var(--color-danger)]/10 disabled:opacity-50"
                >
                  {revoking ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  撤销已保存的 API Key
                </button>
              ) : null}
            </section>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-default px-6 py-4">
          <div className="text-[11px] text-secondary">
            {loadingStatus ? '正在读取当前 TokenHub 配置…' : '配置变更会立即应用到 AI 智能决策中心。'}
          </div>
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={handlePrev}
              disabled={isFirstStep || saving}
              className="inline-flex items-center gap-1 rounded-lg border border-default bg-surface-2 px-3 py-1.5 text-[12px] font-medium text-primary transition-colors hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              上一步
            </button>

            {isLastStep ? (
              <>
                <button
                  type="button"
                  onClick={() => void handleTest()}
                  disabled={testing || saving || !apiKey.trim()}
                  className="inline-flex items-center gap-1 rounded-lg border border-default bg-surface-2 px-3 py-1.5 text-[12px] font-semibold text-primary hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {testing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                  测试连接
                </button>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving || !apiKey.trim() || (testResult ? !testResult.ok : true)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-brand)] px-4 py-1.5 text-[12.5px] font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? <RefreshCw className="h-4 w-4 animate-spin" /> : savedFlash ? <Check className="h-4 w-4" /> : <KeyRound className="h-4 w-4" />}
                  {savedFlash ? '已保存！' : saving ? '正在保存…' : '保存到本系统'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handleNext}
                className="inline-flex items-center gap-1 rounded-lg bg-[var(--color-brand)] px-4 py-1.5 text-[12.5px] font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)]"
              >
                下一步
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default TokenHubSetupWizard;
