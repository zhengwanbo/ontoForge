<template>
  <div class="home-dashboard">
    <section class="hero">
      <div>
        <div class="eyebrow">Oracle Ontology Forge</div>
        <h1>{{ currentDomainName || '平台首页' }}</h1>
        <p>查看当前业务分析域的本体建设进度、阻断问题与最近活动。</p>
      </div>
      <div class="hero-actions">
        <el-button @click="loadOverview" :loading="loading"><el-icon><Refresh /></el-icon>刷新概览</el-button>
        <el-button type="primary" @click="go('/business/ontology')">开始本体生成</el-button>
      </div>
    </section>

    <el-empty v-if="!currentDomainId" description="请先在页面右上角选择业务分析域" />
    <template v-else>
      <section class="quick-actions">
        <el-button type="primary" plain @click="go('/business/ontology')"><el-icon><Share /></el-icon>本体关系构建</el-button>
        <el-button type="success" plain @click="go('/mapping/operation')"><el-icon><MagicStick /></el-icon>数据映射</el-button>
        <el-button type="warning" plain @click="go('/mapping/manage')"><el-icon><Connection /></el-icon>映射关系检查</el-button>
        <el-button type="danger" plain @click="go('/ddl')"><el-icon><DocumentCopy /></el-icon>生成 DDL</el-button>
        <el-button plain @click="go('/browse/graph-query')"><el-icon><Search /></el-icon>图数据查询</el-button>
      </section>

      <el-skeleton v-if="loading && !overview" :rows="10" animated />
      <template v-else>
        <section class="metric-grid">
          <div v-for="item in metrics" :key="item.label" class="metric-card" :class="item.tone" @click="item.path && go(item.path)">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <small>{{ item.detail }}</small>
          </div>
        </section>

        <section class="content-grid">
          <el-card class="stage-card" shadow="never">
            <template #header><div class="card-title"><span>本体建设进度</span><small>按当前分析域统计</small></div></template>
            <div class="stage-list">
              <div v-for="(stage, index) in overview?.stages || []" :key="stage.key" class="stage-row" :class="{ complete: stage.complete }">
                <div class="stage-index">{{ stage.complete ? '✓' : Number(index) + 1 }}</div>
                <div><strong>{{ stage.title }}</strong><span>{{ stage.detail }}</span></div>
              </div>
            </div>
          </el-card>

          <el-card class="blocker-card" shadow="never">
            <template #header><div class="card-title"><span>当前待办与阻断项</span><el-tag :type="blockers.length ? 'danger' : 'success'" size="small">{{ blockers.length ? `${blockers.length} 项待处理` : '检查通过' }}</el-tag></div></template>
            <el-empty v-if="!blockers.length" description="当前没有阻断 DDL 生成的问题" :image-size="72" />
            <div v-else class="blocker-list">
              <div v-for="item in blockers" :key="`${item.code}-${item.relation_id || item.entity_id}`" class="blocker-item">
                <div><strong>{{ item.title }}</strong><p>{{ item.message }}</p></div>
                <el-button link type="primary" @click="fixIssue(item)">{{ item.action_label || '去处理' }}</el-button>
              </div>
            </div>
          </el-card>
        </section>

        <section class="content-grid lower-grid">
          <el-card shadow="never">
            <template #header><div class="card-title"><span>本体关系概览</span><small>{{ overview?.graph_overview?.relation_count || 0 }} 条关系</small></div></template>
            <div v-if="overview?.graph_overview?.nodes?.length" class="node-cloud">
              <div v-for="node in overview.graph_overview.nodes" :key="node.id" class="overview-node" :class="node.mapped ? 'mapped' : 'pending'" @click="go('/business/ontology')">
                <i></i>{{ node.name }}
              </div>
            </div>
            <el-empty v-else description="尚未创建本体对象" :image-size="72" />
          </el-card>

          <el-card shadow="never">
            <template #header><div class="card-title"><span>最近活动</span><small>最近 10 条</small></div></template>
            <el-empty v-if="!(overview?.activities || []).length" description="暂无活动记录" :image-size="72" />
            <div v-else class="activity-list">
              <div v-for="activity in overview.activities" :key="`${activity.time}-${activity.title}`" class="activity-item" @click="go(activity.target)">
                <i :class="activity.activity_type"></i><div><strong>{{ activity.title }}</strong><span>{{ activity.detail }}</span></div><time>{{ formatTime(activity.time) }}</time>
              </div>
            </div>
          </el-card>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { dashboardApi } from '../api'
import { useAppStore } from '../stores/app'

const router = useRouter()
const appStore = useAppStore()
const loading = ref(false)
const overview = ref<any>(null)
const currentDomainId = computed(() => appStore.currentDomainId)
const currentDomainName = computed(() => appStore.currentDomainName)
const blockers = computed(() => overview.value?.blockers || [])
const metrics = computed(() => {
  const summary = overview.value?.summary || {}
  return [
    { label: '数据源', value: summary.data_source_count || 0, detail: `${summary.connected_source_count || 0} 个已连接`, tone: 'blue', path: '/source/browse' },
    { label: '本体实体', value: summary.entity_count || 0, detail: `${summary.property_count || 0} 个属性`, tone: 'green', path: '/business/ontology' },
    { label: '本体关系', value: summary.relation_count || 0, detail: `${summary.configured_relation_count || 0} 条已配置`, tone: 'violet', path: '/mapping/manage' },
    { label: '已映射实体', value: `${summary.mapped_entity_count || 0}/${summary.entity_count || 0}`, detail: `${summary.mapped_property_count || 0} 个字段已映射`, tone: 'orange', path: '/mapping/manage' },
    { label: 'DDL 阻断项', value: summary.ddl_blocker_count || 0, detail: `最近 DDL：${summary.latest_ddl_status || 'NOT_STARTED'}`, tone: 'red', path: '/ddl' },
  ]
})

const loadOverview = async () => {
  if (!currentDomainId.value) { overview.value = null; return }
  loading.value = true
  try {
    const res = await dashboardApi.getOverview(currentDomainId.value)
    overview.value = res.data || null
  } finally {
    loading.value = false
  }
}
const go = (path: string) => router.push(path)
const fixIssue = (issue: any) => {
  const target = issue.navigate_to || { path: '/mapping/manage', query: {} }
  router.push({ path: target.path, query: target.query || {} })
}
const formatTime = (value: string) => value ? value.replace('T', ' ').slice(0, 16) : '-'

watch(currentDomainId, loadOverview)
onMounted(loadOverview)
</script>

<style scoped>
.home-dashboard { max-width: 1480px; margin: 0 auto; padding: 8px 4px 28px; }
.hero { display:flex; justify-content:space-between; gap:20px; padding:26px 30px; border-radius:16px; color:#fff; background:linear-gradient(122deg,#173c64,#2b6e90 68%,#3d8f8a); }
.hero h1 { margin:5px 0 8px; font-size:27px; }.hero p,.eyebrow { margin:0; opacity:.84; }.eyebrow { font-size:12px; letter-spacing:1px; }.hero-actions { display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
.quick-actions { display:flex; gap:10px; flex-wrap:wrap; margin:16px 0; }.metric-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; }
.metric-card { cursor:pointer; display:flex; min-height:104px; flex-direction:column; justify-content:center; padding:15px 17px; border:1px solid #e5edf5; border-radius:12px; background:#fff; transition:.2s; }.metric-card:hover { transform:translateY(-2px); box-shadow:0 8px 20px rgba(24,62,98,.12); }.metric-card span,.metric-card small { color:#6a7d91; font-size:12px; }.metric-card strong { margin:6px 0; color:#1f3b57; font-size:25px; }.blue { border-top:3px solid #418ac5 }.green { border-top:3px solid #35a37d }.violet { border-top:3px solid #8167c8 }.orange { border-top:3px solid #db9a39 }.red { border-top:3px solid #dc6570 }
.content-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }.lower-grid { grid-template-columns:.9fr 1.1fr }.card-title { display:flex; align-items:center; justify-content:space-between; gap:12px; color:#1f3b57; font-weight:600; }.card-title small { color:#7d8da0; font-weight:400; }.stage-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }.stage-row { display:flex; gap:9px; min-height:65px; padding:11px; border:1px solid #e4ebf2; border-radius:10px; }.stage-index { display:grid; place-items:center; flex:0 0 23px; height:23px; border-radius:50%; color:#77899a; background:#edf2f6; font-size:12px; }.stage-row.complete { border-color:#bce5d4; background:#f3fbf7; }.stage-row.complete .stage-index { color:#fff; background:#32a477; }.stage-row strong,.stage-row span { display:block; }.stage-row strong { color:#284661; font-size:13px; }.stage-row span { margin-top:5px; color:#75889b; font-size:11px; line-height:1.35; }
.blocker-list { display:flex; flex-direction:column; gap:9px; }.blocker-item { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 12px; border-left:3px solid #e46c72; border-radius:6px; background:#fff7f7; }.blocker-item strong { color:#8c3d43; font-size:13px; }.blocker-item p { margin:4px 0 0; color:#765d60; font-size:12px; line-height:1.45; }.node-cloud { display:flex; flex-wrap:wrap; gap:10px; align-content:flex-start; min-height:146px; }.overview-node { padding:9px 12px; border:1px solid #dce7f0; border-radius:18px; color:#34516c; font-size:13px; cursor:pointer; }.overview-node i { display:inline-block; width:8px; height:8px; margin-right:6px; border-radius:50%; }.overview-node.mapped { background:#f2fbf7; border-color:#c2e8d8; }.overview-node.mapped i { background:#34a477; }.overview-node.pending { background:#f4f7fa; border-color:#d6e0e9; }.overview-node.pending i { background:#7b8798; }.activity-list { max-height:180px; overflow:auto; }.activity-item { display:grid; grid-template-columns:10px 1fr auto; gap:9px; align-items:start; padding:8px 0; border-bottom:1px solid #edf1f4; cursor:pointer; }.activity-item>i { width:8px; height:8px; margin-top:5px; border-radius:50%; background:#8a9baa; }.activity-item>i.MAPPING { background:#db9a39 }.activity-item>i.DDL { background:#a96a72 }.activity-item>i.BLUEPRINT { background:#468bc4 }.activity-item strong,.activity-item span { display:block; }.activity-item strong { color:#34516c; font-size:13px; }.activity-item span,.activity-item time { color:#8494a4; font-size:11px; }.activity-item span { margin-top:3px; }.activity-item time { white-space:nowrap; }
@media (max-width:1200px) { .metric-grid { grid-template-columns:repeat(3,1fr); }.stage-list { grid-template-columns:repeat(2,1fr); } } @media (max-width:760px) { .hero,.content-grid { grid-template-columns:1fr; flex-direction:column; }.metric-grid { grid-template-columns:repeat(2,1fr); }.stage-list { grid-template-columns:1fr; } }
</style>
