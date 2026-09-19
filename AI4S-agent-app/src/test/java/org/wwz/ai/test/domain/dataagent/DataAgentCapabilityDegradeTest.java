package org.wwz.ai.test.domain.dataagent;

import org.junit.Assert;
import org.junit.Test;
import org.mockito.Mockito;
import org.springframework.test.util.ReflectionTestUtils;
import org.wwz.ai.application.agent.dataquery.DataAgentApplicationService;
import org.wwz.ai.application.agent.dataquery.IDataAgentApplicationService;
import org.wwz.ai.config.ai4s.DataAgentInitRunner;
import org.wwz.ai.domain.agent.runtime.AI4SRuntimeDependencies;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;
import org.wwz.ai.domain.agent.ai4s.config.data.DataAgentConfig;
import org.wwz.ai.domain.agent.ai4s.config.data.EsConfig;
import org.wwz.ai.domain.agent.ai4s.config.data.QdrantConfig;
import org.wwz.ai.domain.agent.ai4s.service.ChatModelInfoService;
import org.wwz.ai.domain.agent.ai4s.service.ColumnValueSyncService;
import org.wwz.ai.domain.agent.ai4s.service.EmbeddingService;
import org.wwz.ai.domain.agent.ai4s.service.QdrantService;
import org.wwz.ai.infrastructure.adapter.port.OkHttpRemoteHttpAdapter;
import org.wwz.ai.infrastructure.adapter.port.OkHttpRemoteStreamAdapter;
import org.wwz.ai.infrastructure.adapter.port.AI4SToolFileArtifactAdapter;
import org.wwz.ai.test.domain.support.AI4SRuntimeTestSupport;
import org.wwz.ai.trigger.http.dataagent.DataAgentController;

import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 能力降级测试。
 */
public class DataAgentCapabilityDegradeTest {

    @Test
    public void shouldInjectCaseSeamIntoDataAgentController() {
        List<String> fieldTypes = Arrays.stream(DataAgentController.class.getDeclaredFields())
                .map(field -> field.getType().getName())
                .collect(Collectors.toList());

        Assert.assertTrue(fieldTypes.contains(IDataAgentApplicationService.class.getName()));
        Assert.assertFalse(fieldTypes.contains("org.wwz.ai.domain.agent.ai4s.service.DataAgentService"));
    }

    @Test
    public void shouldInjectStableDomainSeamIntoDataAgentApplicationService() {
        List<String> fieldTypes = Arrays.stream(DataAgentApplicationService.class.getDeclaredFields())
                .map(field -> field.getType().getName())
                .collect(Collectors.toList());

        Assert.assertTrue(fieldTypes.contains("org.wwz.ai.domain.agent.rag.DataAgentQueryService"));
        Assert.assertFalse(fieldTypes.contains("org.wwz.ai.domain.agent.ai4s.service.DataAgentService"));
        Assert.assertFalse(fieldTypes.contains("org.wwz.ai.domain.agent.ai4s.service.Nl2SqlService"));
        Assert.assertFalse(fieldTypes.contains("org.wwz.ai.domain.agent.ai4s.service.ChatModelInfoService"));
        Assert.assertFalse(fieldTypes.contains("org.wwz.ai.domain.agent.rag.SchemaRecallService"));
    }

    @Test
    public void shouldAssembleInfrastructureOwnedRuntimeAdapters() {
        AI4SRuntimeDependencies dependencies = AI4SRuntimeTestSupport.runtimeDependencies(new AI4SConfig());

        Assert.assertTrue(dependencies.requireRemoteHttpPort() instanceof OkHttpRemoteHttpAdapter);
        Assert.assertTrue(dependencies.requireRemoteStreamPort() instanceof OkHttpRemoteStreamAdapter);
        Assert.assertTrue(dependencies.requireFileArtifactPort() instanceof AI4SToolFileArtifactAdapter);
    }

    @Test
    public void shouldDisableEsWhenRegularStartupInitFails() throws Exception {
        DataAgentInitRunner runner = new DataAgentInitRunner();
        DataAgentConfig dataAgentConfig = new DataAgentConfig();
        QdrantConfig qdrantConfig = new QdrantConfig();
        qdrantConfig.setEnable(false);
        EsConfig esConfig = new EsConfig();
        esConfig.setEnable(true);
        dataAgentConfig.setQdrantConfig(qdrantConfig);
        dataAgentConfig.setEsConfig(esConfig);
        dataAgentConfig.setForceRefresh(false);

        ColumnValueSyncService columnValueSyncService = Mockito.mock(ColumnValueSyncService.class);
        Mockito.doThrow(new IllegalStateException("es init failed")).when(columnValueSyncService).initColumnValueIndex();

        ReflectionTestUtils.setField(runner, "dataAgentConfig", dataAgentConfig);
        ReflectionTestUtils.setField(runner, "qdrantService", Mockito.mock(QdrantService.class));
        ReflectionTestUtils.setField(runner, "chatModelInfoService", Mockito.mock(ChatModelInfoService.class));
        ReflectionTestUtils.setField(runner, "columnValueSyncService", columnValueSyncService);
        ReflectionTestUtils.setField(runner, "embeddingService", Mockito.mock(EmbeddingService.class));

        runner.run();

        Assert.assertFalse(dataAgentConfig.getEsConfig().getEnable());
    }
}
