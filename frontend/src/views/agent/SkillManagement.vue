<template>
  <div class="skill-management-page">
    <section class="page-header">
      <div>
        <div class="eyebrow">AGENT SKILL REGISTRY</div>
        <h2>技能管理</h2>
        <p>上传和管理当前业务分析域可被 Agent 使用的 Skill ZIP 包。每个包必须在根目录包含 <code>SKILL.md</code>。</p>
      </div>
      <el-button :icon="Refresh" @click="loadSkills">刷新</el-button>
    </section>

    <el-alert v-if="currentDomainId" :title="`当前业务分析域：${appStore.currentDomainName || currentDomainId}`" type="info" :closable="false" show-icon class="domain-alert" />
    <el-alert v-else title="请先在左侧选择业务分析域，才能管理该分析域的 Agent Skill。" type="warning" :closable="false" show-icon class="domain-alert" />

    <el-card shadow="never" class="upload-card">
      <el-upload drag accept=".zip,application/zip" :auto-upload="false" :show-file-list="false" :disabled="uploading || !currentDomainId" :on-change="handleFileChange">
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">将 Agent Skill ZIP 拖到这里，或 <em>点击选择文件</em></div>
        <template #tip><div class="el-upload__tip">支持最大 10MB 的 ZIP；必须包含根目录 <code>SKILL.md</code>，最多 30 个文件。</div></template>
      </el-upload>
    </el-card>

    <el-card shadow="never" class="list-card">
      <template #header><div class="card-header"><span>当前 Agent Skills</span><el-tag>{{ skills.length }} 个</el-tag></div></template>
      <el-table :data="skills" v-loading="loading" border stripe size="small" empty-text="尚未上传 Agent Skill ZIP 包">
        <el-table-column prop="skill_name" label="Agent / 技能名称" min-width="190" />
        <el-table-column prop="skill_desc" label="描述" min-width="300" show-overflow-tooltip />
        <el-table-column prop="package_filename" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column label="包信息" width="130"><template #default="{ row }">{{ formatSize(row.package_size) }} · {{ row.file_count }} 文件</template></el-table-column>
        <el-table-column prop="use_count" label="使用次数" width="90" align="center" />
        <el-table-column prop="uploaded_by" label="上传人" width="110" />
        <el-table-column label="上传时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button type="danger" link @click="removeSkill(row)">删除</el-button></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { agentApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const currentDomainId = computed(() => appStore.currentDomainId || '')
const skills = ref<any[]>([])
const loading = ref(false)
const uploading = ref(false)

const loadSkills = async () => {
  if (!currentDomainId.value) { skills.value = []; return }
  loading.value = true
  try {
    const res = await agentApi.listManagedSkills(currentDomainId.value)
    skills.value = res.data || []
  } catch (_) {
    skills.value = []
  } finally {
    loading.value = false
  }
}
const handleFileChange = async (file: any) => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择当前业务分析域'); return }
  const raw = file.raw as File | undefined
  if (!raw) return
  if (!raw.name.toLowerCase().endsWith('.zip')) { ElMessage.warning('请上传 ZIP 格式的 Agent Skill 包'); return }
  if (raw.size > 10 * 1024 * 1024) { ElMessage.warning('Skill ZIP 不能超过 10MB'); return }
  uploading.value = true
  try {
    await agentApi.uploadManagedSkill(currentDomainId.value, raw)
    ElMessage.success('Agent Skill 已上传并完成 SKILL.md 校验')
    await loadSkills()
  } catch (_) {
    // 请求错误由 API 拦截器提示。
  } finally {
    uploading.value = false
  }
}
const removeSkill = async (skill: any) => {
  try {
    await ElMessageBox.confirm(`确定删除 Agent Skill“${skill.skill_name}”吗？该操作无法恢复。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await agentApi.deleteManagedSkill(skill.managed_skill_id, currentDomainId.value)
    ElMessage.success('Agent Skill 已删除')
    await loadSkills()
  } catch (_) {}
}
const formatSize = (value: number) => value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(2)} MB` : `${Math.max(0, Math.round(value / 1024))} KB`
const formatDate = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'

watch(currentDomainId, loadSkills)
onMounted(loadSkills)
</script>

<style scoped>
.skill-management-page { min-height: calc(100vh - 86px); padding: 8px 0 20px; }.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin: 8px 0 16px; }.eyebrow { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }.page-header h2 { margin: 4px 0; color: #0f172a; font-size: 25px; }.page-header p { margin: 0; color: #64748b; font-size: 13px; }.page-header code, .el-upload__tip code { color: #2563eb; }.domain-alert { margin-bottom: 16px; }.upload-card, .list-card { border-color: #e4eaf2; }.upload-card { margin-bottom: 16px; }.upload-icon { margin-bottom: 10px; color: #2563eb; font-size: 42px; }.card-header { display: flex; align-items: center; justify-content: space-between; } @media (max-width: 760px) { .page-header { align-items: flex-start; flex-direction: column; } }
</style>
