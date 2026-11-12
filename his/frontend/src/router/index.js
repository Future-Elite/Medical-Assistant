import { createRouter, createWebHistory } from 'vue-router'
export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: () => import('../views/NeonatalRecords.vue') },
    { path: '/neonatal/:id', component: () => import('../views/NeonatalDetail.vue') },
    { path: '/dashboard', component: () => import('../views/NeonatalDashboard.vue') },
    { path: '/validation', component: () => import('../views/NeonatalQuality.vue') },
    { path: '/import', component: () => import('../views/NeonatalImport.vue') },
    { path: '/entry', redirect: '/import' },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})
