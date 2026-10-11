import { h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'

// 用 import.meta.glob 懒加载视图：Vite 只会为已存在的 .vue 文件生成 chunk，
// Task 8/9/10 的视图文件落地后会被自动纳入，本文件无需再改。
const views = import.meta.glob('../views/*.vue')

const view = (name) => {
  const loader = views[`../views/${name}.vue`]
  if (loader) return loader
  // 视图尚未实现（PatientDetail/EntryForm/Dashboard 属于后续任务），渲染占位提示
  return {
    name: `${name}Pending`,
    render: () => h('div', { style: 'padding: 24px; color: #909399' }, `${name} 页面待实现`)
  }
}

const routes = [
  { path: '/', name: 'patient-list', component: view('PatientList') },
  { path: '/patient/:id', name: 'patient-detail', component: view('PatientDetail') },
  { path: '/entry', name: 'entry', component: view('EntryForm') },
  { path: '/dashboard', name: 'dashboard', component: view('Dashboard') },
  { path: '/admission/:id', name: 'admission-detail', component: view('AdmissionDetail') },
  { path: '/validation', name: 'validation', component: view('ValidationReport') }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

export default router
