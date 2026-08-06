<template>
  <div class="datasource-page">
    <div class="page-header">
      <h3>Oracle 26ai 数据源管理</h3>
      <el-button type="primary" @click="showCreateDialog">新建数据源</el-button>
    </div>

    <!-- Data Source List -->
    <el-table :data="dataSources" border stripe v-loading="loadingTable">
      <el-table-column prop="source_name" label="名称" width="200" />
      <el-table-column label="连接地址" min-width="280">
        <template #default="{ row }">
          <code>{{ row.host }}:{{ row.port }}
            <template v-if="row.service_name">/{{ row.service_name }}</template>
            <template v-if="row.sid">:{{ row.sid }}</template>
          </code>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="120" />
      <el-table-column prop="schema_name" label="Schema" width="120" />
      <el-table-column prop="business_domain_name" label="业务分析域" width="180">
        <template #default="{ row }">
          <el-tag v-if="row.business_domain_name" size="small" type="success">{{ row.business_domain_name }}</el-tag>
          <span v-else class="muted-text">未指定</span>
        </template>
      </el-table-column>
      <el-table-column prop="db_type" label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small">{{ row.db_type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="connection_status" label="连接状态" width="120">
        <template #default="{ row }">
          <el-tag
            :type="row.connection_status === 'CONNECTED' ? 'success' : row.connection_status === 'DISCONNECTED' ? 'danger' : 'info'"
            size="small"
          >
            {{ row.connection_status === 'CONNECTED' ? '已连接' : row.connection_status === 'DISCONNECTED' ? '连接失败' : '未测试' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_default" label="默认" width="70">
        <template #default="{ row }">
          <el-tag :type="row.is_default === 'Y' ? 'success' : 'info'" size="small">{{ row.is_default === 'Y' ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="启用" width="70">
        <template #default="{ row }">
          <el-tag :type="row.is_active === 'Y' ? 'success' : 'danger'" size="small">{{ row.is_active === 'Y' ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="success" @click="testConnection(row)" :loading="row._testing">测试连接</el-button>
          <el-button size="small" type="primary" @click="showEditDialog(row)">编辑</el-button>
          <el-button size="small" @click="browseSourceTables(row)">浏览表</el-button>
          <el-button size="small" type="danger" @click="deleteSource(row.source_id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loadingTable && dataSources.length === 0" description="暂无数据源配置，请添加Oracle 26ai数据库连接" />

    <!-- Table Browser Dialog -->
    <el-dialog v-model="browserVisible" :title="`数据源表浏览 - ${browserSource?.source_name}`" width="900px">
      <div class="browser-meta">
        <el-tag type="info" size="small">Schema: {{ browserSchema }}</el-tag>
        <el-tag type="info" size="small" v-if="browserUser">连接用户: {{ browserUser }}</el-tag>
        <span class="browser-count">共 {{ browserTables.length }} 张表</span>
      </div>
      <div class="browser-layout">
        <div class="browser-left">
          <el-input v-model="tableSearch" placeholder="搜索表名" size="small" clearable />
          <div class="browser-table-list">
            <div
              v-for="t in filteredBrowserTables"
              :key="`${t.owner || browserSchema}.${t.table_name}`"
              class="browser-table-item"
              :class="{ active: browserSelectedTable === t.table_name && browserSelectedOwner === (t.owner || browserSchema) }"
              @click="selectBrowserTable(t)"
            >
              <el-icon><Grid /></el-icon>
              <span>{{ t.table_name }}</span>
              <span class="browser-comment">{{ t.comments || '' }}</span>
            </div>
            <el-empty v-if="filteredBrowserTables.length === 0" description="未找到匹配的表" />
          </div>
        </div>
        <div class="browser-right" v-loading="browserColumnsLoading">
          <div v-if="browserSelectedTable">
            <h5>{{ browserSelectedTable }} - 字段结构</h5>
            <div class="browser-detail-meta">
              <el-tag type="info" size="small">Schema: {{ browserSelectedOwner || browserSchema || '-' }}</el-tag>
              <span v-if="browserTableDetail?.table_comment">表描述: {{ browserTableDetail.table_comment }}</span>
            </div>
            <el-alert
              v-if="browserColumnError"
              :title="browserColumnError"
              type="warning"
              show-icon
              :closable="false"
              class="browser-alert"
            />
            <el-table :data="browserColumns" border stripe size="small" max-height="400">
              <el-table-column prop="column_name" label="字段名" width="150" />
              <el-table-column prop="data_type" label="数据类型" width="160" />
              <el-table-column prop="nullable" label="可空" width="60">
                <template #default="{ row: c }">
                  <el-tag :type="c.nullable === 'Y' ? 'info' : 'danger'" size="small">{{ c.nullable }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="default_value" label="默认值" width="100" />
              <el-table-column prop="comments" label="列描述" min-width="180" show-overflow-tooltip />
            </el-table>
            <el-empty v-if="!browserColumnsLoading && !browserColumnError && browserColumns.length === 0" description="未获取到字段信息" />
          </div>
          <el-empty v-else description="请从左侧选择表" />
        </div>
      </div>
    </el-dialog>

    <!-- Edit / Create Dialog -->
    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑数据源' : '新建 Oracle 数据源'" width="550px">
      <el-form :model="form" label-width="110px" :rules="rules" ref="formRef">
        <el-form-item label="配置名称" prop="source_name">
          <el-input v-model="form.source_name" placeholder="如：生产环境Oracle" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.source_desc" placeholder="数据源用途描述" />
        </el-form-item>
        <el-form-item label="数据库类型">
          <el-select v-model="form.db_type">
            <el-option label="Oracle 26ai" value="oracle" />
            <el-option label="Oracle (通用)" value="oracle_generic" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">连接信息</el-divider>
        <el-form-item label="主机地址" prop="host">
          <el-input v-model="form.host" placeholder="如：192.168.1.100" />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number v-model="form.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="连接方式">
          <el-radio-group v-model="connectionMode">
            <el-radio value="service">Service Name</el-radio>
            <el-radio value="sid">SID</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="connectionMode === 'service'" label="Service Name">
          <el-input v-model="form.service_name" placeholder="如：ORCLPDB1" />
        </el-form-item>
        <el-form-item v-else label="SID">
          <el-input v-model="form.sid" placeholder="如：ORCL" />
        </el-form-item>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="Oracle 用户名" />
        </el-form-item>
        <el-form-item label="密码" :prop="isEditing ? '' : 'password'">
          <el-input v-model="form.password" type="password" show-password placeholder="Oracle 密码" />
        </el-form-item>
        <el-form-item label="Schema">
          <el-input v-model="form.schema_name" placeholder="Schema名称，默认使用用户名" />
        </el-form-item>
        <el-form-item label="业务分析域">
          <el-select v-model="form.business_domain_id" clearable filterable placeholder="可选，作为该数据源的业务访问对象">
            <el-option v-for="domain in domains" :key="domain.domain_id" :label="domain.domain_name" :value="domain.domain_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="form.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveDataSource" :loading="saving">{{ isEditing ? '更新' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid } from '@element-plus/icons-vue'
import { datasourceApi, domainApi } from '../../api'

const loadingTable = ref(false)
const saving = ref(false)
const dataSources = ref<any[]>([])
const domains = ref<any[]>([])
const dialogVisible = ref(false)
const isEditing = ref(false)
const editingId = ref('')
const formRef = ref()
const connectionMode = ref('service')

const form = reactive({
  source_name: '', source_desc: '', db_type: 'oracle',
  host: '', port: 1521, service_name: '', sid: '',
  username: '', password: '', schema_name: '', business_domain_id: '', is_default: false
})

const rules = {
  source_name: [{ required: true, message: '请输入配置名称' }],
  host: [{ required: true, message: '请输入主机地址' }],
  port: [{ required: true, message: '请输入端口' }],
  username: [{ required: true, message: '请输入用户名' }],
  password: [{ required: true, message: '请输入密码' }]
}

// Table Browser
const browserVisible = ref(false)
const browserSource = ref<any>(null)
const browserTables = ref<any[]>([])
const browserColumns = ref<any[]>([])
const browserTableDetail = ref<any>(null)
const browserSelectedTable = ref('')
const browserSelectedOwner = ref('')
const browserColumnsLoading = ref(false)
const browserColumnError = ref('')
const browserSchema = ref('')
const browserUser = ref('')
const tableSearch = ref('')
const filteredBrowserTables = computed(() => {
  if (!tableSearch.value) return browserTables.value
  return browserTables.value.filter((t: any) => t.table_name.toLowerCase().includes(tableSearch.value.toLowerCase()))
})

const loadDomains = async () => {
  const res = await domainApi.list('ACTIVE')
  domains.value = res.data || []
}

const loadDataSources = async () => {
  loadingTable.value = true
  try {
    const res: any = await datasourceApi.list()
    dataSources.value = (res.data || []).map((ds: any) => ({ ...ds, _testing: false }))
  } catch {} finally { loadingTable.value = false }
}

const showCreateDialog = () => {
  isEditing.value = false
  editingId.value = ''
  connectionMode.value = 'service'
  Object.assign(form, {
    source_name: '', source_desc: '', db_type: 'oracle',
    host: '', port: 1521, service_name: '', sid: '',
    username: '', password: '', schema_name: '', business_domain_id: '', is_default: false
  })
  dialogVisible.value = true
}

const showEditDialog = (row: any) => {
  isEditing.value = true
  editingId.value = row.source_id
  connectionMode.value = row.service_name ? 'service' : 'sid'
  Object.assign(form, {
    source_name: row.source_name,
    source_desc: row.source_desc || '',
    db_type: row.db_type,
    host: row.host,
    port: row.port || 1521,
    service_name: row.service_name || '',
    sid: row.sid || '',
    username: row.username,
    password: '',
    schema_name: row.schema_name || '',
    business_domain_id: row.business_domain_id || '',
    is_default: row.is_default === 'Y'
  })
  dialogVisible.value = true
}

const saveDataSource = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload: any = { ...form }
    // Clean empty strings for optional fields
    if (!payload.service_name) delete payload.service_name
    if (!payload.sid) delete payload.sid
    if (!payload.schema_name) delete payload.schema_name
    if (!payload.business_domain_id) payload.business_domain_id = null

    if (isEditing.value) {
      // Don't send password if empty when editing
      if (!payload.password) (payload as any).password = undefined
      await datasourceApi.update(editingId.value, payload)
      ElMessage.success('数据源已更新')
    } else {
      await datasourceApi.create(payload)
      ElMessage.success('数据源已创建')
    }
    dialogVisible.value = false
    await loadDataSources()
  } catch {} finally { saving.value = false }
}

const testConnection = async (row: any) => {
  row._testing = true
  try {
    const res: any = await datasourceApi.test(row.source_id)
    if (res.data?.success) {
      ElMessage.success({ message: res.data.message || '连接成功!', duration: 3000 })
      row.connection_status = 'CONNECTED'
    } else {
      ElMessage.error({ message: res.data?.message || '连接失败', duration: 5000 })
      row.connection_status = 'DISCONNECTED'
    }
    row.last_test_time = new Date().toISOString()
  } catch {
    row.connection_status = 'DISCONNECTED'
  } finally { row._testing = false }
}

const deleteSource = async (sourceId: string) => {
  try { await ElMessageBox.confirm('确定删除此数据源?', '确认', { type: 'warning' }) } catch { return }
  try { await datasourceApi.remove(sourceId); ElMessage.success('已删除'); await loadDataSources() } catch {}
}

const browseSourceTables = async (row: any) => {
  browserSource.value = row
  browserVisible.value = true
  browserTables.value = []
  browserColumns.value = []
  browserTableDetail.value = null
  browserSelectedTable.value = ''
  browserSelectedOwner.value = ''
  browserColumnError.value = ''
  browserSchema.value = ''
  browserUser.value = ''
  tableSearch.value = ''

  try {
    const res: any = await datasourceApi.listTables(row.source_id)
    browserTables.value = res.data?.tables || []
    browserSchema.value = res.data?.schema || ''
    browserUser.value = res.data?.connected_user || ''
  } catch { browserTables.value = [] }
}

const selectBrowserTable = async (table: any) => {
  const tableName = table.table_name
  const owner = table.owner || browserSchema.value
  browserSelectedTable.value = tableName
  browserSelectedOwner.value = owner
  browserColumns.value = []
  browserTableDetail.value = null
  browserColumnError.value = ''
  browserColumnsLoading.value = true
  try {
    const res: any = await datasourceApi.getTableColumns(
      browserSource.value.source_id,
      tableName,
      owner ? { schema: owner } : {}
    )
    if (res.code !== 200) {
      browserColumnError.value = res.message || '获取表结构失败'
      return
    }
    browserTableDetail.value = res.data || null
    browserSelectedOwner.value = res.data?.owner || owner
    browserSelectedTable.value = res.data?.table_name || tableName
    browserColumns.value = res.data?.columns || []
    if (browserColumns.value.length === 0) {
      browserColumnError.value = '未查询到字段信息，请检查该用户是否有访问此表结构的权限'
    }
  } catch {
    browserColumnError.value = '获取表结构失败'
    browserColumns.value = []
  } finally {
    browserColumnsLoading.value = false
  }
}

onMounted(async () => {
  await loadDomains()
  await loadDataSources()
})
</script>

<style scoped>
.datasource-page { padding: 0; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-header h3 { margin: 0; color: #1a3a5c; font-size: 17px; }
.muted-text { color: #909399; font-size: 12px; }

/* Browser */
.browser-layout { display: flex; gap: 12px; height: 460px; }
.browser-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #eee; }
.browser-count { font-size: 12px; color: #999; margin-left: auto; }
.browser-left { width: 280px; border: 1px solid #e8e8e8; border-radius: 6px; padding: 10px; overflow-y: auto; flex-shrink: 0; }
.browser-right { flex: 1; border: 1px solid #e8e8e8; border-radius: 6px; padding: 10px; overflow-y: auto; }
.browser-right h5 { margin: 0 0 10px; font-size: 14px; color: #333; }
.browser-detail-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 12px; color: #666; }
.browser-alert { margin-bottom: 10px; }
.browser-table-list { margin-top: 8px; }
.browser-table-item { display: flex; align-items: center; gap: 6px; padding: 6px 8px; cursor: pointer; border-radius: 4px; font-size: 13px; }
.browser-table-item:hover { background: #f0f5ff; }
.browser-table-item.active { background: #1a3a5c; color: #fff; }
.browser-comment { font-size: 11px; color: #999; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.browser-table-item.active .browser-comment { color: rgba(255,255,255,0.7); }
</style>
