<template>
  <el-container class="layout-container">
    <el-aside :width="appStore.sidebarCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo-area">
        <div v-if="!appStore.sidebarCollapsed" class="logo-title">
          <el-icon class="logo-icon"><Coin /></el-icon>
          <h2>Oracle本体构建平台</h2>
        </div>
        <div v-else class="logo-title logo-title-collapsed">
          <el-icon class="logo-icon"><Coin /></el-icon>
          <h2>OB</h2>
        </div>
      </div>
      <el-menu
        :default-active="currentMenu"
        :default-openeds="openedMenus"
        :collapse="appStore.sidebarCollapsed"
        router
        background-color="#1a3a5c"
        text-color="#ccc"
        active-text-color="#fff"
      >
        <el-sub-menu index="/source">
          <template #title>
            <el-icon><Database /></el-icon>
            <span>源数据管理</span>
          </template>
          <el-menu-item index="/source/browse">
            <el-icon><DataBoard /></el-icon>
            <span>数据浏览管理</span>
          </el-menu-item>
          <el-menu-item index="/source/annotation">
            <el-icon><EditPen /></el-icon>
            <span>数据对象标注</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="/business">
          <template #title>
            <el-icon><Share /></el-icon>
            <span>业务对象构建</span>
          </template>
          <el-menu-item index="/business/domains">
            <el-icon><CollectionTag /></el-icon>
            <span>业务分析域管理</span>
          </el-menu-item>
          <el-menu-item index="/business/ontology">
            <el-icon><Share /></el-icon>
            <span>本体关系构建</span>
          </el-menu-item>
          <el-menu-item index="/business/process">
            <el-icon><Operation /></el-icon>
            <span>业务流程构建</span>
          </el-menu-item>
          <el-menu-item index="/business/rules-activities">
            <el-icon><SetUp /></el-icon>
            <span>业务规则活动</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="/mapping">
          <template #title>
            <el-icon><Connection /></el-icon>
            <span>数据映射</span>
          </template>
          <el-menu-item index="/mapping/operation">
            <el-icon><MagicStick /></el-icon>
            <span>数据映射操作</span>
          </el-menu-item>
          <el-menu-item index="/mapping/manage">
            <el-icon><Connection /></el-icon>
            <span>数据映射管理</span>
          </el-menu-item>
        </el-sub-menu>
        <el-menu-item index="/ddl">
          <el-icon><DocumentCopy /></el-icon>
          <span>DDL生成与应用</span>
        </el-menu-item>
        <el-sub-menu index="/browse">
          <template #title>
            <el-icon><View /></el-icon>
            <span>本体浏览管理</span>
          </template>
          <el-menu-item index="/browse/ontology">
            <el-icon><View /></el-icon>
            <span>本体图谱浏览</span>
          </el-menu-item>
          <el-menu-item index="/browse/graph-query">
            <el-icon><Connection /></el-icon>
            <span>图数据查询</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="/agent">
          <template #title>
            <el-icon><Cpu /></el-icon>
            <span>智能体构建</span>
          </template>
          <el-menu-item index="/agent/skills">
            <el-icon><Tools /></el-icon>
            <span>技能构建</span>
          </el-menu-item>
          <el-menu-item index="/agent/test">
            <el-icon><Promotion /></el-icon>
            <span>智能体测试</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="/system">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统管理</span>
          </template>
          <el-menu-item index="/system/datasource">
            <el-icon><Coin /></el-icon>
            <span>数据源管理</span>
          </el-menu-item>
          <el-menu-item index="/system/llm">
            <el-icon><Cpu /></el-icon>
            <span>大模型管理</span>
          </el-menu-item>
          <el-menu-item index="/system/users">
            <el-icon><UserFilled /></el-icon>
            <span>用户管理</span>
          </el-menu-item>
          <el-menu-item index="/system/logs">
            <el-icon><Tickets /></el-icon>
            <span>操作日志</span>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="top-header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="appStore.toggleSidebar">
            <Fold v-if="!appStore.sidebarCollapsed" />
            <Expand v-else />
          </el-icon>
          <span class="page-title">{{ currentTitle }}</span>
        </div>
        <div class="header-right">
          <span class="domain-selector">
            当前业务分析域:
            <el-select v-model="appStore.currentDomainId" placeholder="选择业务分析域" size="small" @change="handleDomainChange" style="width: 220px">
              <el-option v-for="d in domains" :key="d.domain_id" :label="d.domain_name" :value="d.domain_id" />
            </el-select>
          </span>
          <el-dropdown @command="handleUserCommand">
            <span class="user-info">
              <el-icon><User /></el-icon>
              {{ appStore.user?.display_name || appStore.user?.username }}
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="change-password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
    <el-dialog v-model="passwordDialogVisible" title="修改密码" width="420px" destroy-on-close>
      <el-form ref="passwordFormRef" :model="passwordForm" :rules="passwordRules" label-width="90px" @submit.prevent>
        <el-form-item label="当前密码" prop="current_password">
          <el-input v-model="passwordForm.current_password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="passwordForm.new_password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input v-model="passwordForm.confirm_password" type="password" show-password autocomplete="new-password" @keyup.enter="savePassword" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="passwordSaving" @click="savePassword">确认修改</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi, domainApi } from '../api'
import { useAppStore } from '../stores/app'

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const domains = ref<any[]>([])
const passwordDialogVisible = ref(false)
const passwordSaving = ref(false)
const passwordFormRef = ref()
const passwordForm = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})
const passwordRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [{ required: true, message: '请输入新密码', trigger: 'blur' }],
  confirm_password: [{ required: true, message: '请确认新密码', trigger: 'blur' }]
}

const currentMenu = computed(() => route.path)
const sourceMenuOpened = computed(() => route.path.startsWith('/source'))
const businessMenuOpened = computed(() => route.path.startsWith('/business'))
const mappingMenuOpened = computed(() => route.path.startsWith('/mapping'))
const browseMenuOpened = computed(() => route.path.startsWith('/browse'))
const agentMenuOpened = computed(() => route.path.startsWith('/agent'))
const systemMenuOpened = computed(() => route.path.startsWith('/system'))
const openedMenus = computed(() => {
  const items: string[] = []
  if (sourceMenuOpened.value) items.push('/source')
  if (mappingMenuOpened.value) items.push('/mapping')
  if (businessMenuOpened.value) items.push('/business')
  if (browseMenuOpened.value) items.push('/browse')
  if (agentMenuOpened.value) items.push('/agent')
  if (systemMenuOpened.value) items.push('/system')
  return items
})
const currentTitle = computed(() => {
  const parts = [
    route.meta?.groupMenu as string,
    route.meta?.parentMenu as string,
    route.meta?.title as string
  ].filter(Boolean)
  return [...new Set(parts)].join(' > ')
})

const loadDomains = async () => {
  try {
    const res = await domainApi.list('ACTIVE')
    domains.value = res.data || []
    if (!domains.value.length) {
      appStore.setCurrentDomain('', '')
      return
    }

    const current = domains.value.find(d => d.domain_id === appStore.currentDomainId)
    if (!current) {
      const first = domains.value[0]
      appStore.setCurrentDomain(first.domain_id, first.domain_name)
    }
  } catch (e) {}
}

const handleDomainChange = (val: string) => {
  const domain = domains.value.find(d => d.domain_id === val)
  if (domain) {
    appStore.setCurrentDomain(val, domain.domain_name)
  }
}

const handleUserCommand = (cmd: string) => {
  if (cmd === 'change-password') {
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    passwordDialogVisible.value = true
  } else if (cmd === 'logout') {
    appStore.logout()
    router.push('/login')
  }
}

const savePassword = async () => {
  const valid = await passwordFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    ElMessage.error('新密码与确认密码不一致')
    return
  }

  passwordSaving.value = true
  try {
    await authApi.changePassword(passwordForm)
    ElMessage.success('密码已修改，请使用新密码重新登录')
    passwordDialogVisible.value = false
    appStore.logout()
    router.push('/login')
  } catch (e) {
    // 请求错误由 API 拦截器提示。
  } finally {
    passwordSaving.value = false
  }
}

onMounted(() => {
  loadDomains()
})
</script>

<style scoped>
.layout-container {
  height: 100vh;
}
.sidebar {
  background-color: #1a3a5c;
  transition: width 0.3s;
  overflow: hidden;
}
.logo-area {
  padding: 16px;
  color: #fff;
  border-bottom: 1px solid rgba(255,255,255,0.1);
}
.logo-title {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.logo-title-collapsed {
  gap: 0;
  flex-direction: column;
}
.logo-icon {
  font-size: 18px;
  color: #f7c66a;
  flex-shrink: 0;
}
.logo-area h2 {
  font-size: 16px;
  white-space: nowrap;
  margin: 0;
}
.top-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  padding: 0 20px;
  height: 50px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: #666;
}
.page-title {
  font-size: 16px;
  color: #333;
  font-weight: 500;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}
.domain-selector {
  font-size: 13px;
  color: #666;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #666;
  cursor: pointer;
  font-size: 14px;
}
.main-content {
  background: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
}
</style>
