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
            <el-input-number v-model="form.sample_limit" :min="1" :max="1000" />
            <span class="sample-limit-hint">默认 100 条，可在 1 - 1000 条之间调整，仅用于 Agent 的受限只读数据检索</span>
          </el-form-item>
          <el-alert title="无需手工选择数据对象：启动后 Agent 会依据 Skill 和数据源自动选择对象。请在右侧对话框中自由提问。" type="info" :closable="false" class="object-selection-hint" />
        </el-form>

        <div class="test-actions">
          <el-button @click="loadAll">刷新</el-button>
          <el-button type="primary" :loading="dialogInitializing" @click="openTestDialog">执行测试</el-button>
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
            <div v-if="selectedSkillPlanningStats.actionable_turns" class="skill-section">
              <div class="section-title">模板命中统计</div>
              <div class="skill-stats-grid">
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中轮次</div>
                  <div class="skill-stat-value">{{ selectedSkillPlanningStats.reference_pattern_turns }}/{{ selectedSkillPlanningStats.actionable_turns }}</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中率</div>
                  <div class="skill-stat-value">{{ selectedSkillPlanningStats.reference_pattern_hit_rate }}%</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">节省 Planner</div>
                  <div class="skill-stat-value">{{ selectedSkillPlanningStats.llm_planner_saved_count }}</div>
                </div>
              </div>
            </div>
            <div v-if="selectedSkillPlanningStats.top_reference_patterns.length" class="skill-section">
              <div class="section-title">高频模板</div>
              <div class="skill-pattern-list">
                <div v-for="item in selectedSkillPlanningStats.top_reference_patterns" :key="item.reference_pattern_id" class="skill-pattern-item">
                  <span>{{ item.reference_pattern_id }}</span>
                  <el-tag size="small" type="primary" effect="plain">{{ item.count }}</el-tag>
                </div>
              </div>
            </div>
            <div v-if="selectedSourcePlanningStats.actionable_turns" class="skill-section">
              <div class="section-title">当前数据源命中统计</div>
              <div class="skill-stats-grid">
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中轮次</div>
                  <div class="skill-stat-value">{{ selectedSourcePlanningStats.reference_pattern_turns }}/{{ selectedSourcePlanningStats.actionable_turns }}</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中率</div>
                  <div class="skill-stat-value">{{ selectedSourcePlanningStats.reference_pattern_hit_rate }}%</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">节省 Planner</div>
                  <div class="skill-stat-value">{{ selectedSourcePlanningStats.llm_planner_saved_count }}</div>
                </div>
              </div>
            </div>
            <div v-if="selectedDomainPlanningStats.actionable_turns" class="skill-section">
              <div class="section-title">当前分析域命中统计</div>
              <div class="skill-stats-grid">
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中轮次</div>
                  <div class="skill-stat-value">{{ selectedDomainPlanningStats.reference_pattern_turns }}/{{ selectedDomainPlanningStats.actionable_turns }}</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">命中率</div>
                  <div class="skill-stat-value">{{ selectedDomainPlanningStats.reference_pattern_hit_rate }}%</div>
                </div>
                <div class="skill-stat-box">
                  <div class="skill-stat-label">节省 Planner</div>
                  <div class="skill-stat-value">{{ selectedDomainPlanningStats.llm_planner_saved_count }}</div>
                </div>
              </div>
            </div>
          </div>
          <el-empty v-else description="选择上传 Skill 后显示详情" :image-size="72" />
        </el-card>

        <el-card class="test-card" shadow="never">
          <template #header><span>测试历史</span></template>
          <div v-if="testSessions.length" class="planning-stats-strip">
            <div class="planning-stat-card">
              <div class="planning-stat-label">累计问答轮次</div>
              <div class="planning-stat-value">{{ aggregatePlanningStats.total_turns }}</div>
            </div>
            <div class="planning-stat-card">
              <div class="planning-stat-label">模板命中轮次</div>
              <div class="planning-stat-value">{{ aggregatePlanningStats.reference_pattern_turns }}</div>
            </div>
            <div class="planning-stat-card">
              <div class="planning-stat-label">命中率</div>
              <div class="planning-stat-value">{{ aggregatePlanningStats.reference_pattern_hit_rate }}%</div>
            </div>
            <div class="planning-stat-card">
              <div class="planning-stat-label">节省 Planner 次数</div>
              <div class="planning-stat-value">{{ aggregatePlanningStats.llm_planner_saved_count }}</div>
            </div>
          </div>
          <el-table v-if="testSessions.length" :data="testSessions" size="small" class="history-table" @row-click="openTestHistory">
            <el-table-column prop="skill_name" label="Skill" min-width="150" show-overflow-tooltip />
            <el-table-column prop="last_question" label="最近问题" min-width="180" show-overflow-tooltip />
            <el-table-column prop="message_count" label="消息" width="64" align="center" />
            <el-table-column label="模板命中" min-width="120">
              <template #default="{ row }">
                <span>{{ normalizePlanningStats(row).reference_pattern_turns }}/{{ normalizePlanningStats(row).actionable_turns }}</span>
              </template>
            </el-table-column>
            <el-table-column label="测试时间" min-width="145">
              <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="72" fixed="right">
              <template #default="{ row }"><el-link type="primary" @click.stop="openTestHistory(row)">查看</el-link></template>
            </el-table-column>
          </el-table>
          <div v-if="groupedPlanningStatsBySkill.length" class="skill-group-summary">
            <div class="section-title">按 Skill 模板命中</div>
            <el-table :data="groupedPlanningStatsBySkill" size="small" border stripe>
              <el-table-column prop="skill_name" label="Skill" min-width="150" show-overflow-tooltip />
              <el-table-column prop="session_count" label="会话" width="72" align="center" />
              <el-table-column label="命中轮次" min-width="110">
                <template #default="{ row }">{{ row.reference_pattern_turns }}/{{ row.actionable_turns }}</template>
              </el-table-column>
              <el-table-column prop="reference_pattern_hit_rate" label="命中率%" width="96" align="center" />
              <el-table-column prop="llm_planner_saved_count" label="节省 Planner" width="110" align="center" />
              <el-table-column label="高频模板" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ row.top_reference_patterns.map((item: any) => `${item.reference_pattern_id}(${item.count})`).join(' / ') || '-' }}</template>
              </el-table-column>
            </el-table>
          </div>
          <div v-if="groupedPlanningStatsBySource.length" class="skill-group-summary">
            <div class="section-title">按数据源模板命中</div>
            <el-table :data="groupedPlanningStatsBySource" size="small" border stripe>
              <el-table-column prop="source_name" label="数据源" min-width="160" show-overflow-tooltip />
              <el-table-column prop="session_count" label="会话" width="72" align="center" />
              <el-table-column label="命中轮次" min-width="110">
                <template #default="{ row }">{{ row.reference_pattern_turns }}/{{ row.actionable_turns }}</template>
              </el-table-column>
              <el-table-column prop="reference_pattern_hit_rate" label="命中率%" width="96" align="center" />
              <el-table-column prop="llm_planner_saved_count" label="节省 Planner" width="110" align="center" />
              <el-table-column label="高频模板" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ row.top_reference_patterns.map((item: any) => `${item.reference_pattern_id}(${item.count})`).join(' / ') || '-' }}</template>
              </el-table-column>
            </el-table>
          </div>
          <div v-if="groupedPlanningStatsByDomain.length" class="skill-group-summary">
            <div class="section-title">按分析域模板命中</div>
            <el-table :data="groupedPlanningStatsByDomain" size="small" border stripe>
              <el-table-column prop="domain_name" label="分析域" min-width="160" show-overflow-tooltip />
              <el-table-column prop="session_count" label="会话" width="72" align="center" />
              <el-table-column label="命中轮次" min-width="110">
                <template #default="{ row }">{{ row.reference_pattern_turns }}/{{ row.actionable_turns }}</template>
              </el-table-column>
              <el-table-column prop="reference_pattern_hit_rate" label="命中率%" width="96" align="center" />
              <el-table-column prop="llm_planner_saved_count" label="节省 Planner" width="110" align="center" />
              <el-table-column label="高频模板" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ row.top_reference_patterns.map((item: any) => `${item.reference_pattern_id}(${item.count})`).join(' / ') || '-' }}</template>
              </el-table-column>
            </el-table>
          </div>
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
            <template v-if="message.role === 'user' && !result.pending">
              <div v-if="getTurnResultForUserMessage(index)?.plan?.intent_type" class="plan-summary-card">
                <div class="data-answer-title">执行计划 · {{ getTurnResultForUserMessage(index).plan.intent_type }}</div>
                <div class="plan-summary-text">
                  模式：{{ getTurnResultForUserMessage(index).plan.planning_mode || 'LLM_PLAN' }}{{ getTurnResultForUserMessage(index).plan.reference_pattern_id ? ` / ${getTurnResultForUserMessage(index).plan.reference_pattern_id}` : '' }}；
                  对象：{{ (getTurnResultForUserMessage(index).plan.selected_objects || []).join(' / ') || '-' }}；
                  步骤：{{ (getTurnResultForUserMessage(index).plan.steps || []).length }}
                </div>
              </div>
              <div v-for="table in getEvidenceTablesForUserMessage(index)" :key="table.key || table.title" class="data-answer-card">
                <div class="data-answer-title">{{ table.title || '问数结果' }} · {{ table.row_count ?? table.sample_rows?.length ?? 0 }} 条</div>
                <el-table :data="table.sample_rows || []" border stripe size="small" max-height="280">
                  <el-table-column v-for="column in (table.columns || []).slice(0, 12)" :key="column.column_name" :prop="column.column_name" :label="column.column_name" min-width="130" show-overflow-tooltip />
                </el-table>
              </div>
              <div v-if="getAnalysisResultForUserMessage(index)" class="analysis-summary-card">
                <div class="data-answer-title">结构化分析摘要</div>
                <div v-if="getCompletionAssessmentForUserMessage(index).status !== 'PENDING'" class="completion-summary-line">
                  <span>任务判定：</span>
                  <el-tag size="small" :type="completionStatusTagType(getCompletionAssessmentForUserMessage(index).status)">
                    {{ getCompletionAssessmentForUserMessage(index).status }}
                  </el-tag>
                  <span class="completion-summary-text">{{ getCompletionAssessmentForUserMessage(index).summary }}</span>
                </div>
                <div v-if="getAnalysisResultForUserMessage(index).analysis_flags.length" class="analysis-flag-list">
                  <el-tag
                    v-for="flag in getAnalysisResultForUserMessage(index).analysis_flags"
                    :key="flag"
                    size="small"
                    effect="plain"
                    type="info"
                  >
                    {{ flag }}
                  </el-tag>
                </div>
                <div v-if="getAnalysisResultForUserMessage(index).top_findings.length" class="analysis-finding-list">
                  <div v-for="(finding, findingIndex) in getAnalysisResultForUserMessage(index).top_findings" :key="`finding_${findingIndex}`" class="analysis-finding-item">
                    {{ finding }}
                  </div>
                </div>
                <div v-if="getAnalysisResultForUserMessage(index).trend_summaries.length" class="trend-card-list">
                  <div v-for="(trend, trendIndex) in getAnalysisResultForUserMessage(index).trend_summaries" :key="`trend_${trendIndex}`" class="trend-card">
                    <div class="trend-card-title">
                      {{ formatTrendGroupLabel(trend.group) }}
                      <el-tag size="small" :type="trend.direction === 'RISING' ? 'success' : trend.direction === 'FALLING' ? 'danger' : 'info'">
                        {{ trend.direction || '-' }}
                      </el-tag>
                    </div>
                    <div class="trend-card-meta">
                      峰值 {{ trend.peak_bucket || '-' }} / {{ formatMetricValue(trend.peak_value) }}
                    </div>
                    <div class="trend-card-meta">
                      谷值 {{ trend.trough_bucket || '-' }} / {{ formatMetricValue(trend.trough_value) }}
                    </div>
                    <div class="trend-card-meta" v-if="trend.comparison_type && trend.period_change_rate !== null && trend.period_change_rate !== undefined">
                      最新 {{ trend.comparison_type }} 变化率 {{ formatMetricValue(trend.period_change_rate) }}%
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <div v-if="message.role === 'user' && index === latestUserMessageIndex && result.pending" class="agent-pending">
              <el-icon class="is-loading"><Loading /></el-icon>
              <div class="agent-pending-content">
                <div>Agent 正在依据 Skill 执行 Oracle Graph 查询并分析数据…</div>
                <div v-if="liveExecutionSteps.length" class="pending-step-list">
                  <span
                    v-for="(item, stepIndex) in liveExecutionSteps"
                    :key="`${item.step_id || item.title}_${stepIndex}`"
                    class="pending-step-chip"
                    :class="item.event_type === 'REFERENCE_PATTERN_MATCHED' ? 'reference' : item.status === 'WARNING' ? 'warning' : item.status === 'SUCCESS' ? 'success' : 'running'"
                  >
                    {{ item.title || item.event_type }}{{ item.runtime_state ? ` · ${item.runtime_state}` : '' }}
                  </span>
                </div>
                <div v-if="currentLiveStep" class="pending-step-detail">
                  当前步骤：{{ currentLiveStep.title || currentLiveStep.event_type }}{{ currentLiveStep.detail ? ` · ${currentLiveStep.detail}` : '' }}
                </div>
              </div>
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
          <div class="execution-model" v-if="result.plan?.intent_type">分析意图：{{ result.plan.intent_type }}</div>
          <div class="execution-model" v-if="result.plan?.planning_mode">计划模式：{{ result.plan.planning_mode }}<span v-if="result.plan?.reference_pattern_id"> / {{ result.plan.reference_pattern_id }}</span></div>
          <div class="execution-model" v-if="result.plan?.selected_objects?.length">涉及对象：{{ result.plan.selected_objects.join(' / ') }}</div>
          <div v-if="normalizedPlanningStats.actionable_turns" class="planning-inline-summary">
            模板命中 {{ normalizedPlanningStats.reference_pattern_turns }}/{{ normalizedPlanningStats.actionable_turns }} 轮，
            命中率 {{ normalizedPlanningStats.reference_pattern_hit_rate }}%，累计节省 LLM planner {{ normalizedPlanningStats.llm_planner_saved_count }} 次。
          </div>
        </div>
        <div class="result-block" v-if="result.plan">
          <div class="section-title">受控执行计划</div>
          <pre class="code-box">{{ JSON.stringify(result.plan, null, 2) }}</pre>
        </div>
        <div class="result-block">
          <div class="section-title">事件流回放</div>
          <div v-if="eventStreamLoading" class="stream-loading">正在回放当前测试会话的执行事件…</div>
          <div v-else-if="streamedExecutionEvents.length" class="stream-event-list">
            <div
              v-for="(item, index) in streamedExecutionEvents"
              :key="`${item.step_id || item.title || 'event'}_${index}`"
              class="stream-event-card"
              :class="{ reference: item.event_type === 'REFERENCE_PATTERN_MATCHED' }"
            >
              <div class="stream-event-title">
                <strong>{{ item.title || item.event_type || `事件 ${index + 1}` }}</strong>
                <el-tag size="small" :type="item.event_type === 'REFERENCE_PATTERN_MATCHED' ? 'primary' : item.status === 'WARNING' ? 'warning' : 'success'">{{ item.status || 'SUCCESS' }}</el-tag>
              </div>
              <div class="stream-event-meta">{{ item.event_type || 'EXECUTION_EVENT' }}<span v-if="item.runtime_state"> · {{ item.runtime_state }}</span></div>
              <div class="trace-action">{{ item.detail || '-' }}</div>
            </div>
          </div>
          <el-empty v-else description="当前会话暂无可回放的执行事件" :image-size="64" />
        </div>
        <div class="result-block">
          <div class="section-title">任务完成判定</div>
          <div class="completion-card">
            <div class="completion-card-header">
              <span>当前状态</span>
              <el-tag size="small" :type="completionStatusTagType(normalizedCompletionAssessment.status)">
                {{ normalizedCompletionAssessment.status }}
              </el-tag>
            </div>
            <div class="completion-card-summary">{{ normalizedCompletionAssessment.summary }}</div>
            <div class="completion-check-grid">
              <div class="completion-check-item">
                <div class="completion-check-title">Deterministic</div>
                <div class="completion-check-status">{{ normalizedCompletionAssessment.deterministic.status }}</div>
                <div class="completion-check-desc">{{ normalizedCompletionAssessment.deterministic.reason }}</div>
              </div>
              <div class="completion-check-item">
                <div class="completion-check-title">Coverage</div>
                <div class="completion-check-status">{{ normalizedCompletionAssessment.coverage_check.status }}</div>
                <div class="completion-check-desc">{{ normalizedCompletionAssessment.coverage_check.reason }}</div>
                <div v-if="normalizedCompletionAssessment.coverage_check.missing_fields.length" class="completion-check-extra">
                  缺少字段：{{ normalizedCompletionAssessment.coverage_check.missing_fields.join(' / ') }}
                </div>
                <div v-if="normalizedCompletionAssessment.coverage_check.missing_relations.length" class="completion-check-extra">
                  缺少关系：{{ normalizedCompletionAssessment.coverage_check.missing_relations.join(' / ') }}
                </div>
              </div>
              <div class="completion-check-item">
                <div class="completion-check-title">Judge</div>
                <div class="completion-check-status">{{ normalizedCompletionAssessment.judge.status }}</div>
                <div class="completion-check-desc">{{ normalizedCompletionAssessment.judge.reason }}</div>
                <div v-if="normalizedCompletionAssessment.judge.missing_components.length" class="completion-check-extra">
                  缺少组件：{{ normalizedCompletionAssessment.judge.missing_components.join(' / ') }}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="result-block">
          <div class="section-title">结构化分析判定</div>
          <div v-if="normalizedAnalysisResult.analysis_flags.length" class="analysis-flag-list">
            <el-tag
              v-for="flag in normalizedAnalysisResult.analysis_flags"
              :key="flag"
              size="small"
              effect="plain"
              type="info"
            >
              {{ flag }}
            </el-tag>
          </div>
          <div v-if="normalizedAnalysisResult.top_findings.length" class="analysis-finding-list">
            <div v-for="(finding, index) in normalizedAnalysisResult.top_findings" :key="`dialog_finding_${index}`" class="analysis-finding-item">
              {{ finding }}
            </div>
          </div>
          <div v-if="normalizedAnalysisResult.period_comparisons.length" class="comparison-table-wrap">
            <el-table :data="normalizedAnalysisResult.period_comparisons" border stripe size="small" max-height="280">
              <el-table-column label="分组" min-width="180">
                <template #default="{ row }">{{ formatTrendGroupLabel(row.group) }}</template>
              </el-table-column>
              <el-table-column prop="comparison_type" label="比较类型" min-width="110" />
              <el-table-column prop="previous_bucket" label="上一周期" min-width="120" />
              <el-table-column prop="latest_bucket" label="当前周期" min-width="120" />
              <el-table-column label="变化值" min-width="110">
                <template #default="{ row }">{{ formatMetricValue(row.period_change_value) }}</template>
              </el-table-column>
              <el-table-column label="变化率" min-width="110">
                <template #default="{ row }">
                  {{ row.period_change_rate === null || row.period_change_rate === undefined ? '-' : `${formatMetricValue(row.period_change_rate)}%` }}
                </template>
              </el-table-column>
              <el-table-column prop="max_rise_bucket" label="最大上涨桶" min-width="120" />
              <el-table-column prop="max_drop_bucket" label="最大下滑桶" min-width="120" />
            </el-table>
          </div>
          <el-empty
            v-else-if="!normalizedAnalysisResult.analysis_flags.length && !normalizedAnalysisResult.top_findings.length"
            description="当前结果暂无结构化分析判定"
            :image-size="64"
          />
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
          <div class="section-title">本次执行的证据表与 Oracle Graph SQL</div>
          <div v-for="table in normalizedEvidenceTables" :key="table.key || table.title" class="query-item">
            <div>{{ table.title }}（返回 {{ table.row_count ?? table.sample_rows?.length ?? 0 }} 行）</div>
            <el-table :data="table.sample_rows || []" border stripe size="small" max-height="260">
              <el-table-column v-for="column in (table.columns || []).slice(0, 10)" :key="column.column_name" :prop="column.column_name" :label="column.column_name" min-width="120" show-overflow-tooltip />
            </el-table>
          </div>
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
const dialogInitializing = ref(false)
const eventStreamLoading = ref(false)
const dialogVisible = ref(false)
const dialogFullscreen = ref(false)
const processDialogVisible = ref(false)
const historyView = ref(false)
const currentSessionId = ref('')
const historySession = ref<any>(null)
const currentDomainId = ref(appStore.currentDomainId || '')
const streamedExecutionEvents = ref<any[]>([])
let eventStreamAbortController: AbortController | null = null

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

const normalizeEvidenceTables = (payload: any) => {
  const evidenceTables = payload?.evidence_tables
  if (Array.isArray(evidenceTables) && evidenceTables.length) {
    return evidenceTables.map((item: any, index: number) => ({
      key: item?.key || `evidence_${index}`,
      title: item?.title || `证据表 ${index + 1}`,
      row_count: item?.row_count ?? item?.sample_rows?.length ?? 0,
      columns: Array.isArray(item?.columns) ? item.columns : [],
      sample_rows: Array.isArray(item?.sample_rows) ? item.sample_rows : []
    }))
  }
  const preview = payload?.table_preview
  if (preview?.sample_rows?.length) {
    return [{
      key: 'legacy_table_preview',
      title: '问数结果',
      row_count: preview.sample_rows.length,
      columns: preview.columns || [],
      sample_rows: preview.sample_rows || []
    }]
  }
  return []
}

const normalizeAnalysisResult = (payload: any) => {
  const analysisResult = payload?.analysis_result
  return {
    analysis_flags: Array.isArray(analysisResult?.analysis_flags) ? analysisResult.analysis_flags : [],
    top_findings: Array.isArray(analysisResult?.top_findings) ? analysisResult.top_findings : [],
    trend_summaries: Array.isArray(analysisResult?.trend_summaries) ? analysisResult.trend_summaries : [],
    period_comparisons: Array.isArray(analysisResult?.period_comparisons) ? analysisResult.period_comparisons : []
  }
}

const normalizeCompletionAssessment = (payload: any) => {
  const completionAssessment = payload?.completion_assessment
  return {
    status: completionAssessment?.status || 'PENDING',
    completed: Boolean(completionAssessment?.completed),
    summary: completionAssessment?.summary || '当前轮尚未生成任务完成判定。',
    deterministic: {
      status: completionAssessment?.deterministic?.status || 'SKIPPED',
      reason: completionAssessment?.deterministic?.reason || '-'
    },
    coverage_check: {
      status: completionAssessment?.coverage_check?.status || 'SKIPPED',
      reason: completionAssessment?.coverage_check?.reason || '-',
      missing_fields: Array.isArray(completionAssessment?.coverage_check?.missing_fields) ? completionAssessment.coverage_check.missing_fields : [],
      missing_relations: Array.isArray(completionAssessment?.coverage_check?.missing_relations) ? completionAssessment.coverage_check.missing_relations : []
    },
    judge: {
      status: completionAssessment?.judge?.status || 'SKIPPED',
      reason: completionAssessment?.judge?.reason || '-',
      missing_components: Array.isArray(completionAssessment?.judge?.missing_components) ? completionAssessment.judge.missing_components : []
    }
  }
}

const normalizePlanningStats = (payload: any) => {
  const stats = payload?.planning_stats
  return {
    total_turns: Number(stats?.total_turns || 0),
    actionable_turns: Number(stats?.actionable_turns || 0),
    reference_pattern_turns: Number(stats?.reference_pattern_turns || 0),
    llm_plan_turns: Number(stats?.llm_plan_turns || 0),
    clarification_turns: Number(stats?.clarification_turns || 0),
    session_ready_turns: Number(stats?.session_ready_turns || 0),
    reference_pattern_hit_rate: Number(stats?.reference_pattern_hit_rate || 0),
    llm_planner_saved_count: Number(stats?.llm_planner_saved_count || 0),
    latest_planning_mode: stats?.latest_planning_mode || '',
    latest_reference_pattern_id: stats?.latest_reference_pattern_id || '',
    top_reference_patterns: Array.isArray(stats?.top_reference_patterns) ? stats.top_reference_patterns : []
  }
}

const buildGroupedPlanningStats = (items: any[]) => {
  const totals = (items || []).map(item => normalizePlanningStats(item)).reduce((acc, item) => {
    acc.total_turns += item.total_turns
    acc.actionable_turns += item.actionable_turns
    acc.reference_pattern_turns += item.reference_pattern_turns
    acc.llm_plan_turns += item.llm_plan_turns
    acc.clarification_turns += item.clarification_turns
    acc.session_ready_turns += item.session_ready_turns
    acc.llm_planner_saved_count += item.llm_planner_saved_count
    for (const pattern of item.top_reference_patterns || []) {
      const patternId = String(pattern?.reference_pattern_id || '')
      const count = Number(pattern?.count || 0)
      if (!patternId || count <= 0) continue
      acc.patternCounter.set(patternId, (acc.patternCounter.get(patternId) || 0) + count)
    }
    return acc
  }, {
    total_turns: 0,
    actionable_turns: 0,
    reference_pattern_turns: 0,
    llm_plan_turns: 0,
    clarification_turns: 0,
    session_ready_turns: 0,
    llm_planner_saved_count: 0,
    patternCounter: new Map<string, number>()
  })
  const reference_pattern_hit_rate = totals.actionable_turns
    ? Number(((totals.reference_pattern_turns / totals.actionable_turns) * 100).toFixed(2))
    : 0
  return {
    total_turns: totals.total_turns,
    actionable_turns: totals.actionable_turns,
    reference_pattern_turns: totals.reference_pattern_turns,
    llm_plan_turns: totals.llm_plan_turns,
    clarification_turns: totals.clarification_turns,
    session_ready_turns: totals.session_ready_turns,
    llm_planner_saved_count: totals.llm_planner_saved_count,
    reference_pattern_hit_rate,
    top_reference_patterns: Array.from(totals.patternCounter.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 3)
      .map(([reference_pattern_id, count]) => ({ reference_pattern_id, count }))
  }
}

const getEvidenceTablesForUserMessage = (messageIndex: number | string) => normalizeEvidenceTables(getTurnResultForUserMessage(messageIndex))
const normalizedEvidenceTables = computed(() => normalizeEvidenceTables(result.value))
const getAnalysisResultForUserMessage = (messageIndex: number | string) => normalizeAnalysisResult(getTurnResultForUserMessage(messageIndex))
const normalizedAnalysisResult = computed(() => normalizeAnalysisResult(result.value))
const getCompletionAssessmentForUserMessage = (messageIndex: number | string) => normalizeCompletionAssessment(getTurnResultForUserMessage(messageIndex))
const normalizedCompletionAssessment = computed(() => normalizeCompletionAssessment(result.value))
const normalizedPlanningStats = computed(() => normalizePlanningStats(result.value))
const selectedSkillPlanningStats = computed(() => {
  if (!form.value.managed_skill_id) {
    return buildGroupedPlanningStats([])
  }
  return buildGroupedPlanningStats(
    (testSessions.value || []).filter(item => item.managed_skill_id === form.value.managed_skill_id)
  )
})
const selectedSourcePlanningStats = computed(() => {
  if (!form.value.source_id) {
    return buildGroupedPlanningStats([])
  }
  return buildGroupedPlanningStats(
    (testSessions.value || []).filter(item => item.source_id === form.value.source_id)
  )
})
const selectedDomainPlanningStats = computed(() => {
  if (!currentDomainId.value) {
    return buildGroupedPlanningStats([])
  }
  return buildGroupedPlanningStats(
    (testSessions.value || []).filter(item => item.domain_id === currentDomainId.value)
  )
})
const aggregatePlanningStats = computed(() => {
  return buildGroupedPlanningStats(testSessions.value || [])
})
const groupedPlanningStatsBySkill = computed(() => {
  const grouped = new Map<string, any[]>()
  for (const item of testSessions.value || []) {
    const key = String(item?.managed_skill_id || item?.skill_name || '')
    if (!key) continue
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key)?.push(item)
  }
  return Array.from(grouped.entries()).map(([managed_skill_id, items]) => ({
    managed_skill_id,
    skill_name: String(items[0]?.skill_name || managed_skill_id),
    session_count: items.length,
    ...buildGroupedPlanningStats(items)
  })).sort((a, b) => {
    if (b.reference_pattern_hit_rate !== a.reference_pattern_hit_rate) {
      return b.reference_pattern_hit_rate - a.reference_pattern_hit_rate
    }
    if (b.reference_pattern_turns !== a.reference_pattern_turns) {
      return b.reference_pattern_turns - a.reference_pattern_turns
    }
    return a.skill_name.localeCompare(b.skill_name)
  })
})
const groupedPlanningStatsBySource = computed(() => {
  const grouped = new Map<string, any[]>()
  for (const item of testSessions.value || []) {
    const key = String(item?.source_id || item?.source_name || '')
    if (!key) continue
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key)?.push(item)
  }
  return Array.from(grouped.entries()).map(([source_id, items]) => ({
    source_id,
    source_name: String(items[0]?.source_name || source_id),
    session_count: items.length,
    ...buildGroupedPlanningStats(items)
  })).sort((a, b) => {
    if (b.reference_pattern_hit_rate !== a.reference_pattern_hit_rate) {
      return b.reference_pattern_hit_rate - a.reference_pattern_hit_rate
    }
    if (b.reference_pattern_turns !== a.reference_pattern_turns) {
      return b.reference_pattern_turns - a.reference_pattern_turns
    }
    return a.source_name.localeCompare(b.source_name)
  })
})
const groupedPlanningStatsByDomain = computed(() => {
  const grouped = new Map<string, any[]>()
  for (const item of testSessions.value || []) {
    const key = String(item?.domain_id || item?.domain_name || '')
    if (!key) continue
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key)?.push(item)
  }
  return Array.from(grouped.entries()).map(([domain_id, items]) => ({
    domain_id,
    domain_name: String(items[0]?.domain_name || domain_id),
    session_count: items.length,
    ...buildGroupedPlanningStats(items)
  })).sort((a, b) => {
    if (b.reference_pattern_hit_rate !== a.reference_pattern_hit_rate) {
      return b.reference_pattern_hit_rate - a.reference_pattern_hit_rate
    }
    if (b.reference_pattern_turns !== a.reference_pattern_turns) {
      return b.reference_pattern_turns - a.reference_pattern_turns
    }
    return a.domain_name.localeCompare(b.domain_name)
  })
})
const liveExecutionSteps = computed(() => streamedExecutionEvents.value.slice(-4))
const currentLiveStep = computed(() => streamedExecutionEvents.value[streamedExecutionEvents.value.length - 1] || null)
const latestTurnNo = computed(() => {
  const turnResults = result.value?.turn_results
  if (!Array.isArray(turnResults) || !turnResults.length) return null
  return turnResults[turnResults.length - 1]?.turn_no ?? null
})

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

const startSession = async () => {
  const res = await agentApi.startManagedSkillTestSession(form.value.managed_skill_id, {
    domain_id: currentDomainId.value,
    llm_config_id: form.value.llm_config_id,
    source_id: form.value.source_id,
    schema: form.value.schema || null,
    sample_limit: form.value.sample_limit,
    start_session: true
  })
  result.value = res.data
  currentSessionId.value = res.data?.session_id || ''
  void replayExecutionEvents()
}

const stopReplayExecutionEvents = () => {
  eventStreamAbortController?.abort()
  eventStreamAbortController = null
}

const replayExecutionEvents = async (turnNo?: number | null) => {
  if (!currentSessionId.value) return
  stopReplayExecutionEvents()
  streamedExecutionEvents.value = []
  eventStreamLoading.value = true
  const controller = new AbortController()
  eventStreamAbortController = controller
  try {
    await agentApi.streamManagedSkillTestEvents(currentSessionId.value, {
      turnNo: turnNo ?? latestTurnNo.value ?? undefined,
      delayMs: 24,
      signal: controller.signal,
      onEvent: (eventName, payload) => {
        if (eventName === 'start') {
          streamedExecutionEvents.value = []
          return
        }
        if (eventName === 'execution_event' && payload?.event) {
          streamedExecutionEvents.value = [...streamedExecutionEvents.value, payload.event]
          return
        }
        if (eventName === 'complete') {
          eventStreamLoading.value = false
        }
      }
    })
  } catch (error: any) {
    if (error?.name !== 'AbortError') {
      streamedExecutionEvents.value = result.value?.execution_events || []
    }
  } finally {
    if (eventStreamAbortController === controller) {
      eventStreamLoading.value = false
      eventStreamAbortController = null
    }
  }
}

const openTestDialog = async () => {
  if (!form.value.managed_skill_id) { ElMessage.warning('请选择技能管理中上传的 Skill'); return }
  if (!form.value.llm_config_id) { ElMessage.warning('请选择测试模型'); return }
  if (!form.value.source_id) { ElMessage.warning('请选择对象数据库'); return }
  dialogInitializing.value = true
  result.value = null
  chatInput.value = ''
  currentSessionId.value = ''
  historySession.value = null
  historyView.value = false
  dialogFullscreen.value = false
  dialogVisible.value = true
  try {
    await startSession()
  } catch (e) {
    dialogVisible.value = false
  } finally {
    dialogInitializing.value = false
  }
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
    void replayExecutionEvents()
  } catch (e) {}
}

const sendMessage = async () => {
  const question = chatInput.value.trim()
  if (!question) return
  if (!currentSessionId.value) {
    try {
      await startSession()
    } catch (e) {
      return
    }
  }
  const previousResult = result.value
  const previousConversation = previousResult?.conversation || []
  testing.value = true
  stopReplayExecutionEvents()
  streamedExecutionEvents.value = []
  eventStreamLoading.value = true
  chatInput.value = ''
  result.value = {
    ...(previousResult || {}),
    conversation: [...previousConversation, { role: 'user', content: question }],
    pending: true,
    execution_events: []
  }
  const controller = new AbortController()
  eventStreamAbortController = controller
  try {
    await agentApi.streamManagedSkillTestTurn(form.value.managed_skill_id, {
      domain_id: currentDomainId.value,
      llm_config_id: form.value.llm_config_id,
      source_id: form.value.source_id,
      schema: form.value.schema || null,
      test_question: question,
      sample_limit: form.value.sample_limit,
      session_id: currentSessionId.value || null,
      conversation_history: previousConversation
    }, {
      signal: controller.signal,
      onEvent: (eventName, payload) => {
        if (eventName === 'execution_event' && payload?.event) {
          streamedExecutionEvents.value = [...streamedExecutionEvents.value, payload.event]
          result.value = {
            ...(result.value || {}),
            execution_events: [...streamedExecutionEvents.value]
          }
          return
        }
        if (eventName === 'turn_result') {
          result.value = payload
          currentSessionId.value = payload?.session_id || currentSessionId.value
          return
        }
        if (eventName === 'error') {
          throw new Error(payload?.message || '流式测试失败')
        }
        if (eventName === 'complete') {
          eventStreamLoading.value = false
        }
      }
    })
    await loadTestSessions()
  } catch (e) {
    result.value = previousResult
    chatInput.value = question
  } finally {
    testing.value = false
    eventStreamLoading.value = false
    if (eventStreamAbortController === controller) {
      eventStreamAbortController = null
    }
  }
}

const formatModelOption = (item: any) => `${item.config_name} / ${item.model_name}`
const formatDateTime = (value: string) => value ? String(value).replace('T', ' ').slice(0, 16) : '-'
const completionStatusTagType = (status: string) => {
  if (status === 'COMPLETED') return 'success'
  if (status === 'PARTIAL') return 'warning'
  if (status === 'FAILED') return 'danger'
  return 'info'
}
const formatMetricValue = (value: any) => {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  if (!Number.isNaN(numeric)) {
    return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(4).replace(/\.?0+$/, '')
  }
  return String(value)
}
const formatTrendGroupLabel = (group: any) => {
  if (!group || typeof group !== 'object' || !Object.keys(group).length) return '整体'
  return Object.entries(group).map(([key, value]) => `${key}: ${value}`).join(' · ')
}

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

watch(dialogVisible, (visible) => {
  if (!visible) {
    stopReplayExecutionEvents()
    eventStreamLoading.value = false
  }
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
.planning-stats-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.planning-stat-card {
  padding: 12px;
  border-radius: 12px;
  background: linear-gradient(135deg, #f7f9ff 0%, #eef3ff 100%);
  border: 1px solid #d8e2fb;
}
.planning-stat-label {
  color: #58708d;
  font-size: 12px;
}
.planning-stat-value {
  margin-top: 8px;
  color: #254a86;
  font-size: 22px;
  font-weight: 700;
}
.result-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.history-table { width: 100%; cursor: pointer; }
.skill-group-summary { margin-top: 14px; }
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
.skill-stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.skill-stat-box {
  padding: 10px 12px;
  border-radius: 10px;
  background: #f7f9ff;
  border: 1px solid #d8e2fb;
}
.skill-stat-label {
  color: #627891;
  font-size: 12px;
}
.skill-stat-value {
  margin-top: 6px;
  color: #234b88;
  font-size: 18px;
  font-weight: 700;
}
.skill-pattern-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-pattern-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: #f9fbfd;
  border: 1px solid #e0e8f0;
  color: #496074;
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
.planning-inline-summary {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f7f9ff;
  border: 1px solid #d8e2fb;
  color: #4b6282;
  line-height: 1.6;
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
.plan-summary-card { align-self: stretch; margin-top: 8px; padding: 10px 12px; border-radius: 12px; background: #fff7ec; border: 1px solid #efd9b0; }
.plan-summary-text { color: #6f5831; line-height: 1.6; font-size: 13px; }
.data-answer-card { align-self: stretch; margin-top: 8px; padding: 12px; border-radius: 12px; background: #f4f9fd; border: 1px solid #d9ebf7; }
.analysis-summary-card { align-self: stretch; margin-top: 8px; padding: 12px; border-radius: 12px; background: #f8fbf6; border: 1px solid #d8ead2; }
.data-answer-title { margin-bottom: 8px; color: #25527c; font-weight: 700; font-size: 13px; }
.completion-summary-line { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; color: #425440; font-size: 13px; }
.completion-summary-text { color: #5f705c; }
.analysis-flag-list { display: flex; flex-wrap: wrap; gap: 8px; }
.analysis-finding-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.analysis-finding-item { padding: 10px 12px; border-radius: 10px; background: #fff; border: 1px solid #e2ead9; color: #425440; line-height: 1.6; }
.trend-card-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-top: 10px; }
.trend-card { padding: 12px; border-radius: 12px; border: 1px solid #d9e4ee; background: #fff; }
.trend-card-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #274661; font-weight: 700; }
.trend-card-meta { margin-top: 8px; color: #607181; font-size: 12px; line-height: 1.6; }
.completion-card { padding: 12px 14px; border-radius: 12px; border: 1px solid #d9e4ee; background: #fbfcfd; }
.completion-card-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #274661; font-weight: 700; }
.completion-card-summary { margin-top: 10px; color: #5d6c79; line-height: 1.7; }
.completion-check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 12px; }
.completion-check-item { padding: 10px 12px; border-radius: 10px; background: #fff; border: 1px solid #e1e8ef; }
.completion-check-title { color: #274661; font-weight: 700; }
.completion-check-status { margin-top: 6px; color: #25527c; font-size: 12px; font-weight: 700; }
.completion-check-desc { margin-top: 6px; color: #6e7d89; font-size: 12px; line-height: 1.6; }
.completion-check-extra { margin-top: 6px; color: #8a5f18; font-size: 12px; line-height: 1.6; }
.agent-pending { display: inline-flex; align-items: center; gap: 8px; margin-top: 8px; padding: 10px 12px; border-radius: 10px; background: #f4f9fd; color: #47708f; font-size: 13px; }
.agent-pending-content { display: flex; flex-direction: column; gap: 8px; }
.pending-step-list { display: flex; flex-wrap: wrap; gap: 8px; }
.pending-step-chip { display: inline-flex; align-items: center; padding: 4px 9px; border-radius: 999px; font-size: 12px; border: 1px solid #d4e1ec; background: #fff; color: #476176; }
.pending-step-chip.running { border-color: #b8d2e8; background: #eef7ff; color: #25527c; }
.pending-step-chip.success { border-color: #cbe8d8; background: #f1fbf5; color: #2c7a55; }
.pending-step-chip.warning { border-color: #ead4a1; background: #fff8e6; color: #8a6418; }
.pending-step-chip.reference { border-color: #cfdaf8; background: #f2f5ff; color: #3157a5; }
.pending-step-detail { color: #5b7286; line-height: 1.5; }
.chat-composer { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.chat-composer .el-button { align-self: flex-end; }
.execution-entry { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 10px; background: #f4f9fd; color: #5c7184; font-size: 12px; }
.sample-limit-hint { margin-left: 10px; color: #8493a1; font-size: 12px; }
.trace-title { display: inline-flex; align-items: center; gap: 6px; }
.stream-loading { padding: 10px 12px; border-radius: 10px; background: #f4f9fd; color: #4e6b87; }
.stream-event-list { display: flex; flex-direction: column; gap: 10px; }
.stream-event-card { padding: 12px 14px; border-radius: 12px; border: 1px solid #dfe7ef; background: #fff; }
.stream-event-card.reference { border-color: #cfdaf8; background: linear-gradient(135deg, #f7f9ff 0%, #eef3ff 100%); }
.stream-event-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #1f3f5d; }
.stream-event-meta { margin-top: 6px; color: #7b8a97; font-size: 12px; }
.comparison-table-wrap { margin-top: 12px; }
.query-item + .query-item { margin-top: 12px; }
.query-item :deep(.el-table) { margin-top: 8px; }
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
  .planning-stats-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .skill-stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
