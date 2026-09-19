package org.wwz.ai.test.domain.support;

import org.springframework.core.env.Environment;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.scheduling.concurrent.ConcurrentTaskScheduler;
import org.springframework.test.util.ReflectionTestUtils;
import org.wwz.ai.domain.agent.adapter.port.FileArtifactPort;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpPort;
import org.wwz.ai.domain.agent.adapter.port.RemoteStreamPort;
import org.wwz.ai.domain.agent.runtime.llm.DomainMessageConverter;
import org.wwz.ai.domain.agent.runtime.llm.LlmChatModelResolver;
import org.wwz.ai.domain.agent.runtime.llm.LlmChatResponseMapper;
import org.wwz.ai.domain.agent.runtime.llm.LlmToolCallbackProvider;
import org.wwz.ai.domain.agent.runtime.llm.OpenAiChatOptionsFactory;
import org.wwz.ai.domain.agent.runtime.llm.StreamResponseHandler;
import org.wwz.ai.domain.agent.runtime.tool.mcp.runtime.McpRegistry;
import org.wwz.ai.domain.agent.runtime.tool.mcp.runtime.McpToolExecutor;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;
import org.wwz.ai.domain.agent.runtime.AI4SLlmDependencies;
import org.wwz.ai.domain.agent.runtime.AI4SRuntimeDependencies;
import org.wwz.ai.domain.agent.ai4s.service.imagegeneration.IImageGenerationExecutionKernel;
import org.wwz.ai.infrastructure.adapter.port.OkHttpRemoteHttpAdapter;
import org.wwz.ai.infrastructure.adapter.port.OkHttpRemoteStreamAdapter;
import org.wwz.ai.infrastructure.adapter.port.AI4SToolFileArtifactAdapter;

import java.util.concurrent.Executor;

/**
 * AI4S 运行时测试夹具。
 * 统一为单测构造最小可用的 AI4SRuntimeDependencies，避免测试回退到全局 Spring 上下文。
 */
public final class AI4SRuntimeTestSupport {

    private AI4SRuntimeTestSupport() {
    }

    public static AI4SRuntimeDependencies runtimeDependencies(AI4SConfig ai4sConfig) {
        return runtimeDependencies(ai4sConfig, null, new MockEnvironment(), null);
    }

    public static AI4SRuntimeDependencies runtimeDependencies(AI4SConfig ai4sConfig,
                                                                  RemoteHttpPort remoteHttpPort) {
        return runtimeDependencies(ai4sConfig, null, new MockEnvironment(), remoteHttpPort);
    }

    public static AI4SRuntimeDependencies runtimeDependencies(AI4SConfig ai4sConfig,
                                                                  IImageGenerationExecutionKernel imageKernel) {
        return runtimeDependencies(ai4sConfig, imageKernel, new MockEnvironment(), null);
    }

    public static AI4SRuntimeDependencies runtimeDependencies(AI4SConfig ai4sConfig,
                                                                  IImageGenerationExecutionKernel imageKernel,
                                                                  Environment environment) {
        return runtimeDependencies(ai4sConfig, imageKernel, environment, null);
    }

    public static AI4SRuntimeDependencies runtimeDependencies(AI4SConfig ai4sConfig,
                                                                  IImageGenerationExecutionKernel imageKernel,
                                                                  Environment environment,
                                                                  RemoteHttpPort overrideRemoteHttpPort) {
        DomainMessageConverter messageConverter = new DomainMessageConverter();
        ReflectionTestUtils.setField(messageConverter, "ai4sConfig", ai4sConfig);

        LlmToolCallbackProvider toolCallbackProvider = new LlmToolCallbackProvider();
        ReflectionTestUtils.setField(toolCallbackProvider, "mcpRegistry", org.mockito.Mockito.mock(McpRegistry.class));

        OpenAiChatOptionsFactory chatOptionsFactory = new OpenAiChatOptionsFactory();
        ReflectionTestUtils.setField(chatOptionsFactory, "toolCallbackProvider", toolCallbackProvider);

        StreamResponseHandler streamResponseHandler = new StreamResponseHandler();
        LlmChatResponseMapper responseMapper = new LlmChatResponseMapper();
        ReflectionTestUtils.setField(streamResponseHandler, "ai4sConfig", ai4sConfig);
        ReflectionTestUtils.setField(streamResponseHandler, "chatResponseMapper", responseMapper);
        AI4SLlmDependencies llmDependencies = AI4SLlmDependencies.builder()
                .chatModelResolver(new LlmChatModelResolver())
                .chatOptionsFactory(chatOptionsFactory)
                .messageConverter(messageConverter)
                .responseMapper(responseMapper)
                .streamResponseHandler(streamResponseHandler)
                .build();
        RemoteHttpPort remoteHttpPort = overrideRemoteHttpPort != null
                ? overrideRemoteHttpPort
                : new OkHttpRemoteHttpAdapter();
        RemoteStreamPort remoteStreamPort = new OkHttpRemoteStreamAdapter();
        FileArtifactPort fileArtifactPort = new AI4SToolFileArtifactAdapter(
                overrideRemoteHttpPort != null ? overrideRemoteHttpPort : new OkHttpRemoteHttpAdapter());
        Executor sameThreadExecutor = Runnable::run;

        return AI4SRuntimeDependencies.builder()
                .ai4sConfig(ai4sConfig)
                .environment(environment)
                .llmDependencies(llmDependencies)
                .mcpToolExecutor(null)
                .imageGenerationExecutionKernel(imageKernel)
                .remoteHttpPort(remoteHttpPort)
                .remoteStreamPort(remoteStreamPort)
                .fileArtifactPort(fileArtifactPort)
                .llmExecutor(sameThreadExecutor)
                .taskExecutor(sameThreadExecutor)
                .toolExecutor(sameThreadExecutor)
                .heartbeatScheduler(new ConcurrentTaskScheduler())
                .build();
    }
}
