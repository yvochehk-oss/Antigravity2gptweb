import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  X,
  Check,
  CheckCircle2,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
  Server,
  CircleHelp,
  Wifi,
  Globe,
  Lock,
} from 'lucide-react';
import {
  ApiError,
  fetchRagSettings,
  saveRagSettings,
  testRagSettings,
  type RagServiceSettings,
} from '../api';

interface RagSetupWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: (settings: RagServiceSettings) => void;
}

type WizardStep = 'overview' | 'deploy' | 'firewall' | 'connect';

const STEPS: ReadonlyArray<{ id: WizardStep; title: string; subTitle: string }> = [
  {
    id: 'overview',
    title: '理解 RAG 知识库',
    subTitle: '了解 RAG 系统在财税智控中的角色',
  },
  {
    id: 'deploy',
    title: '部署 RAG 服务',
    subTitle: '在内网或云端启动 RAG 服务',
  },
  {
    id: 'firewall',
    title: '网络与共享密钥',
    subTitle: '确认 Tax 与 RAG 之间可达且凭据一致',
  },
  {
    id: 'connect',
    title: '填入地址并启用',
    subTitle: '把 RAG 地址粘贴到下方输入框并启用同步',
  },
];

const RAG_DEFAULT_URL = 'http://127.0.0.1:8922';
const RAG_DOCKER_GUIDE_URL = 'https://console.cloud.tencent.com/';
const RAG_PROJECT_DOCS_URL = 'https://github.com/';

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

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '请求失败，请稍后重试。';
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

export function RagSetupWizard({ isOpen, onClose, onSaved }: RagSetupWizardProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [settings, setSettings] = useState<RagServiceSettings | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [ragUrl, setRagUrl] = useState(RAG_DEFAULT_URL);
  const [approvePrivate, setApprovePrivate] = useState(false);
  const [testResult, setTestResult] = useState<RagServiceSettings | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [savedFlash, setSavedFlash] = useState(false);

  const currentStep = STEPS[stepIndex] ?? STEPS[0];
  const isFirstStep = stepIndex === 0;
  const isLastStep = currentStep?.id === 'connect';

  const reloadSettings = useCallback(async (signal?: AbortSignal) => {
    setLoadingStatus(true);
    setError('');
    try {
      const result = await fetchRagSettings(signal);
      setSettings(result);
      if (result.url) setRagUrl(result.url);
      setApprovePrivate(result.approvedPrivate);
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
    void reloadSettings(controller.signal);
    return () => controller.abort();
  }, [isOpen, reloadSettings]);

  useEffect(() => {
    if (!isOpen) {
      setStepIndex(0);
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
    const trimmed = ragUrl.trim();
    if (!trimmed) {
      setError('请先填入 RAG 服务地址。');
      return;
    }
    setTesting(true);
    setError('');
    setTestResult(null);
    try {
      const result = await testRagSettings({
        url: trimmed,
        approvePrivate,
      });
      setTestResult(result);
      if (!result.ok && result.error) {
        setError(result.error);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    const trimmed = ragUrl.trim();
    if (!trimmed) {
      setError('请先填入 RAG 服务地址。');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const result = await saveRagSettings({
        url: trimmed,
        approvePrivate,
      });
      setTestResult(result);
      setSavedFlash(true);
      const refreshed = await reloadSettings();
      if (refreshed) onSaved?.(refreshed);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const summaryBadge = useMemo(() => {
    if (!settings) return { label: '加载中…', tone: 'muted' as const };
    if (settings.ok && settings.configured && settings.projects.length > 0) {
      return { label: '已连接', tone: 'success' as const };
    }
    if (settings.configured && settings.url) {
      return { label: '已保存地址，未通过测试', tone: 'warning' as const };
    }
    return { label: '尚未配置', tone: 'danger' as const };
  }, [settings]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rag-wizard-title"
    >
      <div className="surface-card flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl text-primary shadow-2xl">
        <div className="flex items-center justify-between border-b border-default px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-[var(--color-brand)]/35 bg-[var(--color-brand)]/10 text-[var(--color-brand)]">
              <Server className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h2 id="rag-wizard-title" className="truncate text-[17px] font-bold text-primary">
                RAG 知识库服务连接配置向导
              </h2>
              <p className="truncate text-[11.5px] text-secondary">
                教你在内网或云端部署 RAG 服务，并把地址接入本系统 Tax 后端
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
          {currentStep?.id === 'overview' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <Server className="h-4 w-4 text-[var(--color-brand)]" />
                  第 1 步 · 理解 RAG 在本系统的角色
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  RAG（Retrieval-Augmented Generation，检索增强生成）是本系统「项目工程库」「AI 财税决策中心」的本地证据库。它负责把项目合同、票据、政策文件等结构化与非结构化材料向量化，供 AI 在做风险判断时引用。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  RAG 与 TokenHub 的区别：RAG 提供**证据**，TokenHub（云端大模型）做**推理**。前者保障数据可信，后者保障回答质量。
                </StepListItem>
                <StepListItem>
                  Tax 后端通过 HTTP 调用 RAG 的 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">/api/rag/*</code> 接口，由 RAG 负责把检索到的证据回传给 Tax。
                </StepListItem>
                <StepListItem>
                  RAG 通常部署在与 Tax 同一内网或同一台服务器，**默认端口 8922**；可通过环境变量 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">RAG_PORT</code> 修改。
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-[var(--color-info)]/30 bg-[var(--color-info)]/10 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <CircleHelp className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-info)]" />
                  <span>
                    如果你使用「单机一体化部署」（Tax 与 RAG 在同一台电脑），可直接使用默认地址 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">http://127.0.0.1:8922</code>，无需修改。
                  </span>
                </p>
              </div>
            </section>
          )}

          {currentStep?.id === 'deploy' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <Wifi className="h-4 w-4 text-[var(--color-brand)]" />
                  第 2 步 · 部署 RAG 服务
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  RAG 服务可以部署在 Tax 所在的同一台电脑、公司内网的另一台服务器，或腾讯云/阿里云的云主机。三种方式对网络和安全的要求略有不同。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  <strong>单机一体化</strong>：Tax 与 RAG 同机部署，使用默认 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">127.0.0.1:8922</code>；最简单，无需额外网络配置。
                </StepListItem>
                <StepListItem>
                  <strong>内网分离</strong>：RAG 部署在另一台内网服务器，地址形如 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">http://192.168.x.x:8922</code>；需要 Tax 机器可访问该 IP，并勾选下方「批准本机/公司内网 RAG 地址」。
                </StepListItem>
                <StepListItem>
                  <strong>云端部署</strong>：RAG 部署在腾讯云 CVM/阿里云 ECS 等云主机；需使用 HTTPS + 公网域名（如 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">https://rag.example.com</code>），并在防火墙放行 8922 端口。
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-warning)]" />
                  <span>
                    部署完成后，请在 RAG 所在机器上执行 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">curl http://127.0.0.1:8922/api/health</code>，返回 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">{'"ok": true'}</code> 即表示启动成功。
                  </span>
                </p>
              </div>
            </section>
          )}

          {currentStep?.id === 'firewall' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <ShieldCheck className="h-4 w-4 text-[var(--color-brand)]" />
                  第 3 步 · 网络与共享密钥对齐
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  Tax 与 RAG 通过共享密钥互信：Tax 后端的环境变量 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">RAG_SHARED_API_KEY</code> 必须与 RAG 端的 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">SHARED_API_KEY</code> 一致。
                </p>
              </header>
              <ol className="space-y-2">
                <StepListItem>
                  <strong>防火墙放行</strong>：确保 Tax 与 RAG 之间的 8922 端口互相可达，云主机需在安全组规则中放行 8922。
                </StepListItem>
                <StepListItem>
                  <strong>共享密钥</strong>：两端 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">SHARED_API_KEY</code> / <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">RAG_SHARED_API_KEY</code> 必须完全一致；不一致会导致 Tax 收到 401。
                </StepListItem>
                <StepListItem>
                  <strong>HTTPS / 私网批准</strong>：使用 HTTPS 时直接填写；如果是内网 IP（<code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">192.168.*</code>、<code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">10.*</code> 等），必须勾选下方「批准本机/公司内网 RAG 地址」。
                </StepListItem>
              </ol>
              <div className="rounded-xl border border-default bg-[var(--color-surface-2)]/60 p-3.5">
                <p className="flex items-start gap-2 text-[12px] leading-relaxed text-primary">
                  <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-text-muted)]" />
                  <span>
                    共享密钥只会保存在 Tax 后端环境变量中，**不会**在本向导中显示或提交。配置变更后建议重启 Tax 后端让环境变量生效。
                  </span>
                </p>
              </div>
            </section>
          )}

          {currentStep?.id === 'connect' && (
            <section className="space-y-4">
              <header className="space-y-1.5">
                <h3 className="flex items-center gap-2 text-[15px] font-bold text-primary">
                  <Globe className="h-4 w-4 text-[var(--color-brand)]" />
                  第 4 步 · 把 RAG 地址填入本系统并启用
                </h3>
                <p className="text-[12.5px] leading-relaxed text-secondary">
                  把 RAG 服务的完整地址粘贴到下方输入框，点击「测试连接」验证可达，再点「保存到本系统」持久化地址。
                </p>
              </header>

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
                当前 RAG 连接状态：{summaryBadge.label}
                {settings?.url ? (
                  <span className="ml-2 font-normal text-secondary">
                    · 地址 {settings.host || settings.url}
                    {settings.lastTestedAt ? ` · 最近测试 ${formatDateTime(settings.lastTestedAt)}` : ''}
                  </span>
                ) : null}
              </div>

              <div className="space-y-2">
                <label htmlFor="rag-service-url" className="text-[12.5px] font-semibold text-primary">
                  RAG 服务 IP 地址或域名
                </label>
                <input
                  id="rag-service-url"
                  type="url"
                  value={ragUrl}
                  onChange={(event) => {
                    setRagUrl(event.target.value);
                    setTestResult(null);
                    setError('');
                  }}
                  placeholder={RAG_DEFAULT_URL}
                  maxLength={300}
                  autoComplete="off"
                  className="w-full rounded-lg border border-default bg-[var(--color-surface)] px-3 py-2 font-mono text-[12px] text-primary focus:border-[var(--color-brand)] focus:outline-none"
                />
                <p className="text-[11px] text-secondary">
                  支持 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[10.5px]">http://</code> 与 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[10.5px]">https://</code>；内网地址需在下方勾选「批准本机/公司内网 RAG 地址」。
                </p>
              </div>

              <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-default bg-[var(--color-surface)]/70 p-3">
                <input
                  type="checkbox"
                  checked={approvePrivate}
                  onChange={(event) => {
                    setApprovePrivate(event.target.checked);
                    setTestResult(null);
                    setError('');
                  }}
                  className="mt-0.5 h-4 w-4 accent-[var(--color-brand)]"
                />
                <span className="text-[12.5px] leading-relaxed text-primary">
                  <span className="block font-semibold">批准本机/公司内网 RAG 地址</span>
                  <span className="mt-0.5 block text-[11px] text-secondary">
                    仅当 RAG 位于本机或可信内网时勾选；系统会记录 DNS 地址快照，地址变化后要求重新批准。
                  </span>
                </span>
              </label>

              {testResult && (
                <div
                  className={`rounded-lg border px-3 py-2 text-[12px] ${
                    testResult.ok
                      ? 'border-[var(--color-success)]/40 bg-[var(--color-success)]/10 text-[var(--color-success)]'
                      : 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 text-[var(--color-danger)]'
                  }`}
                  role={testResult.ok ? 'status' : 'alert'}
                >
                  <p className="font-semibold">
                    {testResult.ok
                      ? `✅ 连接测试通过${testResult.ragVersion ? ` · RAG ${testResult.ragVersion}` : ''}`
                      : '❌ 连接测试失败'}
                  </p>
                  <p className="mt-0.5 text-[11.5px] leading-relaxed">
                    {testResult.ok
                      ? `已发现 ${testResult.projects.length} 个 RAG 项目；点击底部「保存到本系统」后会持久化地址。`
                      : (testResult.error || '无法连接到 RAG 服务，请检查地址与共享密钥。')}
                  </p>
                </div>
              )}

              {error && (
                <div className="rounded-lg border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-[12px] text-[var(--color-danger)]" role="alert">
                  <AlertTriangle className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />
                  {error}
                </div>
              )}

              <p className="text-[10.5px] text-secondary">
                共享密钥（shared key）仅保存在 Tax 服务端环境变量 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[10.5px]">RAG_SHARED_API_KEY</code> 中，不会在此页面显示或发送。
              </p>
            </section>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-default px-6 py-4">
          <div className="text-[11px] text-secondary">
            {loadingStatus ? '正在读取当前 RAG 配置…' : '保存后 Tax 后端会立即使用新地址进行 RAG 同步。'}
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
                  disabled={testing || saving || !ragUrl.trim()}
                  className="inline-flex items-center gap-1 rounded-lg border border-default bg-surface-2 px-3 py-1.5 text-[12px] font-semibold text-primary hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {testing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                  测试连接
                </button>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving || !ragUrl.trim()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-brand)] px-4 py-1.5 text-[12.5px] font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? <RefreshCw className="h-4 w-4 animate-spin" /> : savedFlash ? <Check className="h-4 w-4" /> : <Server className="h-4 w-4" />}
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

export default RagSetupWizard;
