package org.wwz.ai.domain.agent.ai4s.gateway;

import org.wwz.ai.domain.agent.ai4s.model.imagegeneration.ImageGenerationGatewayRequest;
import org.wwz.ai.domain.agent.ai4s.model.imagegeneration.ImageGenerationGatewayResponse;

/**
 * 生图工作台下游调用端口。
 */
public interface IAI4SImageGenerationGateway {

    /**
     * 调用下游图片生成服务。
     */
    ImageGenerationGatewayResponse generate(ImageGenerationGatewayRequest request);
}
