<template>
  <div class="rules-page">
    <el-alert type="info" :closable="false" show-icon>
      <template #title>规则负责“何时、在什么条件下触发”；活动负责“触发后执行什么”。规则可引用本体实体/关系，活动可调用已配置的业务流程。</template>
    </el-alert>
    <div v-if="!domainId" class="empty"><el-empty description="请先在页面顶部选择业务分析域" /></div>
    <template v-else>
      <div class="summary">
        <el-card><div class="num">{{ rules.length }}</div><div>业务规则</div></el-card>
        <el-card><div class="num">{{ activities.length }}</div><div>业务活动</div></el-card>
        <el-card><div class="desc">配置路径：本体对象/关系 → 规则条件 → 业务活动 → 业务流程或执行参数</div></el-card>
      </div>
      <el-tabs v-model="tab">
        <el-tab-pane label="业务规则" name="rules">
          <div class="actions"><el-button type="primary" @click="openRule()">新增业务规则</el-button><el-button @click="loadAll">刷新</el-button></div>
          <el-table :data="rules" border stripe>
            <el-table-column prop="rule_name" label="规则名称" min-width="170" />
            <el-table-column prop="rule_category" label="类别" width="110" />
            <el-table-column prop="trigger_event" label="触发事件" width="150" />
            <el-table-column label="关联活动" min-width="140"><template #default="{ row }">{{ activityName(row.activity_id) || '未配置' }}</template></el-table-column>
            <el-table-column prop="priority" label="优先级" width="90" />
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column label="操作" width="150"><template #default="{ row }"><el-button link type="primary" @click="openRule(row)">编辑</el-button><el-button link type="danger" @click="removeRule(row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="业务活动" name="activities">
          <div class="actions"><el-button type="primary" @click="openActivity()">新增业务活动</el-button><el-button @click="loadAll">刷新</el-button></div>
          <el-table :data="activities" border stripe>
            <el-table-column prop="activity_name" label="活动名称" min-width="180" />
            <el-table-column prop="activity_type" label="活动类型" width="150" />
            <el-table-column label="关联流程" min-width="150"><template #default="{ row }">{{ processName(row.process_id) || '无' }}</template></el-table-column>
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column label="操作" width="150"><template #default="{ row }"><el-button link type="primary" @click="openActivity(row)">编辑</el-button><el-button link type="danger" @click="removeActivity(row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-dialog v-model="ruleVisible" :title="editingRule ? '编辑业务规则' : '新增业务规则'" width="720px">
      <el-form :model="ruleForm" label-width="110px">
        <el-form-item label="规则名称" required><el-input v-model="ruleForm.rule_name" /></el-form-item>
        <el-form-item label="规则类别"><el-select v-model="ruleForm.rule_category"><el-option label="校验规则" value="VALIDATION" /><el-option label="决策规则" value="DECISION" /><el-option label="派生规则" value="DERIVATION" /><el-option label="预警规则" value="ALERT" /></el-select></el-form-item>
        <el-form-item label="触发事件"><el-select v-model="ruleForm.trigger_event"><el-option label="数据创建" value="DATA_CREATED" /><el-option label="数据变更" value="DATA_CHANGED" /><el-option label="流程节点完成" value="FLOW_NODE_COMPLETED" /><el-option label="人工触发" value="MANUAL" /></el-select></el-form-item>
        <el-form-item label="约束实体"><el-select v-model="ruleForm.scope_entity_id" clearable filterable><el-option v-for="item in catalog.entities" :key="item.entity_id" :label="item.entity_display_name || item.entity_name" :value="item.entity_id" /></el-select></el-form-item>
        <el-form-item label="约束关系"><el-select v-model="ruleForm.scope_relation_id" clearable filterable><el-option v-for="item in catalog.relations" :key="item.relation_id" :label="item.relation_name" :value="item.relation_id" /></el-select></el-form-item>
        <el-form-item label="条件配置"><el-input v-model="ruleForm.condition_json" type="textarea" :rows="3" placeholder='JSON，例如：{"field":"defect_count","operator":">=","value":3,"logic":"AND"}' /></el-form-item>
        <el-form-item label="触发活动"><el-select v-model="ruleForm.activity_id" clearable><el-option v-for="item in activities" :key="item.activity_id" :label="item.activity_name" :value="item.activity_id" /></el-select></el-form-item>
        <el-form-item label="优先级"><el-input-number v-model="ruleForm.priority" :min="1" :max="100" /></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="ruleForm.status"><el-radio-button value="DRAFT">草稿</el-radio-button><el-radio-button value="ACTIVE">生效</el-radio-button><el-radio-button value="INACTIVE">停用</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="规则说明"><el-input v-model="ruleForm.rule_desc" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="ruleVisible=false">取消</el-button><el-button type="primary" @click="saveRule">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="activityVisible" :title="editingActivity ? '编辑业务活动' : '新增业务活动'" width="720px">
      <el-form :model="activityForm" label-width="110px">
        <el-form-item label="活动名称" required><el-input v-model="activityForm.activity_name" /></el-form-item>
        <el-form-item label="活动类型"><el-select v-model="activityForm.activity_type"><el-option label="通知" value="NOTIFY" /><el-option label="创建任务" value="CREATE_TASK" /><el-option label="调用流程" value="CALL_PROCESS" /><el-option label="数据操作" value="DATA_ACTION" /><el-option label="人工复核" value="MANUAL_REVIEW" /></el-select></el-form-item>
        <el-form-item label="关联流程"><el-select v-model="activityForm.process_id" clearable><el-option v-for="item in catalog.processes" :key="item.process_id" :label="item.process_name" :value="item.process_id" /></el-select></el-form-item>
        <el-form-item label="执行参数"><el-input v-model="activityForm.config_json" type="textarea" :rows="4" placeholder='JSON，例如：{"recipients":["quality_manager"],"template":"缺陷预警"}' /></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="activityForm.status"><el-radio-button value="ACTIVE">启用</el-radio-button><el-radio-button value="INACTIVE">停用</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="活动说明"><el-input v-model="activityForm.activity_desc" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="activityVisible=false">取消</el-button><el-button type="primary" @click="saveActivity">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { businessRuleApi } from '../../api'
import { useAppStore } from '../../stores/app'

const store = useAppStore(); const domainId = computed(() => store.currentDomainId)
const tab = ref('rules'), rules = ref<any[]>([]), activities = ref<any[]>([])
const catalog = reactive<any>({ entities: [], relations: [], processes: [] })
const ruleVisible = ref(false), activityVisible = ref(false), editingRule = ref(''), editingActivity = ref('')
const emptyRule = () => ({ rule_name:'', rule_category:'VALIDATION', rule_desc:'', trigger_event:'DATA_CHANGED', scope_entity_id:'', scope_relation_id:'', condition_json:'', activity_id:'', priority:50, status:'DRAFT' })
const emptyActivity = () => ({ activity_name:'', activity_type:'NOTIFY', activity_desc:'', process_id:'', config_json:'', status:'ACTIVE' })
const ruleForm = reactive<any>(emptyRule()), activityForm = reactive<any>(emptyActivity())
const activityName = (id: string) => activities.value.find(item => item.activity_id === id)?.activity_name
const processName = (id: string) => catalog.processes.find((item: any) => item.process_id === id)?.process_name
const loadAll = async () => { if (!domainId.value) return; try { const [r,a,c] = await Promise.all([businessRuleApi.listRules(domainId.value), businessRuleApi.listActivities(domainId.value), businessRuleApi.catalog(domainId.value)]); rules.value=r.data||[]; activities.value=a.data||[]; Object.assign(catalog,c.data||{}) } catch (e) {} }
const openRule = (item?: any) => { editingRule.value=item?.rule_id||''; Object.assign(ruleForm, item || emptyRule()); ruleVisible.value=true }
const openActivity = (item?: any) => { editingActivity.value=item?.activity_id||''; Object.assign(activityForm, item || emptyActivity()); activityVisible.value=true }
const validateJson = (value: string, label: string) => { if (!value?.trim()) return true; try { JSON.parse(value); return true } catch { ElMessage.warning(`${label}必须是合法 JSON`); return false } }
const saveRule = async () => { if (!ruleForm.rule_name.trim() || !validateJson(ruleForm.condition_json,'条件配置')) return; try { editingRule.value ? await businessRuleApi.updateRule(editingRule.value, ruleForm) : await businessRuleApi.createRule(domainId.value, ruleForm); ruleVisible.value=false; ElMessage.success('业务规则已保存'); loadAll() } catch(e){} }
const saveActivity = async () => { if (!activityForm.activity_name.trim() || !validateJson(activityForm.config_json,'执行参数')) return; try { editingActivity.value ? await businessRuleApi.updateActivity(editingActivity.value, activityForm) : await businessRuleApi.createActivity(domainId.value, activityForm); activityVisible.value=false; ElMessage.success('业务活动已保存'); loadAll() } catch(e){} }
const removeRule = async (item:any) => { try { await ElMessageBox.confirm(`确定删除规则「${item.rule_name}」？`,'确认',{type:'warning'}); await businessRuleApi.deleteRule(item.rule_id); ElMessage.success('已删除'); loadAll() } catch(e){} }
const removeActivity = async (item:any) => { try { await ElMessageBox.confirm(`确定删除活动「${item.activity_name}」？`,'确认',{type:'warning'}); await businessRuleApi.deleteActivity(item.activity_id); ElMessage.success('已删除'); loadAll() } catch(e){} }
watch(domainId, loadAll, { immediate:true })
</script>

<style scoped>
.rules-page{padding:4px 0}.empty{padding:70px}.summary{display:grid;grid-template-columns:160px 160px 1fr;gap:12px;margin:14px 0}.summary :deep(.el-card__body){padding:14px;color:#60758a}.num{font-size:25px;font-weight:700;color:#1a6fb3;margin-bottom:4px}.desc{padding-top:8px}.actions{display:flex;gap:8px;margin:0 0 12px}.el-select{width:100%}
</style>
