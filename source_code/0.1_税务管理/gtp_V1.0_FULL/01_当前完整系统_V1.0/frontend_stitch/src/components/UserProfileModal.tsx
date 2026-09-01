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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-[#0e1628] border border-[#444653]/40 rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden text-[#dae2fd]">
        
        {/* 顶部 Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#444653]/30 bg-[#131b2e]/60">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#8b5cf6]/20 border border-[#a78bfa]/40 flex items-center justify-center text-[#a78bfa]">
              <ShieldCheck className="w-4 h-4" />
            </div>
            <div>
              <h3 className="font-bold text-[15px] text-[#dde1ff]">个人中心与安全设置</h3>
              <p className="text-[11px] text-[#8e909f]">独立用户数据库 · 头像与安全绑定</p>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="w-8 h-8 rounded-lg hover:bg-[#444653]/30 flex items-center justify-center text-[#8e909f] hover:text-[#dae2fd] transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 提示消息 */}
        {msg && (
          <div className={`px-6 py-2.5 text-[12px] flex items-center gap-2 border-b ${
            msg.type === 'success' ? 'bg-[#10b981]/15 text-[#34d399] border-[#10b981]/30' :
            msg.type === 'error' ? 'bg-[#ef4444]/15 text-[#f87171] border-[#ef4444]/30' :
            'bg-[#4cd7f6]/15 text-[#4cd7f6] border-[#4cd7f6]/30'
          }`}>
            {msg.type === 'success' && <CheckCircle className="w-3.5 h-3.5 shrink-0" />}
            {msg.type === 'error' && <AlertCircle className="w-3.5 h-3.5 shrink-0" />}
            {msg.type === 'info' && <RefreshCw className="w-3.5 h-3.5 shrink-0 animate-spin" />}
            <span>{msg.text}</span>
          </div>
        )}

        {/* Tab 导航 */}
        <div className="flex border-b border-[#444653]/30 bg-[#0b1326]/40 text-[13px]">
          <button
            onClick={() => { setActiveTab('profile'); setMsg(null); }}
            className={`flex-1 py-3 font-medium flex items-center justify-center gap-1.5 transition border-b-2 ${
              activeTab === 'profile' 
                ? 'border-[#a78bfa] text-[#dde1ff] bg-[#8b5cf6]/10' 
                : 'border-transparent text-[#8e909f] hover:text-[#dae2fd]'
            }`}
          >
            <User className="w-4 h-4" /> 基本资料与头像
          </button>
          <button
            onClick={() => { setActiveTab('security'); setMsg(null); }}
            className={`flex-1 py-3 font-medium flex items-center justify-center gap-1.5 transition border-b-2 ${
              activeTab === 'security' 
                ? 'border-[#4cd7f6] text-[#dde1ff] bg-[#03b5d3]/10' 
                : 'border-transparent text-[#8e909f] hover:text-[#dae2fd]'
            }`}
          >
            <Mail className="w-4 h-4" /> 邮箱/手机绑定
          </button>
          <button
            onClick={() => { setActiveTab('password'); setMsg(null); }}
            className={`flex-1 py-3 font-medium flex items-center justify-center gap-1.5 transition border-b-2 ${
              activeTab === 'password' 
                ? 'border-[#ef4444] text-[#dde1ff] bg-[#ef4444]/10' 
                : 'border-transparent text-[#8e909f] hover:text-[#dae2fd]'
            }`}
          >
            <Lock className="w-4 h-4" /> 验证码改密
          </button>
        </div>

        {/* Tab 内容区 */}
        <div className="p-6 space-y-5">
          {/* TAB 1: 基本资料与头像 */}
          {activeTab === 'profile' && (
            <div className="space-y-4">
              {/* 头像展示与上传 */}
              <div className="flex items-center gap-4 bg-[#131b2e] p-4 rounded-xl border border-[#444653]/30">
                <div className="relative group">
                  <img
                    src={profile?.avatar_url || '/static/avatars/default.png'}
                    alt="头像"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = 'https://api.dicebear.com/7.x/bottts/svg?seed=' + (profile?.username || 'admin');
                    }}
                    className="w-16 h-16 rounded-full object-cover border-2 border-[#a78bfa]/50 shadow-md bg-[#0b1326]"
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="absolute inset-0 bg-black/60 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition cursor-pointer text-white text-[11px]"
                  >
                    <Camera className="w-5 h-5" />
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    onChange={handleAvatarUpload}
                    className="hidden"
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-[15px] text-[#dde1ff]">{profile?.nickname || profile?.username}</span>
                    <span className="text-[10px] bg-[#8b5cf6]/20 border border-[#a78bfa]/30 text-[#a78bfa] px-1.5 py-0.5 rounded">
                      {profile?.role === 'admin' ? '系统管理员' : '操作员'}
                    </span>
                  </div>
                  <p className="text-[11px] text-[#8e909f] mt-1">账号：{profile?.username}</p>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="mt-2 text-[11px] text-[#4cd7f6] hover:underline flex items-center gap-1 cursor-pointer"
                  >
                    <Camera className="w-3 h-3" /> 更换本地头像图片
                  </button>
                </div>
              </div>

              {/* 昵称修改 */}
              <div className="space-y-1.5">
                <label className="text-[12px] text-[#8e909f]">用户昵称</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="输入新的显示昵称"
                    className="flex-1 h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] focus:border-[#a78bfa] outline-none"
                  />
                  <button
                    onClick={handleSaveNickname}
                    disabled={savingNickname}
                    className="h-9 px-4 rounded-lg bg-[#8b5cf6] hover:bg-[#8b5cf6]/80 text-white font-medium text-[12px] transition cursor-pointer disabled:opacity-50"
                  >
                    {savingNickname ? '保存中…' : '保存昵称'}
                  </button>
                </div>
              </div>

              {/* 快捷安全入口卡片 */}
              <div className="pt-2 border-t border-[#444653]/20 grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => { setActiveTab('security'); setMsg(null); }}
                  className="bg-[#131b2e] hover:bg-[#1a233a] p-3 rounded-xl border border-[#444653]/30 text-left transition cursor-pointer group"
                >
                  <div className="flex items-center gap-1.5 text-[12px] text-[#4cd7f6] font-semibold">
                    <Mail className="w-3.5 h-3.5" /> 邮箱/手机绑定
                  </div>
                  <p className="text-[11px] text-[#8e909f] mt-1 group-hover:text-[#dae2fd]">
                    {profile?.email ? `已绑: ${profile.email}` : '未绑定邮箱'}
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => { setActiveTab('password'); setMsg(null); }}
                  className="bg-[#131b2e] hover:bg-[#1a233a] p-3 rounded-xl border border-[#444653]/30 text-left transition cursor-pointer group"
                >
                  <div className="flex items-center gap-1.5 text-[12px] text-[#f87171] font-semibold">
                    <Lock className="w-3.5 h-3.5" /> 验证码改密
                  </div>
                  <p className="text-[11px] text-[#8e909f] mt-1 group-hover:text-[#dae2fd]">
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
                <div className="bg-[#131b2e] p-3 rounded-xl border border-[#444653]/30">
                  <div className="flex items-center gap-1.5 text-[12px] text-[#8e909f]">
                    <Mail className="w-3.5 h-3.5 text-[#4cd7f6]" /> 电子邮箱
                  </div>
                  <p className="text-[13px] font-semibold text-[#dde1ff] mt-1 truncate">
                    {profile?.email || '未绑定'}
                  </p>
                  <span className={`text-[10px] inline-block mt-1 px-1.5 py-0.5 rounded ${
                    profile?.email_verified ? 'bg-[#10b981]/20 text-[#34d399]' : 'bg-[#444653]/30 text-[#8e909f]'
                  }`}>
                    {profile?.email_verified ? '✓ 已验证' : '未验证'}
                  </span>
                </div>
                <div className="bg-[#131b2e] p-3 rounded-xl border border-[#444653]/30">
                  <div className="flex items-center gap-1.5 text-[12px] text-[#8e909f]">
                    <Smartphone className="w-3.5 h-3.5 text-[#38bdf8]" /> 手机号码
                  </div>
                  <p className="text-[13px] font-semibold text-[#dde1ff] mt-1 truncate">
                    {profile?.phone || '未绑定'}
                  </p>
                  <span className={`text-[10px] inline-block mt-1 px-1.5 py-0.5 rounded ${
                    profile?.phone_verified ? 'bg-[#10b981]/20 text-[#34d399]' : 'bg-[#444653]/30 text-[#8e909f]'
                  }`}>
                    {profile?.phone_verified ? '✓ 已验证' : '未验证'}
                  </span>
                </div>
              </div>

              {/* 绑定/换绑表单 */}
              <div className="bg-[#131b2e] p-4 rounded-xl border border-[#444653]/30 space-y-3">
                <div className="flex gap-4 text-[12px]">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="bindChannel"
                      checked={bindChannel === 'email'}
                      onChange={() => setBindChannel('email')}
                      className="accent-[#4cd7f6]"
                    />
                    <span>绑定邮箱</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="bindChannel"
                      checked={bindChannel === 'sms'}
                      onChange={() => setBindChannel('sms')}
                      className="accent-[#4cd7f6]"
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
                    className="w-full h-9 bg-[#0b1326] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] outline-none"
                  />
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    maxLength={6}
                    value={bindCode}
                    onChange={(e) => setBindCode(e.target.value.replace(/\D/g, ''))}
                    placeholder="输入6位验证码"
                    className="flex-1 h-9 bg-[#0b1326] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] tracking-widest focus:border-[#4cd7f6] outline-none"
                  />
                  <button
                    onClick={handleSendBindCode}
                    disabled={bindCountdown > 0}
                    className="h-9 px-3 rounded-lg bg-[#03b5d3]/20 border border-[#4cd7f6]/40 hover:bg-[#03b5d3]/30 text-[#4cd7f6] font-medium text-[11px] transition cursor-pointer disabled:opacity-50 whitespace-nowrap"
                  >
                    {bindCountdown > 0 ? `${bindCountdown}s 后重试` : '获取验证码'}
                  </button>
                </div>

                <button
                  onClick={handleBindSubmit}
                  disabled={bindLoading || !bindTarget || bindCode.length !== 6}
                  className="w-full h-9 rounded-lg bg-[#0ea5e9] hover:bg-[#0ea5e9]/80 disabled:opacity-50 text-white font-bold text-[12px] transition cursor-pointer shadow-md flex items-center justify-center gap-1.5"
                >
                  <KeyRound className="w-3.5 h-3.5" />
                  {bindLoading ? '正在验证绑定…' : '确认绑定'}
                </button>
              </div>
            </div>
          )}

          {/* TAB 3: 验证码改密 */}
          {activeTab === 'password' && (
            <div className="space-y-4">
              <div className="bg-[#131b2e] p-4 rounded-xl border border-[#444653]/30 space-y-3">
                <div className="text-[12px] text-[#8e909f] mb-1">
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
                      className="accent-[#ef4444]"
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
                      className="accent-[#ef4444]"
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
                    className="flex-1 h-9 bg-[#0b1326] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] tracking-widest focus:border-[#ef4444] outline-none"
                  />
                  <button
                    onClick={handleSendPwdCode}
                    disabled={pwdCountdown > 0 || (pwdChannel === 'email' ? !profile?.email : !profile?.phone)}
                    className="h-9 px-3 rounded-lg bg-[#ef4444]/20 border border-[#f87171]/40 hover:bg-[#ef4444]/30 text-[#f87171] font-medium text-[11px] transition cursor-pointer disabled:opacity-50 whitespace-nowrap"
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
                    className="w-full h-9 bg-[#0b1326] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] focus:border-[#ef4444] outline-none"
                  />
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再次确认新密码"
                    className="w-full h-9 bg-[#0b1326] border border-[#444653]/50 rounded-lg px-3 text-[13px] text-[#dae2fd] focus:border-[#ef4444] outline-none"
                  />
                </div>

                <button
                  onClick={handleChangePassword}
                  disabled={pwdLoading || pwdCode.length !== 6 || !newPassword || newPassword !== confirmPassword}
                  className="w-full h-9 rounded-lg bg-[#ef4444] hover:bg-[#ef4444]/80 disabled:opacity-50 text-white font-bold text-[12px] transition cursor-pointer shadow-md flex items-center justify-center gap-1.5"
                >
                  <Lock className="w-3.5 h-3.5" />
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

/* ===========================================================
   保持 Windows ClearType 亚像素渲染（系统字体方案下无需 swap）
   -webkit-font-smoothing: auto  -> Windows 使用 ClearType
                                -> macOS  使用视网膜灰度平滑
   =========================================================== */
*, *::before, *::after, html, body, .antialiased {
  -webkit-font-smoothing: auto !important;
  -moz-osx-font-smoothing: auto !important;
  text-rendering: auto !important;
}
