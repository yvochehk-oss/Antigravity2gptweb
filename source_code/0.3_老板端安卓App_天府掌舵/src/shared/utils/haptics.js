/**
 * Haptic feedback utility backed by @capacitor/haptics.
 * Gracefully no-ops in browser / desktop environments.
 */

let haptics = null

async function loadHaptics() {
  if (haptics !== null) return haptics
  try {
    const { Haptics, ImpactStyle } = await import('@capacitor/haptics')
    haptics = { Haptics, ImpactStyle }
    return haptics
  } catch {
    haptics = false
    return false
  }
}

/**
 * Fire a light impact (suitable for toggles, button presses).
 * @returns {Promise<void>}
 */
export async function hapticsLight() {
  const lib = await loadHaptics()
  if (!lib) return
  try {
    await lib.Haptics.impact({ style: lib.ImpactStyle.Light })
  } catch {
    // non-fatal
  }
}

/**
 * Fire a medium impact (suitable for confirmations, pull-to-refresh success).
 * @returns {Promise<void>}
 */
export async function hapticsMedium() {
  const lib = await loadHaptics()
  if (!lib) return
  try {
    await lib.Haptics.impact({ style: lib.ImpactStyle.Medium })
  } catch {
    // non-fatal
  }
}

/**
 * Fire a heavy impact (suitable for errors or destructive actions).
 * @returns {Promise<void>}
 */
export async function hapticsHeavy() {
  const lib = await loadHaptics()
  if (!lib) return
  try {
    await lib.Haptics.impact({ style: lib.ImpactStyle.Heavy })
  } catch {
    // non-fatal
  }
}

/**
 * Trigger a success notification haptic (two light pulses on iOS/mac).
 * @returns {Promise<void>}
 */
export async function hapticsSuccess() {
  const lib = await loadHaptics()
  if (!lib) return
  try {
    const { HapticsNotificationType } = await import('@capacitor/haptics')
    await lib.Haptics.notification({ type: HapticsNotificationType.Success })
  } catch {
    // non-fatal
  }
}

/**
 * Trigger an error notification haptic.
 * @returns {Promise<void>}
 */
export async function hapticsError() {
  const lib = await loadHaptics()
  if (!lib) return
  try {
    const { HapticsNotificationType } = await import('@capacitor/haptics')
    await lib.Haptics.notification({ type: HapticsNotificationType.Error })
  } catch {
    // non-fatal
  }
}
