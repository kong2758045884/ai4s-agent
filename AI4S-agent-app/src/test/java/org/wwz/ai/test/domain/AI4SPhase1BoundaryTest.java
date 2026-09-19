package org.wwz.ai.test.domain;

import org.junit.Assert;
import org.junit.Test;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.AnnotatedElementUtils;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;

/**
 * 锁定 Phase 1 明确延后的共享配置边界。
 */
public class AI4SPhase1BoundaryTest {

    @Test
    public void shouldKeepAI4SConfigAsTransitionalSharedConfiguration() {
        Assert.assertEquals(
                "org.wwz.ai.domain.agent.ai4s.config",
                AI4SConfig.class.getPackageName()
        );
        Assert.assertNotNull(AnnotatedElementUtils.findMergedAnnotation(AI4SConfig.class, Configuration.class));
    }
}
