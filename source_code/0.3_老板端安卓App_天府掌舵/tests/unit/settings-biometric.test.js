import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsView from '../../src/modules/settings/SettingsView.vue'
import { useAuthStore } from '../../src/stores/auth.store'

const mocks = vi.hoisted(() => ({
  isBiometricAvailable: vi.fn(),
  enableBiometric: vi.fn(),
  preferences: {
    get: vi.fn(),
    set: vi.fn(),
    remove: vi.fn()
  }
}))

vi.mock('../../src/api/biometric', () => ({
  isBiometricAvailable: mocks.isBiometricAvailable,
  enableBiometric: mocks.enableBiometric
}))

vi.mock('@capacitor/preferences', () => ({
  Preferences: mocks.preferences
}))

async function mountSettings(session) {
  const pinia = createPinia()
  setActivePinia(pinia)
  if (session) useAuthStore().startSession(session)

  const wrapper = mount(SettingsView, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

describe('<SettingsView /> biometric binding', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.isBiometricAvailable.mockResolvedValue(true)
    mocks.enableBiometric.mockResolvedValue(true)
    mocks.preferences.get.mockResolvedValue({ value: null })
    mocks.preferences.set.mockResolvedValue(undefined)
    mocks.preferences.remove.mockResolvedValue(undefined)
    vi.clearAllMocks()
  })

  it('rejects biometric binding without an authenticated session', async () => {
    const wrapper = await mountSettings()

    await wrapper.find('#old-password').setValue('current-password')
    await wrapper.find('#bio-cache').setValue(true)
    await flushPromises()
    await nextTick()

    expect(mocks.enableBiometric).not.toHaveBeenCalled()
    expect(wrapper.find('#bio-cache').element.checked).toBe(false)
    expect(wrapper.text()).toContain('请先正常登录后再启用生物识别。')
  })

  it('rejects biometric binding when the current password is missing', async () => {
    const wrapper = await mountSettings({
      user: { id: 'user-1', role: 'executive' },
      accessToken: 'access-token',
      mode: 'production'
    })

    await wrapper.find('#bio-cache').setValue(true)
    await flushPromises()
    await nextTick()

    expect(mocks.enableBiometric).not.toHaveBeenCalled()
    expect(wrapper.find('#bio-cache').element.checked).toBe(false)
    expect(wrapper.text()).toContain('请输入当前密码后再启用生物识别。')
  })

  it('binds biometric credentials with the authenticated user id and entered password', async () => {
    const wrapper = await mountSettings({
      user: { id: 42, role: 'executive' },
      accessToken: 'access-token',
      mode: 'production'
    })

    await wrapper.find('#old-password').setValue('current-password')
    await wrapper.find('#bio-cache').setValue(true)
    await flushPromises()

    expect(mocks.enableBiometric).toHaveBeenCalledOnce()
    expect(mocks.enableBiometric).toHaveBeenCalledWith('42', 'current-password')
    expect(mocks.preferences.set).toHaveBeenCalledWith({
      key: 'cdjg_biometric_enabled',
      value: 'true'
    })
    expect(wrapper.text()).toContain('生物识别已启用。')
  })
})
