<template>
  <div class="mapping-operation-page">
    <div class="hero-panel">
      <div>
        <div class="hero-title">数据映射操作</div>
        <div class="hero-desc">
          一次性调用大模型，基于当前分析域下全部本体对象、所选数据源和业务描述，自动设计本体对象的属性来源及 Oracle 26ai 属性图的节点关系结构；边关系 SQL 在后续 DDL 生成阶段统一提供。
        </div>
      </div>
      <div class="hero-stats">
        <div class="stat-card">
          <div class="stat-value">{{ entities.length }}</div>
          <div class="stat-label">本体对象</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ result?.summary?.ready_count || 0 }}</div>
          <div class="stat-label">节点映射</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ result?.summary?.relation_ready_count || 0 }}</div>
          <div class="stat-label">关系设计</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ result?.summary?.applied_total || 0 }}</div>
          <div class="stat-label">已应用条数</div>
        </div>
      </div>
    </div>

    <div class="page-grid">
      <el-card class="config-card" shadow="never">
        <template #header><span>一次性映射配置</span></template>
        <el-form :model="form" label-width="104px">
          <el-form-item label="分析域">
            <el-select v-model="currentDomainId" placeholder="选择业务分析域" @change="handleDomainChange">
              <el-option v-for="item in domains" :key="item.domain_id" :label="item.domain_name" :value="item.domain_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="对象数据库" required>
            <el-select v-model="form.source_id" placeholder="选择映射来源数据源" filterable @change="handleSourceChange">
              <el-option v-for="item in dataSources" :key="item.source_id" :label="item.source_name" :value="item.source_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="Schema" required>
            <el-select v-model="form.schema" placeholder="选择 Schema" filterable>
              <el-option v-for="item in schemaOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="LLM模型" required>
            <el-select v-model="form.model_config_id" placeholder="选择自动映射模型" filterable>
              <el-option v-for="item in llmConfigs" :key="item.config_id" :label="`${item.config_name} / ${item.model_name}`" :value="item.config_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="样例行数">
            <el-input-number v-model="form.sample_limit" :min="1" :max="5" />
          </el-form-item>
          <el-form-item label="业务描述">
            <el-input :model-value="currentDomain?.domain_desc || ''" type="textarea" :rows="4" readonly />
          </el-form-item>
          <el-form-item label="补充提示词">
            <el-input
              v-model="form.mapping_instruction"
              type="textarea"
              :rows="6"
              resize="vertical"
              placeholder="补充映射约束，例如：优先基于产品主表；允许用抽取规则生成派生属性；批次、工单、VCM_ID 视为关键键值。"
            />
          </el-form-item>
        </el-form>
        <div class="config-actions">
          <el-button @click="loadAll">刷新配置</el-button>
          <el-button type="primary" :loading="generating" @click="generateMappings(false)">生成全域映射建议</el-button>
          <el-button type="success" :loading="applying" :disabled="!hasApplicableSuggestions" @click="applyAllMappings">应用全部建议</el-button>
          <el-button type="warning" :loading="applyingSelected" :disabled="!selectedApplicableEntityIds.length" @click="applySelectedMappings">应用选中对象</el-button>
          <el-button type="danger" plain :loading="clearingTasks" @click="clearMappingOperationData">清除操作数据</el-button>
        </div>
        <div class="apply-scope-box">
          <div class="apply-scope-title">应用范围</div>
          <el-segmented
            v-model="applyScope"
            :options="[
              { label: '全部建议', value: 'ALL' },
              { label: '只应用新增', value: 'ADDED' },
              { label: '只应用变更', value: 'CHANGED' }
            ]"
          />
        </div>
        <div class="long-task-tip">
          全域映射是长耗时操作。系统会按分析域下全部本体对象逐个读取业务数据源、构造候选表并调用大模型，可能持续数分钟。
        </div>
        <div v-if="activeTaskDetail" class="active-task-box">
          <div class="active-task-head">
            <div class="active-task-title">后台映射工作</div>
            <el-tag :type="activeTaskStatusTagType(activeTaskDetail.status)" size="small">{{ activeTaskStatusLabel(activeTaskDetail.status) }}</el-tag>
          </div>
          <div class="active-task-meta">
            <span>任务号 {{ activeTaskDetail.task_id }}</span>
            <span>开始于 {{ activeTaskDetail.created_at || '-' }}</span>
          </div>
          <div class="active-task-progress">
            <el-progress
              :percentage="activeTaskProgressPercent"
              :status="activeTaskDetail.status === 'FAILED' ? 'exception' : activeTaskDetail.status === 'SUCCESS' ? 'success' : undefined"
            />
          </div>
          <div class="active-task-grid">
            <div class="active-task-item"><span>总对象</span><strong>{{ activeTaskSummary.entity_count || 0 }}</strong></div>
            <div class="active-task-item"><span>已处理</span><strong>{{ activeTaskSummary.processed_count || 0 }}</strong></div>
            <div class="active-task-item"><span>已生成</span><strong>{{ activeTaskSummary.ready_count || 0 }}</strong></div>
            <div class="active-task-item"><span>失败</span><strong>{{ activeTaskSummary.failed_count || 0 }}</strong></div>
            <div class="active-task-item"><span>关系就绪</span><strong>{{ activeTaskSummary.relation_ready_count || 0 }}</strong></div>
          </div>
          <div class="active-task-current">
            当前对象：{{ activeTaskSummary.current_entity_name || (activeTaskDetail.status === 'IN_PROGRESS' ? '等待调度' : '已结束') }}
          </div>
          <div class="active-task-actions">
            <el-button size="small" @click="refreshActiveTask">刷新状态</el-button>
            <el-button size="small" type="primary" plain :disabled="!isActiveTaskFinished" @click="loadActiveTaskResult">载入结果</el-button>
          </div>
        </div>
      </el-card>

      <div class="result-column">
        <el-card class="summary-card" shadow="never">
          <template #header><span>设计说明</span></template>
          <div class="design-list">
            <div class="design-item">`数据映射操作`：一次性 LLM 设计本体对象、属性、关系如何来源于源数据表，输出候选属性映射和关系来源草案。</div>
            <div class="design-item">`数据映射管理`：对上一步输出的候选结果按对象、属性、来源表、来源字段、计算公式和“关系”逐项确认、修改、落地。</div>
            <div class="design-item">本步骤确认节点表的唯一主键与本体节点间方向：边表以 `SOURCE_ID`、`TARGET_ID` 分别引用两端节点表的主键。完整边关系 SQL 由后续 DDL 生成阶段统一生成。</div>
          </div>
        </el-card>

        <el-card v-if="latestBlueprint" class="summary-card" shadow="never">
          <template #header><span>最新 Guide 设计包</span></template>
          <div class="summary-grid">
            <div class="summary-item">
              <span>版本</span>
              <strong>v{{ latestBlueprint.blueprint_version }}</strong>
            </div>
            <div class="summary-item">
              <span>状态</span>
              <strong>{{ latestBlueprint.blueprint_status }}</strong>
            </div>
            <div class="summary-item">
              <span>源表数</span>
              <strong>{{ (latestBlueprint.selected_tables || []).length }}</strong>
            </div>
            <div class="summary-item">
              <span>业务场景</span>
              <strong>{{ latestBlueprint.business_scenario || '-' }}</strong>
            </div>
            <div class="summary-item">
              <span>启用模式</span>
              <strong>{{ enabledBlueprintPatternCount }}</strong>
            </div>
            <div class="summary-item">
              <span>实体候选</span>
              <strong>{{ latestBlueprintEntityCandidateCount }}</strong>
            </div>
            <div class="summary-item">
              <span>关系候选</span>
              <strong>{{ latestBlueprintRelationCandidateCount }}</strong>
            </div>
            <div class="summary-item">
              <span>标准化视图</span>
              <strong>{{ latestBlueprintViewCount }}</strong>
            </div>
          </div>
          <div class="design-list" style="margin-top: 12px">
            <div class="design-item">规则摘要：{{ latestBlueprintRuleSummary }}</div>
            <div class="design-item">结构化范围：指标族 {{ latestBlueprintFocusFamilies }}；站位 {{ latestBlueprintFocusStations }}</div>
            <div class="design-item">实体候选用于确认对象来源和推荐构建方式；关系候选用于辅助确认本体节点方向及边表引用的两端节点主键。</div>
            <div class="design-item">标准化视图计划：{{ latestBlueprintViewSummary }}</div>
          </div>
        </el-card>

        <el-card v-if="result" class="summary-card" shadow="never">
          <template #header><span>执行摘要</span></template>
          <div class="summary-grid">
            <div class="summary-item">
              <span>分析域</span>
              <strong>{{ result.domain?.domain_name }}</strong>
            </div>
            <div class="summary-item">
              <span>本体对象数</span>
              <strong>{{ result.summary?.entity_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>已生成建议</span>
              <strong>{{ result.summary?.ready_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>空结果对象</span>
              <strong>{{ result.summary?.empty_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>失败对象</span>
              <strong>{{ result.summary?.failed_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>已应用条数</span>
              <strong>{{ result.summary?.applied_total || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>本体关系数</span>
              <strong>{{ result.summary?.relation_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>关系已就绪</span>
              <strong>{{ result.summary?.relation_ready_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>节点 SQL 已就绪</span>
              <strong>{{ result.summary?.node_sql_ready_count || 0 }}</strong>
            </div>
            <div class="summary-item">
              <span>待补齐节点 PK</span>
              <strong>{{ result.summary?.relation_missing_count || 0 }}</strong>
            </div>
          </div>
        </el-card>

        <el-card class="summary-card" shadow="never">
          <template #header><span>对象映射结果</span></template>
          <el-empty v-if="!result?.entities?.length" description="执行一次性映射后，这里会按对象展示候选表、映射建议和抽取规则。" :image-size="76" />
          <template v-else>
            <div class="result-toolbar">
              <div class="result-toolbar-left">
                <el-checkbox
                  :model-value="allApplicableSelected"
                  :indeterminate="isPartiallySelected"
                  @change="toggleSelectAll"
                >
                  选择全部可应用对象
                </el-checkbox>
                <span class="result-toolbar-text">已选 {{ selectedApplicableEntityIds.length }} / {{ applicableEntityIds.length }} 个可应用对象</span>
              </div>
              <el-segmented
                v-model="resultFilter"
                :options="[
                  { label: '全部', value: 'ALL' },
                  { label: '有差异', value: 'DIFF' },
                  { label: '已生成', value: 'READY' },
                  { label: '已应用', value: 'APPLIED' },
                  { label: '空结果', value: 'EMPTY' },
                  { label: '失败', value: 'FAILED' }
                ]"
              />
            </div>
            <el-collapse>
              <el-collapse-item
                v-for="item in filteredEntities"
                :key="item.entity_id"
                :name="item.entity_id"
                :title="`${item.entity_display_name || item.entity_name} | ${statusLabel(item.status)} | ${item.mapping_count || 0} 条`"
              >
              <div class="entity-result-head">
                <el-checkbox
                  :model-value="selectedEntityIds.includes(item.entity_id)"
                  :disabled="!(item.mappings || []).length && !item.node_mapping?.node_sql"
                  @change="(checked: boolean) => toggleEntitySelection(item.entity_id, checked)"
                >
                  纳入批量应用
                </el-checkbox>
                <el-tag :type="statusTagType(item.status)" size="small">{{ statusLabel(item.status) }}</el-tag>
                <span class="entity-desc">{{ item.entity_desc || '无对象描述' }}</span>
                <el-button size="small" @click.stop="rerunEntity(item)" :loading="rerunningEntityId === item.entity_id">重跑该对象</el-button>
                <el-button size="small" type="primary" link @click.stop="jumpToManage(item)">进入管理页</el-button>
              </div>
              <div v-if="item.diff_summary" class="diff-summary">
                <el-tag type="success" size="small">新增 {{ item.diff_summary.added_count || 0 }}</el-tag>
                <el-tag type="warning" size="small">变更 {{ item.diff_summary.changed_count || 0 }}</el-tag>
                <el-tag type="info" size="small">未变 {{ item.diff_summary.unchanged_count || 0 }}</el-tag>
              </div>
              <div v-if="item.blueprint_context?.preferred_tables?.length || item.blueprint_context?.recommended_build_mode" class="diff-summary">
                <el-tag size="small" type="primary">Blueprint v{{ item.blueprint_context?.blueprint_version || '-' }}</el-tag>
                <el-tag v-if="item.blueprint_context?.recommended_build_mode" size="small" type="success">
                  推荐构建 {{ item.blueprint_context.recommended_build_mode }}
                </el-tag>
              </div>
              <div v-if="item.error_message" class="error-box">{{ item.error_message }}</div>
              <div v-if="item.blueprint_context?.preferred_tables?.length" class="subsection">
                <div class="subsection-title">Guide 推荐来源表</div>
                <div class="candidate-table-list">
                  <div v-for="tableName in item.blueprint_context.preferred_tables" :key="`bp-${item.entity_id}-${tableName}`" class="candidate-table-item">
                    <div class="candidate-table-name">{{ tableName }}</div>
                    <div class="candidate-table-comment">该表来自最新 Guide 设计包的 mapping_design 推荐</div>
                  </div>
                </div>
              </div>
              <div class="subsection">
                <div class="subsection-title">候选表目录（仅表名/表注释）</div>
                <div class="candidate-table-list">
                  <div v-for="table in item.table_selection?.catalog_tables || []" :key="`catalog-${item.entity_id}-${table.table_name}`" class="candidate-table-item">
                    <div class="candidate-table-name">{{ table.table_name }}</div>
                    <div class="candidate-table-comment">{{ table.comments || '无表说明' }}</div>
                  </div>
                </div>
              </div>
              <div class="subsection">
                <div class="subsection-title">锁定对象表（大模型选表结果）</div>
                <div class="candidate-table-list">
                  <div v-for="table in item.table_selection?.selected_tables || []" :key="`locked-${item.entity_id}-${table.table_name}`" class="candidate-table-item">
                    <div class="candidate-table-name">{{ table.table_name }}</div>
                    <div class="candidate-table-comment">{{ table.reason || '无选择理由' }}</div>
                  </div>
                </div>
              </div>
              <div class="subsection">
                <div class="subsection-title">候选表</div>
                <div class="candidate-table-list">
                  <div v-for="table in item.candidate_tables || []" :key="table.table_name" class="candidate-table-item">
                    <div class="candidate-table-name">{{ table.table_name }}</div>
                    <div class="candidate-table-comment">{{ table.comments || '无表说明' }}</div>
                    <div class="candidate-table-columns">{{ (table.columns || []).map((column: any) => column.column_name).join(' / ') }}</div>
                  </div>
                </div>
              </div>
              <div class="subsection">
                <div class="subsection-title">Oracle 26ai 顶点表列属性</div>
                <div v-if="item.oracle_vertex" class="oracle-graph-meta">
                  <el-tag type="primary" size="small">VERTEX {{ item.oracle_vertex.vertex_label || item.entity_name }}</el-tag>
                  <el-tag type="success" size="small">表 {{ item.oracle_vertex.vertex_table || '待确认' }}</el-tag>
                  <el-tag :type="item.oracle_vertex.oracle_graph_ready ? 'success' : 'warning'" size="small">
                    KEY {{ item.oracle_vertex.key_property || '待确认' }} → {{ item.oracle_vertex.key_source_column || '待确认' }}
                  </el-tag>
                  <span>共 {{ item.oracle_vertex.property_count || 0 }} 个节点属性列</span>
                </div>
                <div v-if="item.node_mapping?.node_sql" class="node-sql-box">
                  <div class="node-sql-head">
                    <el-tag type="success" size="small">{{ item.node_mapping.build_type || 'TABLE' }}</el-tag>
                    <strong>{{ item.node_mapping.node_table_name }}</strong>
                    <span>来源：{{ (item.node_mapping.source_tables || []).join(' / ') || '由 SQL 推导' }}</span>
                  </div>
                  <div v-if="item.node_mapping.design_reason" class="node-design-reason">{{ item.node_mapping.design_reason }}</div>
                  <el-input :model-value="item.node_mapping.node_sql" type="textarea" :rows="8" readonly />
                </div>
                <el-table :data="item.mappings || []" border stripe size="small" max-height="280">
                  <el-table-column label="图角色" width="86">
                    <template #default="{ row }">
                      <el-tag :type="row.is_vertex_key ? 'success' : 'info'" size="small">{{ row.is_vertex_key ? 'KEY' : '属性' }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="propertyDisplayName" label="本体属性" min-width="140">
                    <template #default="{ row }">{{ row.propertyDisplayName || row.propertyName }}</template>
                  </el-table-column>
                  <el-table-column label="差异" width="90">
                    <template #default="{ row }">
                      <el-tag :type="diffTagType(row.diff_status)" size="small">{{ diffStatusLabel(row.diff_status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="来源" min-width="220">
                    <template #default="{ row }">{{ row.sourceTable }}.{{ row.sourceColumn }}</template>
                  </el-table-column>
                  <el-table-column prop="mappingType" label="映射类型" width="120" />
                  <el-table-column prop="sourceDataType" label="源字段类型" width="120" />
                  <el-table-column prop="reason" label="匹配理由/抽取说明" min-width="220" />
                  <el-table-column prop="formula" label="抽取规则" min-width="180" />
                </el-table>
              </div>
              <div class="subsection">
                <div class="subsection-title">LLM 原始返回</div>
                <el-input :model-value="item.llm_raw_output || ''" type="textarea" :rows="8" readonly />
              </div>
              </el-collapse-item>
            </el-collapse>
          </template>
        </el-card>

        <el-card class="summary-card" shadow="never">
          <template #header><span>Oracle 26ai 本体关系实现</span></template>
          <el-empty
            v-if="!result?.relations?.length"
            description="执行全域映射后，这里会按本体关系展示边表如何通过两端节点表的唯一主键建立连接。"
            :image-size="76"
          />
          <el-table v-else :data="result.relations" border stripe size="small">
            <el-table-column label="本体关系" min-width="180" fixed>
              <template #default="{ row }">
                <div class="relation-name">{{ row.relation_name }}</div>
                <code>{{ row.edge_table_name || '-' }}</code>
                <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="SOURCE 本体节点表" min-width="230">
              <template #default="{ row }">
                <div>{{ row.source_entity_display_name || row.source_entity_name || '-' }}</div>
                <code>{{ row.oracle_edge?.source_vertex_table || '?' }}.{{ row.oracle_edge?.source_vertex_key_property || '?' }}（唯一 PK） → SOURCE_ID</code>
              </template>
            </el-table-column>
            <el-table-column label="DESTINATION 本体节点表" min-width="230">
              <template #default="{ row }">
                <div>{{ row.target_entity_display_name || row.target_entity_name || '-' }}</div>
                <code>{{ row.oracle_edge?.target_vertex_table || '?' }}.{{ row.oracle_edge?.target_vertex_key_property || '?' }}（唯一 PK） → TARGET_ID</code>
              </template>
            </el-table-column>
            <el-table-column label="边关系表实现" min-width="300">
              <template #default="{ row }">
                <div><strong>{{ row.edge_table_name || '待生成边关系表' }}</strong></div>
                <div class="relation-join">EDGE_ID 为边唯一标识；SOURCE_ID、TARGET_ID 分别引用两端节点表的唯一 PK。</div>
                <div class="relation-join">完整建表及边关系 SQL 将在 DDL 生成中提供。</div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card class="summary-card" shadow="never">
          <template #header>
            <div class="history-card-header">
              <span>批量映射历史</span>
              <el-tag v-if="latestBlueprint?.blueprint_version" size="small" type="info">
                仅显示 Blueprint v{{ latestBlueprint.blueprint_version }}
              </el-tag>
            </div>
          </template>
          <el-empty v-if="!historyItems.length" description="暂无历史记录" :image-size="64" />
          <template v-else>
            <div class="history-compare-bar">
              <el-select v-model="compareTaskA" placeholder="选择任务A" clearable style="width: 220px">
                <el-option v-for="item in historyItems" :key="`a-${item.task_id}`" :label="`${item.created_at} / ${item.task_type}`" :value="item.task_id" />
              </el-select>
              <el-select v-model="compareTaskB" placeholder="选择任务B" clearable style="width: 220px">
                <el-option v-for="item in historyItems" :key="`b-${item.task_id}`" :label="`${item.created_at} / ${item.task_type}`" :value="item.task_id" />
              </el-select>
              <el-button :disabled="!compareTaskA || !compareTaskB || compareTaskA === compareTaskB" @click="openCompareDialog">对比任务</el-button>
            </div>
            <div class="history-list">
              <div v-for="item in historyItems" :key="item.task_id" class="history-item">
                <div class="history-main">
                  <div class="history-title">{{ currentDomain?.domain_name || '未命名分析域' }} / {{ item.task_type }}</div>
                  <div class="history-meta">{{ item.created_at }} | Status: {{ item.status }} | Processed: {{ item.summary?.processed_count || 0 }}/{{ item.summary?.entity_count || 0 }} | Ready: {{ item.summary?.ready_count || 0 }} | Applied: {{ item.summary?.applied_total || 0 }}</div>
                </div>
                <div class="history-actions">
                  <el-button size="small" @click="loadHistoryItem(item)">载入</el-button>
                  <el-button size="small" type="primary" link @click="openHistoryDetail(item)">详情</el-button>
                </div>
              </div>
            </div>
          </template>
        </el-card>
      </div>
    </div>

    <el-drawer v-model="historyDetailVisible" title="批量映射任务详情" size="55%">
      <div v-if="historyDetail" class="history-detail">
        <div class="history-detail-actions">
          <el-button type="primary" @click="restoreHistoryDetail">恢复到当前工作区</el-button>
        </div>
        <div class="history-detail-grid">
          <div class="history-detail-item"><span>任务类型</span><strong>{{ historyDetail.task_type }}</strong></div>
          <div class="history-detail-item"><span>状态</span><strong>{{ historyDetail.status }}</strong></div>
          <div class="history-detail-item"><span>创建时间</span><strong>{{ historyDetail.created_at }}</strong></div>
          <div class="history-detail-item"><span>创建人</span><strong>{{ historyDetail.created_by }}</strong></div>
        </div>
        <div class="history-detail-section">
          <div class="history-detail-title">请求参数</div>
          <el-input :model-value="JSON.stringify(historyDetail.request, null, 2)" type="textarea" :rows="10" readonly />
        </div>
        <div class="history-detail-section">
          <div class="history-detail-title">摘要</div>
          <el-input :model-value="JSON.stringify(historyDetail.summary, null, 2)" type="textarea" :rows="6" readonly />
        </div>
        <div class="history-detail-section">
          <div class="history-detail-title">结果快照</div>
          <el-input :model-value="JSON.stringify(historyDetail.result, null, 2)" type="textarea" :rows="16" readonly />
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="previewVisible" title="应用前变更确认" width="920px">
      <div class="preview-summary" v-if="applyPreview">
        <el-tag type="success">将新增 {{ applyPreview.summary.added }}</el-tag>
        <el-tag type="warning">将变更 {{ applyPreview.summary.changed }}</el-tag>
        <el-tag type="info">涉及对象 {{ applyPreview.summary.entities }}</el-tag>
        <el-tag type="success">节点 SQL {{ applyPreview.summary.nodes }}</el-tag>
      </div>
      <div v-if="applyPreview?.nodes?.length" class="preview-relation-section">
        <div class="subsection-title">整体本体节点构建 SQL</div>
        <el-table :data="applyPreview.nodes" border stripe size="small" max-height="240">
          <el-table-column prop="entity_display_name" label="本体对象" min-width="140" />
          <el-table-column prop="node_table_name" label="节点表" min-width="170" />
          <el-table-column prop="build_type" label="构建方式" width="100" />
          <el-table-column prop="key_property_name" label="节点 KEY" min-width="130" />
          <el-table-column prop="node_sql" label="节点 SQL" min-width="360" show-overflow-tooltip />
        </el-table>
      </div>
      <el-table v-if="applyPreview?.rows?.length" :data="applyPreview.rows" border stripe size="small" max-height="300">
        <el-table-column prop="entity_display_name" label="本体对象" min-width="120" />
        <el-table-column prop="propertyDisplayName" label="本体属性" min-width="140">
          <template #default="{ row }">{{ row.propertyDisplayName || row.propertyName }}</template>
        </el-table-column>
        <el-table-column prop="diff_status" label="变更类型" width="90">
          <template #default="{ row }">
            <el-tag :type="diffTagType(row.diff_status)" size="small">{{ diffStatusLabel(row.diff_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="建议来源" min-width="220">
          <template #default="{ row }">{{ row.sourceTable }}.{{ row.sourceColumn }}</template>
        </el-table-column>
        <el-table-column label="当前来源" min-width="220">
          <template #default="{ row }">{{ row.currentSource }}</template>
        </el-table-column>
        <el-table-column prop="mappingType" label="映射类型" width="120" />
      </el-table>
      <template #footer>
        <el-button @click="previewVisible = false">取消</el-button>
        <el-button type="primary" :loading="previewApplying" @click="confirmApplyPreview">确认应用</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="compareVisible" title="历史任务结果对比" width="980px">
      <div v-if="compareResult" class="compare-summary">
        <el-tag>任务A对象 {{ compareResult.summary.entitiesA }}</el-tag>
        <el-tag>任务B对象 {{ compareResult.summary.entitiesB }}</el-tag>
        <el-tag type="success">新增对象 {{ compareResult.summary.addedEntities }}</el-tag>
        <el-tag type="warning">状态变化 {{ compareResult.summary.statusChanged }}</el-tag>
        <el-tag type="info">映射数变化 {{ compareResult.summary.mappingCountChanged }}</el-tag>
      </div>
      <el-table v-if="compareResult" :data="compareResult.rows" border stripe size="small" max-height="460">
        <el-table-column prop="entity_display_name" label="本体对象" min-width="140" />
        <el-table-column prop="statusA" label="任务A状态" width="100" />
        <el-table-column prop="statusB" label="任务B状态" width="100" />
        <el-table-column prop="mappingCountA" label="任务A映射数" width="110" />
        <el-table-column prop="mappingCountB" label="任务B映射数" width="110" />
        <el-table-column prop="changeType" label="变化" min-width="180" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { domainApi, entityApi, mappingApi, sourceApi, systemApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const router = useRouter()
const currentDomainId = ref(appStore.currentDomainId || '')
const domains = ref<any[]>([])
const entities = ref<any[]>([])
const dataSources = ref<any[]>([])
const schemaOptions = ref<string[]>([])
const llmConfigs = ref<any[]>([])
const result = ref<any>(null)
const latestBlueprint = ref<any>(null)
const generating = ref(false)
const applying = ref(false)
const applyingSelected = ref(false)
const clearingTasks = ref(false)
const rerunningEntityId = ref('')
const selectedEntityIds = ref<string[]>([])
const resultFilter = ref<'ALL' | 'DIFF' | 'READY' | 'APPLIED' | 'EMPTY' | 'FAILED'>('ALL')
const applyScope = ref<'ALL' | 'ADDED' | 'CHANGED'>('ALL')
const historyItems = ref<any[]>([])
const historyDetailVisible = ref(false)
const historyDetail = ref<any>(null)
const previewVisible = ref(false)
const previewApplying = ref(false)
const applyPreview = ref<any>(null)
const pendingApplyEntityIds = ref<string[]>([])
const pendingApplyMode = ref<'ALL' | 'SELECTED'>('ALL')
const compareTaskA = ref('')
const compareTaskB = ref('')
const compareVisible = ref(false)
const compareResult = ref<any>(null)
const activeTaskId = ref('')
const activeTaskDetail = ref<any>(null)
let taskPollingTimer: number | null = null

const form = ref({
  source_id: '',
  schema: '',
  model_config_id: '',
  sample_limit: 3,
  mapping_instruction: localStorage.getItem('bulkMappingInstruction') || ''
})

const currentDomain = computed(() => domains.value.find(item => item.domain_id === currentDomainId.value))
const enabledBlueprintPatternCount = computed(() =>
  (latestBlueprint.value?.semantic_patterns || []).filter((item: any) => item.enabled).length
)
const latestBlueprintRuleSummary = computed(() =>
  latestBlueprint.value?.rule_summary?.summary || latestBlueprint.value?.spec_limit_summary?.summary || '无'
)
const latestBlueprintEntityCandidateCount = computed(() =>
  (latestBlueprint.value?.entity_candidates || []).length
)
const latestBlueprintRelationCandidateCount = computed(() =>
  (latestBlueprint.value?.relation_candidates || []).length
)
const latestBlueprintViewCount = computed(() =>
  (latestBlueprint.value?.view_plan?.standardized_views || latestBlueprint.value?.deployment_design?.semantic_views || []).length
)
const latestBlueprintFocusFamilies = computed(() =>
  (latestBlueprint.value?.focus_scope?.focus_metric_families || []).join(' / ') || '未限定'
)
const latestBlueprintFocusStations = computed(() =>
  (latestBlueprint.value?.focus_scope?.focus_stations || []).join(' / ') || '未限定'
)
const latestBlueprintViewSummary = computed(() =>
  (latestBlueprint.value?.view_plan?.standardized_views || latestBlueprint.value?.deployment_design?.semantic_views || [])
    .map((item: any) => item.view_name)
    .slice(0, 6)
    .join('，') || '无'
)
const applicableEntityIds = computed(() => {
  return (result.value?.entities || [])
    .filter((item: any) => (item.mappings || []).length > 0 || Boolean(item.node_mapping?.node_sql))
    .map((item: any) => item.entity_id)
})
const hasApplicableSuggestions = computed(() => {
  return applicableEntityIds.value.length > 0
})
const selectedApplicableEntityIds = computed(() => {
  const allowed = new Set(applicableEntityIds.value)
  return selectedEntityIds.value.filter(item => allowed.has(item))
})
const allApplicableSelected = computed(() => {
  return applicableEntityIds.value.length > 0 && selectedApplicableEntityIds.value.length === applicableEntityIds.value.length
})
const isPartiallySelected = computed(() => {
  return selectedApplicableEntityIds.value.length > 0 && !allApplicableSelected.value
})
const filteredEntities = computed(() => {
  const entities = result.value?.entities || []
  if (resultFilter.value === 'ALL') return entities
  if (resultFilter.value === 'DIFF') {
    return entities.filter((item: any) => {
      const diff = item.diff_summary || {}
      return (diff.added_count || 0) > 0 || (diff.changed_count || 0) > 0
    })
  }
  return entities.filter((item: any) => item.status === resultFilter.value)
})
const activeTaskSummary = computed(() => activeTaskDetail.value?.summary || {})
const isActiveTaskFinished = computed(() => ['SUCCESS', 'PARTIAL', 'FAILED'].includes(activeTaskDetail.value?.status || ''))
const activeTaskProgressPercent = computed(() => {
  const total = Number(activeTaskSummary.value?.entity_count || 0)
  const processed = Number(activeTaskSummary.value?.processed_count || 0)
  if (!total) return 0
  return Math.min(100, Math.round((processed / total) * 100))
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

const loadEntities = async () => {
  if (!currentDomainId.value) {
    entities.value = []
    return
  }
  try {
    const res = await entityApi.list(currentDomainId.value)
    entities.value = res.data || []
  } catch (e) {}
}

const loadDataSources = async () => {
  if (!currentDomainId.value) {
    dataSources.value = []
    return
  }
  try {
    const res = await sourceApi.listDataSources(currentDomainId.value)
    dataSources.value = res.data || []
    if (!form.value.source_id && dataSources.value.length) {
      const defaultSource = dataSources.value.find((item: any) => item.is_default === 'Y') || dataSources.value[0]
      form.value.source_id = defaultSource.source_id
      await loadSchemas()
    }
  } catch (e) {}
}

const loadSchemas = async () => {
  if (!form.value.source_id) {
    schemaOptions.value = []
    form.value.schema = ''
    return
  }
  try {
    const res = await sourceApi.getSchemas(form.value.source_id)
    schemaOptions.value = res.data?.schemas || []
    if (!form.value.schema || !schemaOptions.value.includes(form.value.schema)) {
      form.value.schema = res.data?.default_schema || schemaOptions.value[0] || ''
    }
  } catch (e) {}
}

const loadModels = async () => {
  try {
    const res = await systemApi.getLLMConfigs()
    llmConfigs.value = (res.data || []).filter((item: any) => item.is_active === 'Y')
    if (!form.value.model_config_id && llmConfigs.value.length) {
      const defaultModel = llmConfigs.value.find((item: any) => item.is_default === 'Y') || llmConfigs.value[0]
      form.value.model_config_id = defaultModel?.config_id || ''
    }
  } catch (e) {}
}

const loadLatestBlueprint = async () => {
  if (!currentDomainId.value) {
    latestBlueprint.value = null
    return
  }
  try {
    const res = await mappingApi.getLatestBlueprint(currentDomainId.value)
    latestBlueprint.value = res.data || null
  } catch (e) {
    latestBlueprint.value = null
  }
}

const loadAll = async () => {
  await loadDomains()
  await Promise.all([loadEntities(), loadDataSources(), loadModels(), loadLatestBlueprint()])
  await loadHistory()
  syncActiveTaskFromHistory()
}

const handleDomainChange = async () => {
  const domain = domains.value.find(item => item.domain_id === currentDomainId.value)
  appStore.setCurrentDomain(currentDomainId.value, domain?.domain_name || '')
  result.value = null
  selectedEntityIds.value = []
  activeTaskId.value = ''
  activeTaskDetail.value = null
  stopTaskPolling()
  form.value.source_id = ''
  form.value.schema = ''
  await Promise.all([loadEntities(), loadDataSources(), loadHistory(), loadLatestBlueprint()])
  syncActiveTaskFromHistory()
}

const handleSourceChange = async () => {
  form.value.schema = ''
  await loadSchemas()
}

const generateMappings = async (autoApply = false) => {
  if (!currentDomainId.value) { ElMessage.warning('请选择分析域'); return }
  if (!form.value.source_id) { ElMessage.warning('请选择映射来源数据源'); return }
  if (!form.value.schema) { ElMessage.warning('请选择 Schema'); return }
  if (!form.value.model_config_id) { ElMessage.warning('请选择 LLM 模型'); return }
  generating.value = !autoApply
  applying.value = autoApply
  try {
    const res: any = await mappingApi.bulkAutoMapping(currentDomainId.value, {
      source_id: form.value.source_id,
      schema: form.value.schema,
      model_config_id: form.value.model_config_id,
      sample_limit: form.value.sample_limit,
      mapping_instruction: form.value.mapping_instruction.trim() || null,
      auto_apply: autoApply
    })
    if (res.data?.task_id) {
      activeTaskId.value = res.data.task_id
      activeTaskDetail.value = normalizeTask({
        task_id: res.data.task_id,
        status: res.data.status,
        summary_json: JSON.stringify(res.data.summary || {}),
        result_json: JSON.stringify({ summary: res.data.summary || {}, entities: [], relations: [] }),
        created_at: new Date().toISOString(),
        task_type: 'BULK_GENERATE'
      })
      startTaskPolling()
    } else {
      result.value = res.data
      selectedEntityIds.value = (res.data?.entities || [])
        .filter((item: any) => (item.mappings || []).length > 0 || Boolean(item.node_mapping?.node_sql))
        .map((item: any) => item.entity_id)
    }
    await loadHistory()
    syncActiveTaskFromHistory()
    ElMessage.success(res.message || (autoApply ? '全域映射建议任务已提交并自动应用' : '全域映射建议任务已提交到后台'))
  } catch (e: any) {
    ElMessage.error(e?.message || '生成全域映射建议失败')
  } finally {
    generating.value = false
    applying.value = false
  }
}

const applyAllMappings = async () => {
  pendingApplyMode.value = 'ALL'
  pendingApplyEntityIds.value = (result.value?.entities || []).map((item: any) => item.entity_id)
  openApplyPreview(pendingApplyEntityIds.value)
}

const applySelectedMappings = async () => {
  if (!selectedApplicableEntityIds.value.length) {
    ElMessage.warning('请先选择至少一个可应用对象')
    return
  }
  pendingApplyMode.value = 'SELECTED'
  pendingApplyEntityIds.value = [...selectedApplicableEntityIds.value]
  openApplyPreview(selectedApplicableEntityIds.value)
}

const applyEntities = async (entityIds: string[]) => {
  const selectedSet = new Set(entityIds)
  const entities = (result.value?.entities || [])
    .filter((item: any) =>
      selectedSet.has(item.entity_id)
      && ((item.mappings || []).length > 0 || Boolean(item.node_mapping?.node_sql))
    )
    .map((item: any) => ({
      entity_id: item.entity_id,
      build_type: item.node_mapping?.build_type || item.oracle_vertex?.build_type || null,
      table_name: item.node_mapping?.node_table_name || item.oracle_vertex?.vertex_table || null,
      view_sql: item.node_mapping?.node_sql || null,
      mappings: (item.mappings || [])
        .filter((mapping: any) => applyScope.value === 'ALL' || mapping.diff_status === applyScope.value)
        .map((mapping: any) => ({
        ...mapping,
        action: 'accept',
        property_name: mapping.propertyName,
        property_display_name: mapping.propertyDisplayName,
        property_desc: mapping.propertyDesc,
        source_table: mapping.sourceTable,
        source_column: mapping.sourceColumn,
        source_data_type: mapping.sourceDataType,
        mapping_type: mapping.mappingType,
        formula_expr: mapping.formula,
        formula_desc: mapping.reason,
        confidence: mapping.confidence,
        property_id: mapping.matchedPropertyId || '',
      }))
    }))
    .filter((item: any) => item.mappings.length > 0 || Boolean(item.view_sql))
  if (!entities.length) {
    ElMessage.warning('当前没有符合应用范围的映射建议')
    return false
  }
  const res = await mappingApi.bulkApplyMappings(currentDomainId.value, { entities, relations: [] })
  const appliedMap = new Map((res.data?.entities || []).map((item: any) => [item.entity_id, item.applied_count]))
  result.value = {
    ...result.value,
    summary: {
      ...(result.value?.summary || {}),
      applied_total: ((result.value?.summary?.applied_total || 0) + (res.data?.applied_total || 0)),
      applied_relation_count: result.value?.summary?.applied_relation_count || 0
    },
    entities: (result.value?.entities || []).map((item: any) => (
      appliedMap.has(item.entity_id)
        ? { ...item, status: 'APPLIED', applied_count: appliedMap.get(item.entity_id) }
        : item
    )),
    relations: result.value?.relations || []
  }
  return true
}

const toggleSelectAll = (checked: boolean | string | number) => {
  selectedEntityIds.value = checked ? [...applicableEntityIds.value] : []
}

const toggleEntitySelection = (entityId: string, checked: boolean | string | number) => {
  const next = new Set(selectedEntityIds.value)
  if (checked) next.add(entityId)
  else next.delete(entityId)
  selectedEntityIds.value = [...next]
}

const rerunEntity = async (item: any) => {
  if (!currentDomainId.value || !form.value.source_id || !form.value.schema || !form.value.model_config_id) {
    ElMessage.warning('请先完善分析域、数据源、Schema 和模型配置')
    return
  }
  rerunningEntityId.value = item.entity_id
  try {
    const res = await mappingApi.autoMapping(item.entity_id, {
      entity_id: item.entity_id,
      domain_id: currentDomainId.value,
      source_id: form.value.source_id,
      schema: form.value.schema,
      sample_limit: form.value.sample_limit,
      mapping_instruction: form.value.mapping_instruction.trim() || null,
      model_config_id: form.value.model_config_id
    })
    const nextItem = {
      ...item,
      status: (res.data?.mappings || []).length ? 'READY' : 'EMPTY',
      mappings: res.data?.mappings || [],
      candidate_tables: res.data?.candidate_tables || [],
      llm_raw_output: res.data?.llm_raw_output || '',
      mapping_count: res.data?.mapping_count || 0,
      generation_mode: res.data?.generation_mode || '',
      oracle_vertex: res.data?.oracle_vertex || null
    }
    result.value = {
      ...result.value,
      entities: (result.value?.entities || []).map((entity: any) => entity.entity_id === item.entity_id ? nextItem : entity),
      summary: {
        ...(result.value?.summary || {}),
        ready_count: (result.value?.entities || []).map((entity: any) => entity.entity_id === item.entity_id ? nextItem : entity).filter((entity: any) => entity.status === 'READY' || entity.status === 'APPLIED').length,
        empty_count: (result.value?.entities || []).map((entity: any) => entity.entity_id === item.entity_id ? nextItem : entity).filter((entity: any) => entity.status === 'EMPTY').length,
        failed_count: (result.value?.entities || []).map((entity: any) => entity.entity_id === item.entity_id ? nextItem : entity).filter((entity: any) => entity.status === 'FAILED').length
      }
    }
    if ((res.data?.mappings || []).length) {
      toggleEntitySelection(item.entity_id, true)
    }
    ElMessage.success(`已重跑对象“${item.entity_display_name || item.entity_name}”`)
  } catch (e) {} finally {
    rerunningEntityId.value = ''
  }
}

const jumpToManage = (item: any) => {
  router.push({ path: '/mapping/manage', query: { entity_id: item.entity_id } })
}

const openApplyPreview = (entityIds: string[]) => {
  const selectedSet = new Set(entityIds)
  const nodeRows = (result.value?.entities || [])
    .filter((item: any) => selectedSet.has(item.entity_id) && Boolean(item.node_mapping?.node_sql))
    .map((item: any) => ({
      ...item.node_mapping,
      entity_id: item.entity_id,
      entity_display_name: item.entity_display_name || item.entity_name
    }))
  const rows = (result.value?.entities || [])
    .filter((item: any) => selectedSet.has(item.entity_id))
    .flatMap((item: any) => (item.mappings || [])
      .filter((mapping: any) => applyScope.value === 'ALL' || mapping.diff_status === applyScope.value)
      .map((mapping: any) => {
        const current = (item.existing_mappings || []).find((existing: any) =>
          ((existing.property_id || '') && (existing.property_id === mapping.matchedPropertyId)) ||
          ((existing.property_name || '').toLowerCase() === (mapping.propertyName || '').toLowerCase())
        )
        return {
          ...mapping,
          entity_id: item.entity_id,
          entity_display_name: item.entity_display_name || item.entity_name,
          currentSource: current?.source_table && current?.source_column ? `${current.source_table}.${current.source_column}` : '无'
        }
      }))
  if (!nodeRows.length && !rows.length) {
    ElMessage.warning('当前没有符合应用范围的变更项')
    return
  }
  applyPreview.value = {
    rows,
    nodes: nodeRows,
    summary: {
      added: rows.filter((row: any) => row.diff_status === 'ADDED').length,
      changed: rows.filter((row: any) => row.diff_status === 'CHANGED').length,
      entities: new Set([
        ...nodeRows.map((row: any) => row.entity_id),
        ...rows.map((row: any) => row.entity_id)
      ]).size,
      nodes: nodeRows.length
    }
  }
  previewVisible.value = true
}

const confirmApplyPreview = async () => {
  if (!applyPreview.value) return
  previewApplying.value = true
  if (pendingApplyMode.value === 'ALL') applying.value = true
  else applyingSelected.value = true
  try {
    const applied = await applyEntities(pendingApplyEntityIds.value)
    if (!applied) return
    await loadHistory()
    previewVisible.value = false
    ElMessage.success(pendingApplyMode.value === 'ALL' ? '已批量应用全部建议' : '已应用选中对象的映射建议')
  } catch (e) {} finally {
    previewApplying.value = false
    applying.value = false
    applyingSelected.value = false
  }
}

const clearMappingOperationData = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择分析域')
    return
  }
  try {
    await ElMessageBox.confirm(
      '将删除当前分析域的映射操作任务、结果快照和历史记录；不会删除已确认的本体属性映射、关系配置或 DDL 数据。是否继续？',
      '清除数据映射操作数据',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  clearingTasks.value = true
  try {
    const res = await mappingApi.clearTasks(currentDomainId.value)
    result.value = null
    historyItems.value = []
    historyDetail.value = null
    historyDetailVisible.value = false
    activeTaskId.value = ''
    activeTaskDetail.value = null
    selectedEntityIds.value = []
    compareTaskA.value = ''
    compareTaskB.value = ''
    stopTaskPolling()
    ElMessage.success(`已清除 ${res.data?.deleted_count || 0} 条映射操作记录，可重新生成映射建议`)
  } catch (e) {
    // 全局请求拦截器会展示后端错误信息。
  } finally {
    clearingTasks.value = false
  }
}

const loadHistory = async () => {
  if (!currentDomainId.value) {
    historyItems.value = []
    return
  }
  try {
    const res = await mappingApi.listTasks(currentDomainId.value)
    historyItems.value = (res.data || []).map((item: any) => normalizeTask(item))
  } catch (e) {
    historyItems.value = []
  }
}

const loadHistoryItem = (item: any) => {
  result.value = item.result
  selectedEntityIds.value = ((item.result?.entities || []) as any[])
    .filter(entity => (entity.mappings || []).length > 0 || Boolean(entity.node_mapping?.node_sql))
    .map(entity => entity.entity_id)
  ElMessage.success('已载入历史映射结果')
}

const openHistoryDetail = async (item: any) => {
  try {
    const res = await mappingApi.getTask(item.task_id)
    historyDetail.value = normalizeTask(res.data)
    historyDetailVisible.value = true
  } catch (e) {}
}

const restoreHistoryDetail = async () => {
  if (!historyDetail.value) return
  const request = historyDetail.value.request || {}
  if (historyDetail.value.domain_id && historyDetail.value.domain_id !== currentDomainId.value) {
    currentDomainId.value = historyDetail.value.domain_id
    const domain = domains.value.find(item => item.domain_id === currentDomainId.value)
    appStore.setCurrentDomain(currentDomainId.value, domain?.domain_name || '')
    await Promise.all([loadEntities(), loadDataSources(), loadHistory()])
  }
  form.value.source_id = request.source_id || form.value.source_id
  form.value.schema = request.schema || form.value.schema
  form.value.model_config_id = request.model_config_id || form.value.model_config_id
  form.value.sample_limit = request.sample_limit || form.value.sample_limit
  form.value.mapping_instruction = request.mapping_instruction || ''
  result.value = historyDetail.value.result
  selectedEntityIds.value = ((historyDetail.value.result?.entities || []) as any[])
    .filter(entity => (entity.mappings || []).length > 0 || Boolean(entity.node_mapping?.node_sql))
    .map(entity => entity.entity_id)
  historyDetailVisible.value = false
  ElMessage.success('已恢复历史任务到当前工作区')
}

const openCompareDialog = () => {
  const taskA = historyItems.value.find((item: any) => item.task_id === compareTaskA.value) as any
  const taskB = historyItems.value.find((item: any) => item.task_id === compareTaskB.value) as any
  if (!taskA || !taskB) return
  const rowsA: any[] = taskA.result?.entities || []
  const rowsB: any[] = taskB.result?.entities || []
  const mapA = new Map(rowsA.map((item: any) => [item.entity_id, item]))
  const mapB = new Map(rowsB.map((item: any) => [item.entity_id, item]))
  const ids = Array.from(new Set([...mapA.keys(), ...mapB.keys()]))
  const rows = ids.map(id => {
    const a: any = mapA.get(id)
    const b: any = mapB.get(id)
    const statusA = a?.status || '无'
    const statusB = b?.status || '无'
    const mappingCountA = a?.mapping_count || 0
    const mappingCountB = b?.mapping_count || 0
    let changeType = '无变化'
    if (!a && b) changeType = '任务B新增对象'
    else if (a && !b) changeType = '任务B缺失对象'
    else if (statusA !== statusB) changeType = '状态变化'
    else if (mappingCountA !== mappingCountB) changeType = '映射数量变化'
    return {
      entity_id: id,
      entity_display_name: a?.entity_display_name || b?.entity_display_name || a?.entity_name || b?.entity_name || id,
      statusA,
      statusB,
      mappingCountA,
      mappingCountB,
      changeType
    }
  })
  compareResult.value = {
    rows,
    summary: {
      entitiesA: rowsA.length,
      entitiesB: rowsB.length,
      addedEntities: rows.filter((row: any) => row.changeType === '任务B新增对象').length,
      statusChanged: rows.filter((row: any) => row.changeType === '状态变化').length,
      mappingCountChanged: rows.filter((row: any) => row.changeType === '映射数量变化').length
    }
  }
  compareVisible.value = true
}

const parseJson = (raw: any) => {
  if (!raw) return {}
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

const normalizeTask = (item: any) => ({
  ...item,
  request: parseJson(item?.request_json),
  result: parseJson(item?.result_json),
  summary: parseJson(item?.summary_json),
})

const syncActiveTaskFromHistory = () => {
  const runningTask = historyItems.value.find((item: any) => item.task_type === 'BULK_GENERATE' && item.status === 'IN_PROGRESS')
  if (runningTask) {
    activeTaskId.value = runningTask.task_id
    activeTaskDetail.value = runningTask
    startTaskPolling()
    return
  }
  if (activeTaskId.value) {
    const matched = historyItems.value.find((item: any) => item.task_id === activeTaskId.value)
    if (matched) {
      activeTaskDetail.value = matched
      if (matched.status === 'IN_PROGRESS') startTaskPolling()
      else stopTaskPolling()
      return
    }
  }
  activeTaskId.value = ''
  activeTaskDetail.value = null
  stopTaskPolling()
}

const refreshActiveTask = async () => {
  if (!activeTaskId.value) return
  try {
    const res = await mappingApi.getTask(activeTaskId.value)
    const task = normalizeTask(res.data)
    const previousStatus = activeTaskDetail.value?.status || ''
    activeTaskDetail.value = task
    historyItems.value = historyItems.value.map((item: any) => item.task_id === task.task_id ? task : item)
    if (!historyItems.value.some((item: any) => item.task_id === task.task_id)) {
      historyItems.value.unshift(task)
    }
    if (['SUCCESS', 'PARTIAL', 'FAILED'].includes(task.status)) {
      stopTaskPolling()
      if (previousStatus === 'IN_PROGRESS') {
        if (task.status === 'FAILED') {
          ElMessage.error('后台全域映射任务执行失败')
        } else {
          result.value = task.result
          selectedEntityIds.value = ((task.result?.entities || []) as any[])
            .filter(entity => (entity.mappings || []).length > 0 || Boolean(entity.node_mapping?.node_sql))
            .map(entity => entity.entity_id)
          ElMessage.success(task.status === 'PARTIAL' ? '后台全域映射任务已结束，存在部分失败对象' : '后台全域映射任务已完成')
        }
      }
    }
  } catch (e) {}
}

const loadActiveTaskResult = () => {
  if (!activeTaskDetail.value?.result) return
  result.value = activeTaskDetail.value.result
  selectedEntityIds.value = ((activeTaskDetail.value.result?.entities || []) as any[])
    .filter(entity => (entity.mappings || []).length > 0 || Boolean(entity.node_mapping?.node_sql))
    .map(entity => entity.entity_id)
  ElMessage.success('已载入后台任务结果')
}

const startTaskPolling = () => {
  if (taskPollingTimer !== null) return
  taskPollingTimer = window.setInterval(() => {
    refreshActiveTask()
  }, 5000)
}

const stopTaskPolling = () => {
  if (taskPollingTimer !== null) {
    window.clearInterval(taskPollingTimer)
    taskPollingTimer = null
  }
}

const statusLabel = (status: string) => ({
  READY: '已生成建议',
  APPLIED: '已应用',
  EMPTY: '无建议',
  FAILED: '失败'
}[status] || status)

const statusTagType = (status: string) => ({
  READY: 'warning',
  APPLIED: 'success',
  EMPTY: 'info',
  FAILED: 'danger'
}[status] || 'info')

const diffStatusLabel = (status: string) => ({
  ADDED: '新增',
  CHANGED: '变更',
  UNCHANGED: '未变'
}[status] || '未知')

const diffTagType = (status: string) => ({
  ADDED: 'success',
  CHANGED: 'warning',
  UNCHANGED: 'info'
}[status] || 'info')

const activeTaskStatusLabel = (status: string) => ({
  IN_PROGRESS: '执行中',
  SUCCESS: '已完成',
  PARTIAL: '部分完成',
  FAILED: '失败'
}[status] || status)

const activeTaskStatusTagType = (status: string) => ({
  IN_PROGRESS: 'warning',
  SUCCESS: 'success',
  PARTIAL: 'info',
  FAILED: 'danger'
}[status] || 'info')

watch(() => appStore.currentDomainId, async (value) => {
  if (!value || value === currentDomainId.value) return
  currentDomainId.value = value
  result.value = null
  selectedEntityIds.value = []
  activeTaskId.value = ''
  activeTaskDetail.value = null
  stopTaskPolling()
  await Promise.all([loadEntities(), loadDataSources(), loadHistory(), loadLatestBlueprint()])
  syncActiveTaskFromHistory()
})

watch(() => form.value.mapping_instruction, (value: string) => {
  localStorage.setItem('bulkMappingInstruction', value)
})

onMounted(async () => {
  await loadAll()
})

onBeforeUnmount(() => {
  stopTaskPolling()
})
</script>

<style scoped>
.mapping-operation-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 110px);
}
.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
  border-radius: 16px;
  background: linear-gradient(135deg, #1b4668 0%, #2e709e 55%, #e4f0f7 100%);
  color: #fff;
}
.hero-title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 8px;
}
.hero-desc {
  max-width: 760px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.9);
}
.hero-stats {
  display: flex;
  gap: 12px;
}
.stat-card {
  min-width: 112px;
  padding: 14px 16px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.14);
  text-align: center;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
}
.stat-label {
  margin-top: 6px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.84);
}
.page-grid {
  display: grid;
  grid-template-columns: minmax(360px, 420px) minmax(520px, 1fr);
  gap: 16px;
  align-items: start;
}
.config-card,
.summary-card {
  border-radius: 16px;
  border: 1px solid #d9e4ee;
}
.config-card :deep(.el-select),
.config-card :deep(.el-input),
.config-card :deep(.el-textarea) {
  width: 100%;
}
.config-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.apply-scope-box {
  margin-top: 14px;
  padding: 12px;
  border-radius: 12px;
  background: #f8fbfe;
  border: 1px solid #dce6ee;
}
.apply-scope-title {
  margin-bottom: 8px;
  color: #244b73;
  font-size: 13px;
  font-weight: 600;
}
.long-task-tip {
  margin-top: 14px;
  padding: 12px;
  border-radius: 12px;
  background: #fff8eb;
  border: 1px solid #f2ddb4;
  color: #7b5a17;
  font-size: 13px;
  line-height: 1.6;
}
.active-task-box {
  margin-top: 14px;
  padding: 14px;
  border-radius: 14px;
  background: #f7fbff;
  border: 1px solid #d7e5f2;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.active-task-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.active-task-title {
  font-size: 14px;
  font-weight: 700;
  color: #234565;
}
.active-task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #61758c;
}
.active-task-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}
.active-task-item {
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid #e4edf5;
}
.active-task-item span {
  display: block;
  font-size: 12px;
  color: #74879b;
}
.active-task-item strong {
  display: block;
  margin-top: 4px;
  color: #234565;
}
.active-task-current {
  font-size: 13px;
  color: #47627c;
}
.active-task-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.result-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.history-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.design-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.design-item {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f7fbff;
  border: 1px solid #dbe7f1;
  color: #456074;
  line-height: 1.7;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.summary-item {
  padding: 12px 14px;
  border-radius: 12px;
  background: #fbfdff;
  border: 1px solid #e3ebf2;
}
.summary-item span {
  display: block;
  color: #6d7d8b;
  font-size: 12px;
}
.summary-item strong {
  display: block;
  margin-top: 6px;
  color: #274665;
  font-size: 18px;
}
.result-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fbfe;
  border: 1px solid #dce6ee;
}
.result-toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.result-toolbar-text {
  color: #6b7d8e;
  font-size: 12px;
}
.entity-result-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.diff-summary {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.entity-desc {
  color: #6d7b89;
  font-size: 13px;
}
.error-box {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff2f0;
  border: 1px solid #f5c2bd;
  color: #9f3022;
}
.subsection + .subsection {
  margin-top: 16px;
}
.subsection-title {
  margin-bottom: 8px;
  color: #244b73;
  font-size: 13px;
  font-weight: 600;
}
.oracle-graph-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
  color: #60758a;
  font-size: 12px;
}
.node-sql-box {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid #dce8f2;
  border-radius: 10px;
  background: #f8fbfe;
}
.node-sql-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  color: #526b82;
  font-size: 12px;
}
.node-sql-head strong {
  color: #244b73;
  font-size: 14px;
}
.node-design-reason {
  margin-bottom: 8px;
  color: #63788d;
  font-size: 12px;
  line-height: 1.6;
}
.relation-name {
  margin-bottom: 6px;
  color: #244b73;
  font-weight: 600;
}
.relation-join {
  margin-bottom: 8px;
  color: #516b82;
  line-height: 1.5;
}
.preview-relation-section {
  margin-top: 16px;
}
.candidate-table-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.candidate-table-item {
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fbfe;
  border: 1px solid #dce6ee;
}
.candidate-table-name {
  color: #224364;
  font-weight: 600;
}
.candidate-table-comment,
.candidate-table-columns {
  margin-top: 4px;
  color: #66788a;
  font-size: 12px;
  line-height: 1.6;
}
.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-compare-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.history-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f8fbfe;
  border: 1px solid #dce6ee;
}
.history-title {
  color: #214465;
  font-weight: 600;
}
.history-meta {
  margin-top: 4px;
  color: #6f8192;
  font-size: 12px;
}
.history-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.history-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.history-detail-actions {
  display: flex;
  justify-content: flex-end;
}
.history-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.history-detail-item {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f8fbfe;
  border: 1px solid #dce6ee;
}
.history-detail-item span {
  display: block;
  color: #6f8192;
  font-size: 12px;
}
.history-detail-item strong {
  display: block;
  margin-top: 6px;
  color: #214465;
}
.history-detail-section + .history-detail-section {
  margin-top: 4px;
}
.history-detail-title {
  margin-bottom: 8px;
  color: #244b73;
  font-size: 13px;
  font-weight: 600;
}
.preview-summary,
.compare-summary {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
@media (max-width: 1200px) {
  .page-grid {
    grid-template-columns: 1fr;
  }
  .hero-panel {
    flex-direction: column;
  }
  .history-item,
  .result-toolbar {
    flex-direction: column;
    align-items: flex-start;
  }
  .active-task-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .history-detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
