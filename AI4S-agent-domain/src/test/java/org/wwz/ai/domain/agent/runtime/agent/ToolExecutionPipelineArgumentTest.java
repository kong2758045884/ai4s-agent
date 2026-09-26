package org.wwz.ai.domain.agent.runtime.agent;

import com.fasterxml.jackson.core.JsonProcessingException;
import org.junit.Assert;
import org.junit.Test;

import java.util.Map;

public class ToolExecutionPipelineArgumentTest {

    @Test
    public void malformedLongWorkspaceWriteMustNotSilentlyBecomeEmptyArguments() throws Exception {
        Map<?, ?> valid = (Map<?, ?>) ToolArgumentsJson.parseOriginal(
                "{\"path\":\"report/example.md\",\"content\":\"ready\"}");
        Assert.assertEquals("report/example.md", valid.get("path"));

        try {
            ToolArgumentsJson.parseOriginal(
                    "{\"path\":\"report/example.md\",\"content\":\"truncated");
            Assert.fail("Malformed JSON must fail before the write tool is executed");
        } catch (JsonProcessingException expected) {
            Assert.assertNotNull(expected.getOriginalMessage());
        }
    }
}
