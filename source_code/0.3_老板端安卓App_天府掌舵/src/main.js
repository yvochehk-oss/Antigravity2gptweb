import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'

createApp(App)
  .use(createPinia())
  .use(router)
  .mount('#app')

// Register service worker for offline-first PWA launch.
// The try/catch suppresses errors in non-browser environments (e.g. SSR,
// test runners, Electron main process) so registration never crashes the app.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then(reg => {
        console.debug('[SW] registered, scope:', reg.scope)
      })
      .catch(err => {
        console.warn('[SW] registration failed (non-fatal):', err.message)
      })
  })
}
