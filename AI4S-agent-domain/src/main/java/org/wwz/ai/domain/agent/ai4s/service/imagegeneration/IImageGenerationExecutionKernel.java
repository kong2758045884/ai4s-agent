package org.wwz.ai.domain.agent.ai4s.service.imagegeneration;

import org.wwz.ai.domain.agent.ai4s.model.imagegeneration.ImageGenerationExecuteCommand;
import org.wwz.ai.domain.agent.ai4s.model.imagegeneration.ImageGenerationExecutionResult;

/**
 * 生图执行内核。
 */
public interface IImageGenerationExecutionKernel {

    ImageGenerationExecutionResult execute(ImageGenerationExecuteCommand command);
}
