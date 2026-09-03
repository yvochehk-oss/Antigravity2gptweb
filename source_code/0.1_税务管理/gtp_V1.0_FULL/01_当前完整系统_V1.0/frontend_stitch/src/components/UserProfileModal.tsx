import React, { useState, useEffect, useRef } from 'react';
import { 
  X, User, Mail, Phone, Lock, Camera, CheckCircle, AlertCircle, 
  Send, KeyRound, ShieldCheck, RefreshCw, Smartphone
} from 'lucide-react';

interface UserProfile {
  id: number;
  username: string;
  nickname: string;
  avatar_url: string;
  email: string;
  email_verified: boolean;
  phone: string;
  phone_verified: boolean;
  role: string;
  created_at: string | null;
}

interface UserProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProfileUpdated?: (profile: UserProfile) => void;
}

export const UserProfileModal: React.FC<UserProfileModalProps> = ({ isOpen, onClose, onProfileUpdated }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'profile' | 'security' | 'password'>('profile');
  
  // 昵称修改
  const [nickname, setNickname] = useState('');
  const [savingNickname, setSavingNickname] = useState(false);

  // 绑定邮箱/手机
  const [bindChannel, setBindChannel] = useState<'email' | 'sms'>('email');
  const [bindTarget, setBindTarget] = useState('');
  const [bindCode, setBindCode] = useState('');
  const [bindCountdown, setBindCountdown] = useState(0);
  const [bindLoading, setBindLoading] = useState(false);

  // 修改密码
  const [pwdChannel, setPwdChannel] = useState<'email' | 'sms'>('email');
  const [pwdCode, setPwdCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pwdCountdown, setPwdCountdown] = useState(0);
  const [pwdLoading, setPwdLoading] = useState(false);

  // 提示信息
  const [msg, setMsg] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 加载个人资料
  const fetchProfile = async (): Promise<UserProfile | null> => {
    try {
      setLoading(true);
      const res = await fetch('/api/v1/user-center/profile');
      if (res.ok) {
        const data: UserProfile = await res.json();
        setProfile(data);
        setNickname(data.nickname || data.username);
        onProfileUpdated?.(data);
        return data;
      }
    } catch (err) {
      console.error('获取个人资料失败:', err);
    } finally {
      setLoading(false);
    }
    return null;
  };


  useEffect(() => {
    if (isOpen) {
      setMsg(null);
      fetchProfile();
    }
  }, [isOpen]);

  // 倒计时计时器
  useEffect(() => {
    let timer: any;
    if (bindCountdown > 0) {
      timer = setInterval(() => setBindCountdown(c => c - 1), 1000);
    }
    return () => clearInterval(timer);
  }, [bindCountdown]);

  useEffect(() => {
    let timer: any;
    if (pwdCountdown > 0) {
      timer = setInterval(() => setPwdCountdown(c => c - 1), 1000);
    }
    return () => clearInterval(timer);
  }, [pwdCountdown]);

  if (!isOpen) return null;

  // 上传头像
  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
      setMsg({ type: 'info', text: '正在上传头像…' });
      const res = await fetch('/api/v1/user-center/avatar', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (res.ok) {
        setMsg({ type: 'success', text: '头像更新成功！' });
        fetchProfile();
      } else {
        setMsg({ type: 'error', text: data.detail || '头像上传失败' });
      }
    } catch (err: any) {
      setMsg({ type: 'error', text: '网络异常，头像上传失败' });
    }
  };

  // 保存昵称并自动关闭弹窗
  const handleSaveNickname = async () => {
    if (!nickname.trim()) return;
    try {
      setSavingNickname(true);
      const res = await fetch('/api/v1/user-center/nickname', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname: nickname.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setMsg({ type: 'success', text: '昵称修改成功！' });
        const updated = await fetchProfile();
        if (updated) {
          onProfileUpdated?.(updated);
        }
        // 成功后自动关闭弹窗
        setTimeout(() => {
          onClose();
        }, 300);
      } else {
        setMsg({ type: 'error', text: data.detail || '昵称修改失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: '网络异常，昵称修改失败' });
    } finally {
      setSavingNickname(false);
    }
  };


  // 发送绑定验证码
  const handleSendBindCode = async () => {
    if (!bindTarget.trim()) {
      setMsg({ type: 'error', text: `请输入要绑定的${bindChannel === 'email' ? '邮箱地址' : '手机号码'}` });
      return;
    }
    try {
      setMsg({ type: 'info', text: '正在发送验证码…' });
      const res = await fetch('/api/v1/user-center/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target: bindTarget.trim(),
          channel: bindChannel,
          purpose: bindChannel === 'email' ? 'bind_email' : 'bind_phone',
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setBindCountdown(60);
        setMsg({ 
          type: 'success', 
          text: data.debug_code 
            ? `验证码发送成功！[调试验证码: ${data.debug_code}]` 
            : data.message 
        });
      } else {
        setMsg({ type: 'error', text: data.detail || '验证码发送失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: '网络异常，验证码发送失败' });
    }
  };

  // 提交绑定
  const handleBindSubmit = async () => {
    if (!bindTarget.trim() || bindCode.length !== 6) {
      setMsg({ type: 'error', text: '请填写完整的目标地址及 6 位验证码' });
      return;
    }
    try {
      setBindLoading(true);
      const res = await fetch('/api/v1/user-center/bind-account', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: bindChannel,
          target: bindTarget.trim(),
          code: bindCode.trim(),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setMsg({ type: 'success', text: `成功绑定${bindChannel === 'email' ? '电子邮箱' : '手机号码'}！` });
        setBindCode('');
        setBindTarget('');
        const updated = await fetchProfile();
        if (updated) {
          onProfileUpdated?.(updated);
        }
        // 绑定成功后平滑自动关闭弹窗
        setTimeout(() => {
          onClose();
        }, 350);
      } else {
        setMsg({ type: 'error', text: data.detail || '绑定失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: '网络异常，绑定失败' });
    } finally {
      setBindLoading(false);
    }
  };

  // 发送改密验证码
  const handleSendPwdCode = async () => {
    const target = pwdChannel === 'email' ? profile?.email : profile?.phone;
    if (!target) {
      setMsg({ type: 'error', text: `您尚未绑定${pwdChannel === 'email' ? '电子邮箱' : '手机号码'}，无法接收验证码` });
      return;
    }
    try {
      setMsg({ type: 'info', text: '正在发送改密验证码…' });
      const res = await fetch('/api/v1/user-center/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target: target,
          channel: pwdChannel,
          purpose: 'change_password',
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setPwdCountdown(60);
        setMsg({ 
          type: 'success', 
          text: data.debug_code 
            ? `改密验证码已发送！[调试验证码: ${data.debug_code}]` 
            : data.message 
        });
      } else {
        setMsg({ type: 'error', text: data.detail || '验证码发送失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: '网络异常，验证码发送失败' });
    }
  };

  // 提交修改密码并自动关闭弹窗
  const handleChangePassword = async () => {
    if (pwdCode.length !== 6) {
      setMsg({ type: 'error', text: '请输入 6 位有效验证码' });
      return;
    }
    if (newPassword.length < 6) {
      setMsg({ type: 'error', text: '新密码长度至少为 6 位' });
      return;
    }
    if (newPassword !== confirmPassword) {
      setMsg({ type: 'error', text: '两次输入的新密码不一致' });
      return;
    }

    try {
      setPwdLoading(true);
      const res = await fetch('/api/v1/user-center/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: pwdChannel,
          code: pwdCode.trim(),
          new_password: newPassword,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setMsg({ type: 'success', text: '密码修改成功！请牢记新密码。' });
        setPwdCode('');
        setNewPassword('');
        setConfirmPassword('');
        // 改密成功后平滑自动关闭弹窗
        setTimeout(() => {
          onClose();
        }, 500);
      } else {
        setMsg({ type: 'error', text: data.detail || '修改密码失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: '网络异常，修改密码失败' });
    } finally {
      setPwdLoading(false);
    }
  };


  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 animate-fade-in">
      <div className="surface-card w-full max-w-lg overflow-hidden rounded-xl text-[var(--color-text-primary)] shadow-2xl">
        
        {/* 顶部 Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-2)] px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-brand)]">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-[15px] font-bold text-[var(--color-text-primary)]">个人中心与安全设置</h3>
              <p className="text-[11px] text-[var(--color-text-muted)]">独立用户数据库 · 头像与安全绑定</p>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 提示消息 */}
        {msg && (
          <div className={`flex items-center gap-2 border-b px-6 py-2.5 text-[12px] ${
            msg.type === 'success' ? 'border-[var(--color-success)]/30 bg-[var(--color-success)]/10 text-[var(--color-success)]' :
            msg.type === 'error' ? 'border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 text-[var(--color-danger)]' :
            'border-[var(--color-brand)]/30 bg-[var(--color-brand-muted)] text-[var(--color-brand)]'
          }`}>
            {msg.type === 'success' && <CheckCircle className="h-3.5 w-3.5 shrink-0" />}
            {msg.type === 'error' && <AlertCircle className="h-3.5 w-3.5 shrink-0" />}
            {msg.type === 'info' && <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin" />}
            <span>{msg.text}</span>
          </div>
        )}

        {/* Tab 导航 */}
        <div className="flex border-b border-[var(--color-border)] bg-[var(--color-surface)] text-[13px]">
          <button
            onClick={() => { setActiveTab('profile'); setMsg(null); }}
            className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-3 font-medium transition ${
              activeTab === 'profile' 
                ? 'border-[var(--color-brand)] bg-[var(--color-brand-muted)] text-[var(--color-text-primary)]' 
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            <User className="h-4 w-4" /> 基本资料与头像
          </button>
          <button
            onClick={() => { setActiveTab('security'); setMsg(null); }}
            className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-3 font-medium transition ${
              activeTab === 'security' 
                ? 'border-[var(--color-brand)] bg-[var(--color-brand-muted)] text-[var(--color-text-primary)]' 
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            <Mail className="h-4 w-4" /> 邮箱/手机绑定
          </button>
          <button
            onClick={() => { setActiveTab('password'); setMsg(null); }}
            className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-3 font-medium transition ${
              activeTab === 'password' 
                ? 'border-[var(--color-brand)] bg-[var(--color-brand-muted)] text-[var(--color-text-primary)]' 
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            <Lock className="h-4 w-4" /> 验证码改密
          </button>
        </div>

        {/* Tab 内容区 */}
        <div className="space-y-5 p-6">
          {/* TAB 1: 基本资料与头像 */}
          {activeTab === 'profile' && (
            <div className="space-y-4">
              {/* 头像展示与上传 */}
              <div className="surface-card flex items-center gap-4 rounded-xl p-4">
                <div className="group relative">
                  <img
                    src={profile?.avatar_url || '/static/avatars/default.png'}
                    alt="头像"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = 'https://api.dicebear.com/7.x/bottts/svg?seed=' + (profile?.username || 'admin');
                    }}
                    className="h-16 w-16 rounded-full border-2 border-[var(--color-border)] bg-[var(--color-surface-2)] object-cover shadow-md"
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="absolute inset-0 flex cursor-pointer items-center justify-center rounded-full bg-black/60 text-[11px] text-white opacity-0 transition group-hover:opacity-100"
                  >
                    <Camera className="h-5 w-5" />
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    onChange={handleAvatarUpload}
                    className="hidden"
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-bold text-[var(--color-text-primary)]">{profile?.nickname || profile?.username}</span>
                    <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-secondary)]">
                      {profile?.role === 'admin' ? '系统管理员' : '操作员'}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">账号：{profile?.username}</p>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="mt-2 flex cursor-pointer items-center gap-1 text-[11px] text-[var(--color-brand)] hover:underline"
                  >
                    <Camera className="h-3 w-3" /> 更换本地头像图片
                  </button>
                </div>
              </div>

              {/* 昵称修改 */}
              <div className="space-y-1.5">
                <label className="text-[12px] text-[var(--color-text-muted)]">用户昵称</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="输入新的显示昵称"
                    className="h-9 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                  <button
                    onClick={handleSaveNickname}
                    disabled={savingNickname}
                    className="h-9 cursor-pointer rounded-lg bg-[var(--color-brand)] px-4 text-[12px] font-medium text-white transition hover:bg-[var(--color-brand-hover)] disabled:opacity-50"
                  >
                    {savingNickname ? '保存中…' : '保存昵称'}
                  </button>
                </div>
              </div>

              {/* 快捷安全入口卡片 */}
              <div className="grid grid-cols-2 gap-3 border-t border-[var(--color-border)] pt-2">
                <button
                  type="button"
                  onClick={() => { setActiveTab('security'); setMsg(null); }}
                  className="surface-card group cursor-pointer rounded-xl p-3 text-left transition hover:bg-[var(--color-surface-2)]"
                >
                  <div className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--color-brand)]">
                    <Mail className="h-3.5 w-3.5" /> 邮箱/手机绑定
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-muted)] group-hover:text-[var(--color-text-primary)]">
                    {profile?.email ? `已绑: ${profile.email}` : '未绑定邮箱'}
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => { setActiveTab('password'); setMsg(null); }}
                  className="surface-card group cursor-pointer rounded-xl p-3 text-left transition hover:bg-[var(--color-surface-2)]"
                >
                  <div className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--color-brand)]">
                    <Lock className="h-3.5 w-3.5" /> 验证码改密
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-muted)] group-hover:text-[var(--color-text-primary)]">
                    通过邮箱/短信改密
                  </p>
                </button>
              </div>
            </div>
          )}


          {/* TAB 2: 邮箱/手机绑定 */}
          {activeTab === 'security' && (
            <div className="space-y-4">
              {/* 当前绑定状态卡片 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="surface-card rounded-xl p-3">
                  <div className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
                    <Mail className="h-3.5 w-3.5 text-[var(--color-brand)]" /> 电子邮箱
                  </div>
                  <p className="mt-1 truncate text-[13px] font-semibold text-[var(--color-text-primary)]">
                    {profile?.email || '未绑定'}
                  </p>
                  <span className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] ${
                    profile?.email_verified ? 'bg-[var(--color-success)]/10 text-[var(--color-success)]' : 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]'
                  }`}>
                    {profile?.email_verified ? '✓ 已验证' : '未验证'}
                  </span>
                </div>
                <div className="surface-card rounded-xl p-3">
                  <div className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
                    <Smartphone className="h-3.5 w-3.5 text-[var(--color-brand)]" /> 手机号码
                  </div>
                  <p className="mt-1 truncate text-[13px] font-semibold text-[var(--color-text-primary)]">
                    {profile?.phone || '未绑定'}
                  </p>
                  <span className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] ${
                    profile?.phone_verified ? 'bg-[var(--color-success)]/10 text-[var(--color-success)]' : 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]'
                  }`}>
                    {profile?.phone_verified ? '✓ 已验证' : '未验证'}
                  </span>
                </div>
              </div>

              {/* 绑定/换绑表单 */}
              <div className="surface-card space-y-3 rounded-xl p-4">
                <div className="flex gap-4 text-[12px]">
                  <label className="flex cursor-pointer items-center gap-1.5">
                    <input
                      type="radio"
                      name="bindChannel"
                      checked={bindChannel === 'email'}
                      onChange={() => setBindChannel('email')}
                      className="accent-[var(--color-brand)]"
                    />
                    <span>绑定邮箱</span>
                  </label>
                  <label className="flex cursor-pointer items-center gap-1.5">
                    <input
                      type="radio"
                      name="bindChannel"
                      checked={bindChannel === 'sms'}
                      onChange={() => setBindChannel('sms')}
                      className="accent-[var(--color-brand)]"
                    />
                    <span>绑定手机号</span>
                  </label>
                </div>

                <div className="space-y-1.5">
                  <input
                    type={bindChannel === 'email' ? 'email' : 'tel'}
                    value={bindTarget}
                    onChange={(e) => setBindTarget(e.target.value)}
                    placeholder={bindChannel === 'email' ? '请输入电子邮箱 (如 user@company.com)' : '请输入11位手机号码'}
                    className="h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    maxLength={6}
                    value={bindCode}
                    onChange={(e) => setBindCode(e.target.value.replace(/\D/g, ''))}
                    placeholder="输入6位验证码"
                    className="h-9 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] tracking-widest text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                  <button
                    onClick={handleSendBindCode}
                    disabled={bindCountdown > 0}
                    className="h-9 cursor-pointer whitespace-nowrap rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[11px] font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-surface)] disabled:opacity-50"
                  >
                    {bindCountdown > 0 ? `${bindCountdown}s 后重试` : '获取验证码'}
                  </button>
                </div>

                <button
                  onClick={handleBindSubmit}
                  disabled={bindLoading || !bindTarget || bindCode.length !== 6}
                  className="flex h-9 w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-[var(--color-brand)] text-[12px] font-bold text-white transition hover:bg-[var(--color-brand-hover)] disabled:opacity-50"
                >
                  <KeyRound className="h-3.5 w-3.5" />
                  {bindLoading ? '正在验证绑定…' : '确认绑定'}
                </button>
              </div>
            </div>
          )}

          {/* TAB 3: 验证码改密 */}
          {activeTab === 'password' && (
            <div className="space-y-4">
              <div className="surface-card space-y-3 rounded-xl p-4">
                <div className="mb-1 text-[12px] text-[var(--color-text-muted)]">
                  修改登录密码需向您绑定的账号发送 6 位安全验证码：
                </div>

                {/* 验证码接收渠道选择 */}
                <div className="flex gap-4 text-[12px]">
                  <label className={`flex items-center gap-1.5 cursor-pointer ${!profile?.email ? 'opacity-40 cursor-not-allowed' : ''}`}>
                    <input
                      type="radio"
                      name="pwdChannel"
                      checked={pwdChannel === 'email'}
                      disabled={!profile?.email}
                      onChange={() => setPwdChannel('email')}
                      className="accent-[var(--color-brand)]"
                    />
                    <span>邮箱验证码 ({profile?.email ? profile.email : '未绑定'})</span>
                  </label>
                  <label className={`flex items-center gap-1.5 cursor-pointer ${!profile?.phone ? 'opacity-40 cursor-not-allowed' : ''}`}>
                    <input
                      type="radio"
                      name="pwdChannel"
                      checked={pwdChannel === 'sms'}
                      disabled={!profile?.phone}
                      onChange={() => setPwdChannel('sms')}
                      className="accent-[var(--color-brand)]"
                    />
                    <span>手机验证码 ({profile?.phone ? profile.phone : '未绑定'})</span>
                  </label>
                </div>

                {/* 验证码输入框与获取按钮 */}
                <div className="flex gap-2">
                  <input
                    type="text"
                    maxLength={6}
                    value={pwdCode}
                    onChange={(e) => setPwdCode(e.target.value.replace(/\D/g, ''))}
                    placeholder="输入6位动态验证码"
                    className="h-9 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] tracking-widest text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                  <button
                    onClick={handleSendPwdCode}
                    disabled={pwdCountdown > 0 || (pwdChannel === 'email' ? !profile?.email : !profile?.phone)}
                    className="h-9 cursor-pointer whitespace-nowrap rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[11px] font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-surface)] disabled:opacity-50"
                  >
                    {pwdCountdown > 0 ? `${pwdCountdown}s 后重发` : '获取改密验证码'}
                  </button>
                </div>

                {/* 新密码与确认新密码 */}
                <div className="space-y-2 pt-1">
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="输入新密码 (至少6位)"
                    className="h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再次确认新密码"
                    className="h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-brand)]"
                  />
                </div>

                <button
                  onClick={handleChangePassword}
                  disabled={pwdLoading || pwdCode.length !== 6 || !newPassword || newPassword !== confirmPassword}
                  className="flex h-9 w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-[var(--color-brand)] text-[12px] font-bold text-white transition hover:bg-[var(--color-brand-hover)] disabled:opacity-50"
                >
                  <Lock className="h-3.5 w-3.5" />
                  {pwdLoading ? '正在修改密码…' : '验证并更改登录密码'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
