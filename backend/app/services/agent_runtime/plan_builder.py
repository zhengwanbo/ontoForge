from typing import Any, Dict


class ManagedSkillPlanBuilder:
    def __init__(self, service: Any):
        self.service = service

    async def build(
        self,
        *,
        skill_markdown: str,
        skill_files: Dict[str, str],
        topology: Dict[str, Any],
        llm_config: Any,
        question: str,
        conversation_context: str,
        planning_feedback: str = "",
        excluded_root_labels: list[str] | None = None,
        excluded_target_labels: list[str] | None = None,
        preferred_root_label: str = "",
    ) -> Dict[str, Any]:
        query_guidance = self.service._build_skill_query_guidance(skill_markdown, skill_files)
        execution_contract = self.service._load_execution_contract(skill_files) or self.service._derive_execution_contract_from_topology(topology, skill_files)
        plan = await self.service._resolve_managed_skill_plan(
            skill_markdown=skill_markdown,
            skill_files=skill_files,
            question=question,
            conversation_context=conversation_context,
            topology=topology,
            llm_config=llm_config,
            skill_guidance=query_guidance,
            execution_contract=execution_contract,
            planning_feedback=planning_feedback,
            excluded_root_labels=excluded_root_labels or [],
            excluded_target_labels=excluded_target_labels or [],
            preferred_root_label=preferred_root_label,
        )
        selected_objects = plan.get("selected_objects") or []
        selected_node = {
            "displayName": " / ".join(selected_objects) or self.service._normalize_label_name((topology.get("nodes") or [{}])[0].get("displayName")),
            "tableName": "由受控计划编排器自动选择",
            "reason": plan.get("reason") or "根据问题和 Skill 语义选择起点对象与关联对象。",
        }
        return {
            "plan": plan,
            "selected_node": selected_node,
            "query_guidance": query_guidance,
            "execution_contract": execution_contract,
        }
