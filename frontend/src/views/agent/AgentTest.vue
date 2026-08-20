<template>
  <div class="agent-test-page">
    <div class="test-banner">
      <div>
        <div class="banner-title">智能体测试</div>
        <div class="banner-desc">
          选择技能管理中上传的 Skill 和目标数据源，以对话方式让 Agent 按 Skill 规则检索并分析数据；全过程与 SQL 可展开查看。
        </div>
      </div>
      <el-alert
        title="当前测试为 Agent + 上传 Skill 执行"
        description="Agent 会自动选择最相关的数据对象，仅执行受限只读采样 SQL，并将 Skill 加载、选表、数据检索和分析过程完整回放。"
        type="info"
        :closable="false"
        show-icon
      />
    </div>

    <div class="layout-grid">
      <el-card class="test-card" shadow="never">
        <template #header><span>测试配置</span></template>

        <el-form :model="form" label-width="96px">
          <el-form-item label="分析域">
            <el-select v-model="currentDomainId" placeholder="选择分析域" @change="handleDomainChange">
              <el-option v-for="item in domains" :key="item.domain_id" :label="item.domain_name" :value="item.domain_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="上传 Skill" required>
            <el-select v-model="form.managed_skill_id" placeholder="选择技能管理中上传的 Skill" filterable>
              <el-option v-for="item in managedSkills" :key="item.managed_skill_id" :label="item.skill_name" :value="item.managed_skill_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="测试模型" required>
            <el-select v-model="form.llm_config_id" placeholder="选择测试执行大模型" filterable>
              <el-option
                v-for="item in llmConfigs"
                :key="item.config_id"
                :label="formatModelOption(item)"
                :value="item.config_id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="对象数据库" required>
            <el-select v-model="form.source_id" placeholder="选择本体对象数据库" filterable @change="handleSourceChange">
              <el-option v-for="item in dataSources" :key="item.source_id" :label="item.source_name" :value="item.source_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="Schema">
            <el-select v-model="form.schema" placeholder="选择 Schema（可选）" filterable clearable>
              <el-option v-for="item in schemas" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="读取记录数">
            <el-input-number v-model="form.sample_limit" :min="1" :max="100" />
            <span class="sample-limit-hint">默认 100 条，仅用于 Agent 的受限只读数据检索</span>
          </el-form-item>
          <el-alert title="无需手工选择数据对象：启动后 Agent 会依据 Skill 和数据源自动选择对象。请在右侧对话框中自由提问。" type="info" :closable="false" class="object-selection-hint" />
        </el-form>

        <div class="test-actions">
          <el-button @click="loadAll">刷新</el-button>
          <el-button type="primary" @click="openTestDialog">执行测试</el-button>
        </div>
      </el-card>

      <div class="result-column">
        <el-card class="test-card" shadow="never">
          <template #header><span>技能信息</span></template>
          <div v-if="selectedManagedSkill" class="skill-profile">
            <div class="skill-name">{{ selectedManagedSkill.skill_name }}</div>
            <div class="skill-meta">
              <el-tag size="small" type="success">上传 Skill</el-tag>
              <el-tag size="small" effect="plain">{{ selectedManagedSkill.package_filename }}</el-tag>
              <el-tag size="small" :type="selectedManagedSkill.status === 'ACTIVE' ? 'success' : 'info'">{{ selectedManagedSkill.status }}</el-tag>
            </div>
            <div class="skill-desc">{{ selectedManagedSkill.skill_desc || '当前技能尚未补充说明。' }}</div>
            <div class="skill-section">
              <div class="section-title">使用情况</div>
              <div>包内文件 {{ selectedManagedSkill.file_count }} 个 · 已测试 {{ selectedManagedSkill.use_count }} 次</div>
            </div>
          </div>
          <el-empty v-else description="选择上传 Skill 后显示详情" :image-size="72" />
        </el-card>

        <el-card class="test-card" shadow="never">
          <template #header><span>测试历史</span></template>
          <el-table v-if="testSessions.length" :data="testSessions" size="small" class="history-table" @row-click="openTestHistory">
            <el-table-column prop="skill_name" label="Skill" min-width="150" show-overflow-tooltip />
            <el-table-column prop="last_question" label="最近问题" min-width="180" show-overflow-tooltip />
            <el-table-column prop="message_count" label="消息" width="64" align="center" />
            <el-table-column label="测试时间" min-width="145">
              <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="72" fixed="right">
              <template #default="{ row }"><el-link type="primary" @click.stop="openTestHistory(row)">查看</el-link></template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无测试历史。完成一次对话后会自动保留记录。" :image-size="76" />
        </el-card>
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      class="agent-test-dialog"
      width="860px"
      :fullscreen="dialogFullscreen"
      :draggable="!dialogFullscreen"
      :close-on-click-modal="false"
      :close-on-press-escape="!testing"
    >
      <template #header>
        <div class="dialog-header">
          <span>{{ `${historyView ? '测试历史' : 'Agent + Skill 对话测试'}${dialogSkillName ? ' · ' + dialogSkillName : ''}` }}</span>
          <el-button text type="primary" class="fullscreen-button" @click="dialogFullscreen = !dialogFullscreen">
            <el-icon><FullScreen /></el-icon>
            {{ dialogFullscreen ? '退出全屏' : '全屏' }}
          </el-button>
        </div>
      </template>
      <div class="dialog-model" v-if="form.llm_config_id">测试模型：{{ llmConfigs.find(item => item.config_id === form.llm_config_id)?.config_name }}</div>
      <div class="conversation-list dialog-conversation">
        <el-empty v-if="!result?.conversation?.length" description="请输入客户问题，Agent 将按 Skill 检索并分析数据。" :image-size="70" />
        <template v-else>
          <div v-for="(message, index) in result.conversation" :key="index" class="conversation-message" :class="message.role">
            <div class="conversation-role">{{ message.role === 'user' ? '客户' : 'Agent' }}</div>
            <div class="llm-output-box">{{ message.content }}</div>
            <div v-if="message.role === 'user' && !result.pending && getTurnResultForUserMessage(index)?.table_preview?.sample_rows?.length" class="data-answer-card">
              <div class="data-answer-title">问数结果 · {{ getTurnResultForUserMessage(index).table_preview.sample_rows.length }} 条</div>
              <el-table :data="getTurnResultForUserMessage(index).table_preview.sample_rows" border stripe size="small" max-height="280">
                <el-table-column v-for="column in (getTurnResultForUserMessage(index).table_preview.columns || []).slice(0, 12)" :key="column.column_name" :prop="column.column_name" :label="column.column_name" min-width="130" show-overflow-tooltip />
              </el-table>
            </div>
            <div v-if="message.role === 'user' && index === latestUserMessageIndex && result.pending" class="agent-pending">
              <el-icon class="is-loading"><Loading /></el-icon>
              Agent 正在依据 Skill 执行 Oracle Graph 查询并分析数据…
            </div>
          </div>
          <div v-if="!testing && result.agent_output" class="execution-entry">
            <el-link type="primary" @click="processDialogVisible = true">查看本次对话执行流程</el-link>
            <span>含执行摘要、选表依据与数据库 SQL</span>
          </div>
        </template>
      </div>
      <div v-if="!historyView" class="chat-composer">
        <el-input v-model="chatInput" type="textarea" :rows="3" placeholder="请输入客户问题，按 Ctrl / ⌘ + Enter 发送" :disabled="testing" @keydown.ctrl.enter.prevent="sendMessage" @keydown.meta.enter.prevent="sendMessage" />
        <el-button type="primary" :loading="testing" :disabled="!chatInput.trim()" @click="sendMessage">发送问题</el-button>
      </div>
    </el-dialog>

    <el-dialog v-model="processDialogVisible" title="本次对话执行流程" width="860px" append-to-body>
      <template v-if="result">
        <el-alert v-for="item in result.warnings || []" :key="item" :title="item" type="warning" :closable="false" show-icon class="warning-item" />
        <div class="result-block">
          <div class="section-title">Agent 执行摘要</div>
          <div class="summary-box">以下为本次对话可审计的执行摘要：Skill 加载、数据对象选择、数据读取与分析步骤。</div>
          <div class="execution-model" v-if="result.execution_model">测试模型：{{ result.execution_model.llm_config_name }} / {{ result.execution_model.llm_model_name }}</div>
        </div>
        <div class="result-block">
          <div class="section-title">执行步骤与决策依据</div>
          <el-collapse class="trace-collapse">
            <el-collapse-item v-for="item in result.execution_trace || []" :key="item.step_no" :name="item.step_no">
              <template #title><span class="trace-title"><b>步骤 {{ item.step_no }}</b> · {{ item.title }} · <el-tag size="small" type="success">{{ item.status }}</el-tag></span></template>
              <div class="trace-action">{{ item.detail }}</div>
              <pre v-if="item.sql" class="code-box">{{ item.sql }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
        <div class="result-block">
          <div class="section-title">本次执行的 Oracle Graph SQL</div>
          <div v-for="item in result.executed_queries || []" :key="item.sql" class="query-item">
            <div>{{ item.purpose }}（返回 {{ item.row_count }} 行）</div>
            <pre class="code-box">{{ item.sql }}</pre>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { FullScreen, Loading } from '@element-plus/icons-vue'
import { agentApi, domainApi, sourceApi, systemApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()

const domains = ref<any[]>([])
const managedSkills = ref<any[]>([])
const dataSources = ref<any[]>([])
const llmConfigs = ref<any[]>([])
const schemas = ref<string[]>([])
const testSessions = ref<any[]>([])
const result = ref<any>(null)
const testing = ref(false)
const dialogVisible = ref(false)
const dialogFullscreen = ref(false)
const processDialogVisible = ref(false)
const historyView = ref(false)
const currentSessionId = ref('')
const historySession = ref<any>(null)
const currentDomainId = ref(appStore.currentDomainId || '')

const form = ref({
  managed_skill_id: '',
  llm_config_id: '',
  source_id: '',
  schema: '',
  sample_limit: 100
})
const chatInput = ref('')

const selectedManagedSkill = computed(() => managedSkills.value.find(item => item.managed_skill_id === form.value.managed_skill_id))
const defaultTestModel = computed(() => llmConfigs.value.find(item => item.is_default === 'Y') || llmConfigs.value[0])
const dialogSkillName = computed(() => historyView.value ? historySession.value?.skill_name : selectedManagedSkill.value?.skill_name)
const latestUserMessageIndex = computed(() => {
  const messages = result.value?.conversation || []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'user') return index
  }
  return -1
})

const getTurnResultForUserMessage = (messageIndex: number | string) => {
  const messages = result.value?.conversation || []
  const numericMessageIndex = Number(messageIndex)
  if (messages[numericMessageIndex]?.role !== 'user') return null
  const userMessageNo = messages.slice(0, numericMessageIndex + 1).filter((item: any) => item?.role === 'user').length - 1
  const turnResults = result.value?.turn_results || []
  const matched = turnResults.find((item: any) => item?.user_message_no === userMessageNo)
  if (matched) return matched
  // 兼容尚未保存逐轮结果的历史会话：只能展示其中最后一次已保存的查询结果。
  return numericMessageIndex === latestUserMessageIndex.value && result.value?.table_preview ? result.value : null
}

const loadDomains = async () => {
  try {
    const res = await domainApi.list('ACTIVE')
    domains.value = res.data || []
    if (!currentDomainId.value && domains.value.length) {
      const first = domains.value[0]
      currentDomainId.value = first.domain_id
      appStore.setCurrentDomain(first.domain_id, first.domain_name)
    }
  } catch (e) {}
}

const loadDomainResources = async () => {
  if (!currentDomainId.value) {
    managedSkills.value = []
    dataSources.value = []
    return
  }
  try {
    const [skillRes, sourceRes] = await Promise.all([
      agentApi.listManagedSkills(currentDomainId.value),
      sourceApi.listDataSources(currentDomainId.value)
    ])
    managedSkills.value = (skillRes.data || []).filter((item: any) => item.status === 'ACTIVE')
    dataSources.value = sourceRes.data || []
  } catch (e) {}
}

const loadLLMConfigs = async () => {
  try {
    const res = await systemApi.getLLMConfigs()
    llmConfigs.value = (res.data || []).filter((item: any) => item.is_active === 'Y')
    if (!form.value.llm_config_id) {
      form.value.llm_config_id = defaultTestModel.value?.config_id || ''
    }
  } catch (e) {
    llmConfigs.value = []
  }
}

const loadSchemas = async () => {
  if (!form.value.source_id) {
    schemas.value = []
    return
  }
  try {
    const res = await sourceApi.getSchemas(form.value.source_id)
    schemas.value = res.data?.schemas || []
    if (!form.value.schema) {
      form.value.schema = res.data?.default_schema || ''
    }
  } catch (e) {}
}

const loadAll = async () => {
  await loadDomains()
  await loadLLMConfigs()
  await loadDomainResources()
  await loadTestSessions()
  if (form.value.source_id) {
    await loadSchemas()
  }
}

const loadTestSessions = async () => {
  try {
    const res = await agentApi.listManagedSkillTestSessions()
    testSessions.value = res.data || []
  } catch (e) {}
}

const handleDomainChange = async (val: string) => {
  const domain = domains.value.find(item => item.domain_id === val)
  if (domain) appStore.setCurrentDomain(domain.domain_id, domain.domain_name)
  form.value.managed_skill_id = ''
  result.value = null
  currentSessionId.value = ''
  await loadDomainResources()
}

const handleSourceChange = async () => {
  form.value.schema = ''
  await loadSchemas()
}

const openTestDialog = () => {
  if (!form.value.managed_skill_id) { ElMessage.warning('请选择技能管理中上传的 Skill'); return }
  if (!form.value.llm_config_id) { ElMessage.warning('请选择测试模型'); return }
  if (!form.value.source_id) { ElMessage.warning('请选择对象数据库'); return }
  result.value = null
  chatInput.value = ''
  currentSessionId.value = ''
  historySession.value = null
  historyView.value = false
  dialogFullscreen.value = false
  dialogVisible.value = true
}

const openTestHistory = async (row: any) => {
  try {
    const res = await agentApi.getManagedSkillTestSession(row.session_id)
    historySession.value = res.data
    result.value = res.data?.result || { conversation: res.data?.conversation || [] }
    currentSessionId.value = row.session_id
    historyView.value = true
    chatInput.value = ''
    dialogFullscreen.value = false
    dialogVisible.value = true
  } catch (e) {}
}

const sendMessage = async () => {
  const question = chatInput.value.trim()
  if (!question) return
  const previousResult = result.value
  const previousConversation = previousResult?.conversation || []
  testing.value = true
  chatInput.value = ''
  result.value = {
    ...(previousResult || {}),
    conversation: [...previousConversation, { role: 'user', content: question }],
    pending: true
  }
  try {
    const res = await agentApi.testManagedSkill(form.value.managed_skill_id, {
      domain_id: currentDomainId.value,
      llm_config_id: form.value.llm_config_id,
      source_id: form.value.source_id,
      schema: form.value.schema || null,
      test_question: question,
      sample_limit: form.value.sample_limit,
      session_id: currentSessionId.value || null,
      conversation_history: previousConversation
    })
    result.value = res.data
    currentSessionId.value = res.data?.session_id || currentSessionId.value
    await loadTestSessions()
  } catch (e) {
    result.value = previousResult
    chatInput.value = question
  } finally {
    testing.value = false
  }
}

const formatModelOption = (item: any) => `${item.config_name} / ${item.model_name}`
const formatDateTime = (value: string) => value ? String(value).replace('T', ' ').slice(0, 16) : '-'

watch(() => appStore.currentDomainId, async (val) => {
  if (!val || val === currentDomainId.value) return
  currentDomainId.value = val
  form.value.managed_skill_id = ''
  form.value.llm_config_id = defaultTestModel.value?.config_id || ''
  result.value = null
  currentSessionId.value = ''
  historyView.value = false
  await loadDomainResources()
})

onMounted(async () => {
  await loadAll()
})
</script>

<style scoped>
.agent-test-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 110px);
}
.test-banner {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  padding: 18px 22px;
  border-radius: 16px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.3), transparent 42%),
    linear-gradient(135deg, #17324d 0%, #295c82 55%, #dfeef8 100%);
  color: #fff;
}
.banner-title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 8px;
}
.banner-desc {
  max-width: 720px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.9);
}
.layout-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(500px, 1.1fr);
  gap: 16px;
  align-items: start;
}
.test-card {
  border-radius: 16px;
  border: 1px solid #d9e4ee;
}
.test-actions {
  display: flex;
  gap: 8px;
}
.result-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.history-table { width: 100%; cursor: pointer; }
.skill-profile {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.skill-name {
  font-size: 18px;
  font-weight: 700;
  color: #1f3f5d;
}
.skill-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.skill-desc {
  color: #5d6a78;
  line-height: 1.7;
}
.skill-section,
.result-block {
  margin-top: 12px;
}
.section-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 700;
  color: #25527c;
}
.summary-box {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f4f9fd;
  border: 1px solid #d9ebf7;
  color: #435364;
  line-height: 1.7;
}
.focus-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.execution-model {
  margin-top: 10px;
  color: #1f5f8b;
  font-size: 12px;
  font-weight: 600;
}
.llm-output-box {
  padding: 14px 16px;
  border-radius: 12px;
  background: #fffdf6;
  border: 1px solid #efe1a8;
  color: #473f21;
  white-space: pre-wrap;
  line-height: 1.8;
}
.conversation-list { display: flex; flex-direction: column; gap: 10px; }
.dialog-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-right: 28px; font-size: 16px; font-weight: 600; color: #303133; }
.fullscreen-button { flex: 0 0 auto; }
.fullscreen-button .el-icon { margin-right: 4px; }
:deep(.agent-test-dialog.el-dialog) { min-width: 640px; min-height: 460px; max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); resize: both; overflow: auto; }
:deep(.agent-test-dialog.el-dialog.is-fullscreen) { min-width: 0; min-height: 0; max-width: none; max-height: none; resize: none; }
.dialog-conversation { min-height: 300px; max-height: min(460px, calc(100vh - 300px)); overflow-y: auto; padding: 2px 6px 2px 2px; }
:deep(.agent-test-dialog.el-dialog.is-fullscreen) .dialog-conversation { max-height: calc(100vh - 300px); }
.dialog-model { margin-bottom: 12px; color: #1f5f8b; font-size: 12px; font-weight: 600; }
.conversation-message { display: flex; flex-direction: column; gap: 5px; }
.conversation-message.user { align-items: flex-end; }
.conversation-role { color: #58728b; font-size: 12px; font-weight: 700; }
.conversation-message.user .llm-output-box { max-width: 78%; background: #eef7ff; border-color: #cfe5f6; color: #24435c; }
.conversation-message.assistant .llm-output-box { background: #fffdf6; border-color: #efe1a8; color: #473f21; }
.data-answer-card { align-self: stretch; margin-top: 8px; padding: 12px; border-radius: 12px; background: #f4f9fd; border: 1px solid #d9ebf7; }
.data-answer-title { margin-bottom: 8px; color: #25527c; font-weight: 700; font-size: 13px; }
.agent-pending { display: inline-flex; align-items: center; gap: 8px; margin-top: 8px; padding: 10px 12px; border-radius: 10px; background: #f4f9fd; color: #47708f; font-size: 13px; }
.chat-composer { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.chat-composer .el-button { align-self: flex-end; }
.execution-entry { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 10px; background: #f4f9fd; color: #5c7184; font-size: 12px; }
.sample-limit-hint { margin-left: 10px; color: #8493a1; font-size: 12px; }
.trace-title { display: inline-flex; align-items: center; gap: 6px; }
.query-item + .query-item { margin-top: 12px; }
.trace-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.trace-item {
  display: flex;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e3edf5;
}
.trace-step {
  display: inline-flex;
  width: 26px;
  height: 26px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #295c82;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}
.trace-name {
  font-weight: 700;
  color: #22384d;
}
.trace-name span {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #6f8090;
}
.trace-action {
  margin-top: 6px;
  color: #526272;
  line-height: 1.7;
}
.code-box {
  margin: 0;
  padding: 14px;
  border-radius: 12px;
  background: #0f1b28;
  color: #d9e8f6;
  white-space: pre-wrap;
  word-break: break-word;
}
.warning-item + .warning-item {
  margin-top: 10px;
}
@media (max-width: 1200px) {
  .layout-grid,
  .test-banner {
    grid-template-columns: 1fr;
  }
}
</style>
