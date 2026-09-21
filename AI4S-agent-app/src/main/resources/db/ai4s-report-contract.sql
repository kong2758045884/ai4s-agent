-- Keep the persisted report worker aligned with the runtime AI4S report skill.
-- This separate, idempotent patch runs after data.sql so existing seed rows do
-- not need to be duplicated and H2 development instances receive the same
-- contract as production.
UPDATE ai_agent_sub_agent_definition
SET system_prompt = CASE
        WHEN system_prompt LIKE '%事件概览、科学问题、技术路线%' THEN REPLACE(
            system_prompt,
            '事件概览、科学问题、技术路线、主要创新、论文团队与机构、前序工作、竞争路线、AI4S意义、待观察问题、来源证据',
            '事件概览、技术路线、主要创新、论文团队与机构、前序工作、竞争路线、AI4S意义、待观察问题、来源证据；科学问题、输入输出和评价指标写入“技术路线”，不得另增第十章'
        )
        WHEN system_prompt NOT LIKE '%ai4s-report-analysis%' THEN CONCAT(
            system_prompt,
            '\n\n## AI4S 研判报告契约\n正式报告必须先调用 skill_tool 加载 ai4s-report-analysis。报告只保留九个一级章节并按顺序输出：事件概览、技术路线、主要创新、论文团队、前序工作、竞争路线、AI4S意义、待观察问题、来源证据。科学问题、输入输出和评价指标写入“技术路线”，不得另增第十章。重要事实必须带可打开来源，不得把代理地址、密钥、异常堆栈或工具错误写入正文。'
        )
        ELSE system_prompt
    END,
    update_time = CURRENT_TIMESTAMP
WHERE agent_key = 'report_agent'
  AND (
      system_prompt LIKE '%事件概览、科学问题、技术路线%'
      OR system_prompt NOT LIKE '%ai4s-report-analysis%'
  );
