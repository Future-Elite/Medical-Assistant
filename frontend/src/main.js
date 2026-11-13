import { createApp, defineAsyncComponent } from 'vue'
import DreamerApp from './DualApp.vue'
import './style.css'
import './neonatal.css'

const App = new URLSearchParams(window.location.search).get('legacy') === '1'
  ? defineAsyncComponent(() => import('./App.vue')) : DreamerApp
createApp(App).mount('#app')
