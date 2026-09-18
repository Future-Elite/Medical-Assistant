<template>
  <el-container class="app">
    <el-header class="app-header">
      <div class="app-logo">HIS 模拟系统</div>
      <el-menu class="app-menu" mode="horizontal" :default-active="activeMenu" router>
        <el-menu-item index="/">患者列表</el-menu-item>
        <el-menu-item index="/entry">录入</el-menu-item>
        <el-menu-item index="/dashboard">看板</el-menu-item>
      </el-menu>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
// 患者详情页（/patient/:id）仍归属「患者列表」菜单
const activeMenu = computed(() => (route.path.startsWith('/patient/') ? '/' : route.path))
</script>

<style scoped>
.app {
  min-height: 100vh;
}

.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.app-logo {
  font-size: 18px;
  font-weight: 600;
  white-space: nowrap;
}

.app-menu {
  flex: 1;
  border-bottom: none;
}
</style>
