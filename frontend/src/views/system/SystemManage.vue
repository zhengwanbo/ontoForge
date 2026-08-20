<template>
  <div class="system-page">
    <!-- LLM Config -->
    <div v-if="activeTab === 'llm'" class="section">
      <div class="section-header">
        <h4>大模型配置</h4>
        <el-button type="primary" @click="showCreateLLMConfig" size="small">新建配置</el-button>
      </div>
      <el-table :data="llmConfigs" border stripe>
        <el-table-column prop="config_name" label="配置名称" width="180" />
        <el-table-column prop="api_base_url" label="API地址" width="250" />
        <el-table-column prop="model_name" label="模型" width="120" />
        <el-table-column prop="temperature" label="温度" width="80" />
        <el-table-column prop="context_window_tokens" label="最大Token" width="120" />
        <el-table-column prop="is_default" label="默认" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_default === 'Y' ? 'success' : 'info'" size="small">{{ row.is_default === 'Y' ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="启用" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active === 'Y' ? 'success' : 'danger'" size="small">{{ row.is_active === 'Y' ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="showEditLLMConfig(row)">编辑</el-button>
            <el-button size="small" type="primary" link @click="testLLM(row.config_id)">测试</el-button>
            <el-button size="small" type="warning" link @click="setDefault(row.config_id)">设为默认</el-button>
            <el-button size="small" type="danger" link @click="deleteLLM(row.config_id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- User Management -->
    <div v-if="activeTab === 'users'" class="section">
      <div class="section-header">
        <h4>用户管理</h4>
        <el-button type="primary" @click="showCreateUser" size="small">新建用户</el-button>
      </div>
      <el-table :data="users" border stripe>
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column prop="display_name" label="显示名称" width="120" />
        <el-table-column prop="email" label="邮箱" width="200" />
        <el-table-column prop="role" label="角色" width="100">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : row.role === 'analyst' ? '' : 'info'" size="small">{{ row.role }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="可用业务分析域" min-width="220">
          <template #default="{ row }">
            <el-tag v-if="row.role === 'admin'" type="danger" size="small">全部分析域</el-tag>
            <template v-else-if="row.domain_ids?.length">
              <el-tag v-for="domainId in row.domain_ids" :key="domainId" size="small" class="domain-tag">
                {{ domainNameById(domainId) }}
              </el-tag>
            </template>
            <el-tag v-else type="warning" size="small">未授权</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="editUser(row)">编辑</el-button>
            <el-button size="small" type="danger" link @click="disableUser(row.user_id)">禁用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Operation Logs -->
    <div v-if="activeTab === 'logs'" class="section">
      <div class="section-header"><h4>操作日志</h4></div>
      <el-table :data="operationLogs" border stripe>
        <el-table-column prop="operator" label="操作人" width="100" />
        <el-table-column prop="operation_type" label="操作类型" width="150" />
        <el-table-column prop="operation_target" label="操作对象" width="200" />
        <el-table-column prop="operation_detail" label="操作详情" min-width="300" />
        <el-table-column prop="created_at" label="时间" width="180" />
      </el-table>
    </div>

    <!-- Create LLM Config Dialog -->
    <el-dialog v-model="llmDialogVisible" :title="llmDialogTitle" width="500px">
      <el-form :model="llmForm" label-width="100px">
        <el-form-item label="配置名称"><el-input v-model="llmForm.config_name" /></el-form-item>
        <el-form-item label="API地址"><el-input v-model="llmForm.api_base_url" placeholder="https://api.openai.com/v1" /></el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="llmForm.api_key"
            :placeholder="isEditLLM ? '留空则保持当前 Key 不变' : 'sk-xxxx'"
            show-password
          />
        </el-form-item>
        <el-form-item label="模型名称"><el-input v-model="llmForm.model_name" placeholder="gpt-4o / deepseek-v4-flash" /></el-form-item>
        <el-form-item label="温度参数"><el-slider v-model="llmForm.temperature" :min="0" :max="2" :step="0.1" show-input /></el-form-item>
        <el-form-item label="最大Token">
          <el-input-number v-model="llmForm.context_window_tokens" :min="1000" :max="2000000" />
          <div class="form-tip">表示输入内容允许使用的最大 token 值。输出上限由系统自动控制。</div>
        </el-form-item>
        <el-form-item label="超时(秒)"><el-input-number v-model="llmForm.timeout" :min="10" :max="300" /></el-form-item>
        <el-form-item v-if="isEditLLM" label="启用状态"><el-switch v-model="llmForm.is_active" /></el-form-item>
        <el-form-item label="设为默认"><el-switch v-model="llmForm.is_default" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="llmDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveLLMConfig" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- Create/Edit User Dialog -->
    <el-dialog v-model="userDialogVisible" :title="userDialogTitle" width="500px">
      <el-form :model="userForm" label-width="100px">
        <el-form-item label="用户名"><el-input v-model="userForm.username" :disabled="isEditUser" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="userForm.display_name" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="userForm.email" /></el-form-item>
        <el-form-item label="密码" v-if="!isEditUser"><el-input v-model="userForm.password" type="password" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role">
            <el-option label="管理员 (admin)" value="admin" />
            <el-option label="分析师 (analyst)" value="analyst" />
            <el-option label="只读用户 (viewer)" value="viewer" />
          </el-select>
        </el-form-item>
        <el-form-item label="可用分析域">
          <el-checkbox-group v-model="userForm.domain_ids" :disabled="userForm.role === 'admin'">
            <el-checkbox v-for="domain in domains" :key="domain.domain_id" :label="domain.domain_id">
              {{ domain.domain_name }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="form-tip" v-if="userForm.role === 'admin'">管理员默认可查看和管理全部业务分析域，无需单独授权。</div>
          <div class="form-tip" v-else>请至少选择一个业务分析域；用户登录后只能访问这些分析域下的本体、映射、DDL 等内容。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveUser" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { domainApi, systemApi } from '../../api'

const route = useRoute()

const tabRouteMap: Record<string, string> = { '/system/llm': 'llm', '/system/users': 'users', '/system/logs': 'logs' }
const activeTab = ref(tabRouteMap[route.path] || 'llm')

// 路由变化 → 切换显示区
watch(() => route.path, (path) => {
  const tab = tabRouteMap[path]
  if (tab) {
    activeTab.value = tab
    loadDataForTab(tab)
  }
})

const loadDataForTab = (tab: string) => {
  if (tab === 'llm') loadLLMConfigs()
  else if (tab === 'users') loadUsers()
  else if (tab === 'logs') loadOperationLogs()
}
const saving = ref(false)

// LLM Config
const llmConfigs = ref<any[]>([])
const llmDialogVisible = ref(false)
const llmDialogTitle = ref('新建大模型配置')
const isEditLLM = ref(false)
const editLLMId = ref('')
const createEmptyLLMForm = () => ({
  config_name: '',
  api_base_url: '',
  api_key: '',
  model_name: 'gpt-4o',
  temperature: 0.7,
  context_window_tokens: 32000,
  timeout: 60,
  is_active: true,
  is_default: false
})
const llmForm = ref(createEmptyLLMForm())

// User
const users = ref<any[]>([])
const userDialogVisible = ref(false)
const userDialogTitle = ref('新建用户')
const userForm = ref({ username: '', display_name: '', email: '', password: '', role: 'analyst', domain_ids: [] as string[] })
const isEditUser = ref(false)
const editUserId = ref('')

// Logs
const operationLogs = ref<any[]>([])

const loadLLMConfigs = async () => {
  try { const res = await systemApi.getLLMConfigs(); llmConfigs.value = res.data || [] } catch (e) {}
}
const loadUsers = async () => {
  try { const res = await systemApi.getUsers(); users.value = res.data || [] } catch (e) {}
}
const domains = ref<any[]>([])
const loadDomains = async () => {
  try { const res = await domainApi.list(); domains.value = res.data || [] } catch (e) {}
}
const domainNameById = (domainId: string) => domains.value.find(item => item.domain_id === domainId)?.domain_name || domainId
const loadOperationLogs = async () => {
  try { const res = await systemApi.getOperationLogs(); operationLogs.value = res.data || [] } catch (e) {}
}

const showCreateLLMConfig = () => {
  llmDialogTitle.value = '新建大模型配置'
  isEditLLM.value = false
  editLLMId.value = ''
  llmForm.value = createEmptyLLMForm()
  llmDialogVisible.value = true
}

const showEditLLMConfig = (row: any) => {
  llmDialogTitle.value = '编辑大模型配置'
  isEditLLM.value = true
  editLLMId.value = row.config_id
  llmForm.value = {
    config_name: row.config_name || '',
    api_base_url: row.api_base_url || '',
    api_key: '',
    model_name: row.model_name || 'gpt-4o',
    temperature: Number(row.temperature ?? 0.7),
    context_window_tokens: Number(row.context_window_tokens ?? 32000),
    timeout: Number(row.timeout ?? 60),
    is_active: row.is_active === 'Y',
    is_default: row.is_default === 'Y'
  }
  llmDialogVisible.value = true
}

const saveLLMConfig = async () => {
  saving.value = true
  try {
    const payload: any = { ...llmForm.value }
    delete payload.max_tokens
    if (isEditLLM.value && !payload.api_key) {
      delete payload.api_key
    }

    if (isEditLLM.value) {
      await systemApi.updateLLMConfig(editLLMId.value, payload)
      ElMessage.success('配置已更新')
    } else {
      await systemApi.createLLMConfig(payload)
      ElMessage.success('配置已创建')
    }
    llmDialogVisible.value = false
    await loadLLMConfigs()
  } catch (e) {} finally { saving.value = false }
}

const testLLM = async (configId: string) => {
  try {
    ElMessage.info('正在测试连接...')
    const res = await systemApi.testLLMConfig(configId)
    if (res.data?.success) {
      const contextText = res.data?.detected_context_window_tokens || res.data?.context_window_tokens
        ? `, 上下文窗口: ${res.data?.detected_context_window_tokens || res.data?.context_window_tokens}`
        : ''
      ElMessage.success(`连接成功! 耗时${res.data.duration}秒, 模型: ${res.data.model}${contextText}`)
    } else {
      ElMessage.error(`连接失败: ${res.data?.error}`)
    }
  } catch (e) {}
}

const setDefault = async (configId: string) => {
  try {
    await systemApi.updateLLMConfig(configId, { is_default: true })
    ElMessage.success('已设为默认配置')
    await loadLLMConfigs()
  } catch (e) {}
}

const deleteLLM = async (configId: string) => {
  await ElMessageBox.confirm('确定删除此配置?', '确认')
  try {
    await systemApi.deleteLLMConfig(configId)
    ElMessage.success('配置已删除')
    await loadLLMConfigs()
  } catch (e) {}
}

const showCreateUser = () => {
  userDialogTitle.value = '新建用户'
  userForm.value = { username: '', display_name: '', email: '', password: '', role: 'analyst', domain_ids: [] }
  isEditUser.value = false
  userDialogVisible.value = true
}

const editUser = (row: any) => {
  userDialogTitle.value = '编辑用户'
  userForm.value = {
    username: row.username,
    display_name: row.display_name,
    email: row.email,
    password: '',
    role: row.role,
    domain_ids: [...(row.domain_ids || [])]
  }
  editUserId.value = row.user_id
  isEditUser.value = true
  userDialogVisible.value = true
}

const saveUser = async () => {
  saving.value = true
  try {
    if (userForm.value.role !== 'admin' && !userForm.value.domain_ids.length) {
      ElMessage.warning('请至少选择一个可用业务分析域')
      return
    }
    if (isEditUser.value) {
      await systemApi.updateUser(editUserId.value, {
        display_name: userForm.value.display_name,
        email: userForm.value.email,
        role: userForm.value.role,
        domain_ids: userForm.value.domain_ids
      })
    } else {
      await systemApi.createUser(userForm.value)
    }
    ElMessage.success('用户已保存')
    userDialogVisible.value = false
    await loadUsers()
  } catch (e) {} finally { saving.value = false }
}

const disableUser = async (userId: string) => {
  await ElMessageBox.confirm('确定禁用此用户?', '确认')
  try {
    await systemApi.deleteUser(userId)
    ElMessage.success('用户已禁用')
    await loadUsers()
  } catch (e) {}
}

onMounted(() => {
  loadDomains()
  loadDataForTab(activeTab.value)
})
</script>

<style scoped>
.system-page { padding: 0; }
.section { padding: 0; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.section-header h4 { margin: 0; font-size: 16px; color: #1a3a5c; }
.form-tip { margin-top: 4px; color: #909399; font-size: 12px; line-height: 1.4; }
.domain-tag { margin: 2px 4px 2px 0; }
</style>
