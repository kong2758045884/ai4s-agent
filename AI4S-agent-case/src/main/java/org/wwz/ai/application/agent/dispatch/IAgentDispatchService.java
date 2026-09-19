package org.wwz.ai.application.agent.dispatch;

import org.wwz.ai.application.agent.stream.AgentSessionStream;
import org.wwz.ai.domain.agent.ai4s.model.req.AgentRequest;

/**
 * Agent 应用层调度接口。
 */
public interface IAgentDispatchService {

    void dispatch(AgentRequest request, AgentSessionStream stream) throws Exception;
}
