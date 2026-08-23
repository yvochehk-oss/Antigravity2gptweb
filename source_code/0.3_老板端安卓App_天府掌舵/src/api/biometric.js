import { Capacitor } from '@capacitor/core'
import { NativeBiometric } from 'capacitor-native-biometric'
import { Preferences } from '@capacitor/preferences'

const BIOMETRIC_USERNAME_KEY = 'cdjg_biometric_username'
const BIOMETRIC_PASSWORD_KEY = 'cdjg_biometric_password'

/**
 * Checks if biometric is available (either via Native API or WebAuthn)
 */
export async function isBiometricAvailable() {
  if (Capacitor.isNativePlatform()) {
    try {
      const result = await NativeBiometric.isAvailable()
      return result.isAvailable
    } catch {
      return false
    }
  } else {
    // For WebAuthn, check if the browser supports PublicKeyCredential
    return window.PublicKeyCredential !== undefined &&
           typeof window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function'
  }
}

/**
 * Enable biometric login (Route 2 for Native, Route 1 for PWA)
 */
export async function enableBiometric(username, password) {
  if (Capacitor.isNativePlatform()) {
    // Route 2: Native App (Save credentials securely using Preferences/Keychain)
    await Preferences.set({ key: BIOMETRIC_USERNAME_KEY, value: username })
    
    // In a real app, this should be stored using `@capacitor-community/secure-storage` or similar
    // We use NativeBiometric's setCredentials if supported, or Preferences as a fallback demonstration
    try {
      await NativeBiometric.setCredentials({
        username: username,
        password: password,
        server: 'cdjg.executive.app'
      })
    } catch {
      // Fallback to basic preferences if native setCredentials fails
      await Preferences.set({ key: BIOMETRIC_PASSWORD_KEY, value: password })
    }
    return true
  } else {
    // Route 1: PWA/Web (WebAuthn)
    // NOTE: Requires backend implementation of challenge & signing.
    throw new Error('WebAuthn (Passkeys) 需要后端接口配合，已列入研发计划。')
  }
}

/**
 * Perform biometric login
 */
export async function performBiometricLogin() {
  if (Capacitor.isNativePlatform()) {
    try {
      await await NativeBiometric.verifyIdentity({
        reason: '请验证您的指纹/面容以登录锐宝管理系统',
        title: '生物识别登录'
      })
      
      let credentials
      try {
        credentials = await NativeBiometric.getCredentials({ server: 'cdjg.executive.app' })
      } catch {
        // Fallback reading
        const { value: u } = await Preferences.get({ key: BIOMETRIC_USERNAME_KEY })
        const { value: p } = await Preferences.get({ key: BIOMETRIC_PASSWORD_KEY })
        if (u && p) credentials = { username: u, password: p }
      }

      if (!credentials) throw new Error('未找到绑定的账号信息，请先使用密码登录并绑定。')
      
      return credentials
    } catch {
      throw new Error('生物识别验证失败')
    }
  } else {
    // Route 1: PWA WebAuthn Login
    throw new Error('Web 环境的通行密钥登录尚未激活。')
  }
}
