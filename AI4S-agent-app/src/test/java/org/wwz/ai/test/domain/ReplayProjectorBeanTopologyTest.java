package org.wwz.ai.test.domain;

import org.junit.Assert;
import org.junit.Test;
import org.mockito.Mockito;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.wwz.ai.config.ai4s.ReplayProjectorAutoConfiguration;
import org.wwz.ai.config.ai4s.DataAgentInitRunner;
import org.wwz.ai.config.ai4s.data.Es7HighLevelClientConfig;
import org.wwz.ai.domain.agent.ledger.IExecutionLedgerReadRepository;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;
import org.wwz.ai.domain.agent.ledger.ExecutionLedgerQueryService;
import org.wwz.ai.domain.agent.ledger.impl.ExecutionLedgerQueryServiceImpl;
import org.wwz.ai.domain.agent.ledger.replay.ConversationHistoryReplayService;
import org.wwz.ai.domain.agent.ledger.tooloutput.ToolOutputReader;
import org.wwz.ai.infrastructure.adapter.repository.ExecutionLedgerReadRepository;
import org.wwz.ai.infrastructure.dao.ai4s.IArtifactLedgerDao;
import org.wwz.ai.infrastructure.dao.ai4s.IDialogueRunLedgerDao;
import org.wwz.ai.infrastructure.dao.ai4s.IDialogueSessionLedgerDao;
import org.wwz.ai.infrastructure.dao.ai4s.ILlmInvocationLedgerDao;
import org.wwz.ai.infrastructure.dao.ai4s.IToolInvocationLedgerDao;
import org.wwz.ai.trigger.http.agent.AgentConversationHistoryController;

import java.lang.reflect.Field;

/**
 * 验证 Phase 1 迁出的 AI4S 装配仍能在 app 层稳定提供 Bean。
 */
public class ReplayProjectorBeanTopologyTest {

    @Test
    public void shouldWireHistoryBeansFromAppOwnedConfiguration() {
        Assert.assertTrue(ReplayProjectorAutoConfiguration.class.getPackageName().startsWith("org.wwz.ai.config"));
        Assert.assertTrue(Es7HighLevelClientConfig.class.getPackageName().startsWith("org.wwz.ai.config"));
        Assert.assertTrue(DataAgentInitRunner.class.getPackageName().startsWith("org.wwz.ai.config"));
        Assert.assertTrue(AI4SConfig.class.getPackageName().startsWith("org.wwz.ai.domain.agent.ai4s.config"));

        AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext();
        context.register(ReplayProjectorAutoConfiguration.class);
        context.register(ExecutionLedgerQueryServiceImpl.class);
        context.register(ExecutionLedgerReadRepository.class);
        context.register(AgentConversationHistoryController.class);
        context.register(TestDependencyConfiguration.class);

        try {
            context.refresh();

            Assert.assertNotNull(context.getBean(ExecutionLedgerQueryService.class));
            Assert.assertNotNull(context.getBean(IExecutionLedgerReadRepository.class));
            Assert.assertNotNull(context.getBean(ConversationHistoryReplayService.class));
            Assert.assertNotNull(context.getBean(AgentConversationHistoryController.class));
        } finally {
            context.close();
        }
    }

    @Test
    public void shouldLimitAppOwnedDeferredLegacyContractsToDocumentedConfigAndMetadataServices() {
        assertDeclaredFieldTypes(DataAgentInitRunner.class,
                "org.wwz.ai.domain.agent.ai4s.config.data.DataAgentConfig",
                "org.wwz.ai.domain.agent.ai4s.service.QdrantService",
                "org.wwz.ai.domain.agent.ai4s.service.ChatModelInfoService",
                "org.wwz.ai.domain.agent.ai4s.service.ColumnValueSyncService",
                "org.wwz.ai.domain.agent.ai4s.service.EmbeddingService");
        assertDeclaredFieldTypes(Es7HighLevelClientConfig.class,
                "org.wwz.ai.domain.agent.ai4s.config.data.DataAgentConfig");
    }

    private void assertDeclaredFieldTypes(Class<?> type, String... expectedTypes) {
        java.util.List<String> fieldTypes = java.util.Arrays.stream(type.getDeclaredFields())
                .map(Field::getType)
                .map(Class::getName)
                .toList();
        for (String expectedType : expectedTypes) {
            Assert.assertTrue(type.getSimpleName() + " 应显式登记延期 legacy 契约: " + expectedType,
                    fieldTypes.contains(expectedType));
        }
    }

    @Configuration
    static class TestDependencyConfiguration {

        @Bean
        public IDialogueRunLedgerDao dialogueRunLedgerDao() {
            return Mockito.mock(IDialogueRunLedgerDao.class);
        }

        @Bean
        public IDialogueSessionLedgerDao dialogueSessionLedgerDao() {
            return Mockito.mock(IDialogueSessionLedgerDao.class);
        }

        @Bean
        public ILlmInvocationLedgerDao llmInvocationLedgerDao() {
            return Mockito.mock(ILlmInvocationLedgerDao.class);
        }

        @Bean
        public IToolInvocationLedgerDao toolInvocationLedgerDao() {
            return Mockito.mock(IToolInvocationLedgerDao.class);
        }

        @Bean
        public IArtifactLedgerDao artifactLedgerDao() {
            return Mockito.mock(IArtifactLedgerDao.class);
        }

        @Bean
        public ToolOutputReader toolOutputReader() {
            return Mockito.mock(ToolOutputReader.class);
        }
    }
}
