package org.wwz.ai.domain.agent.runtime.agent;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.commons.lang3.StringUtils;

/** Parse execution arguments without replacing malformed payloads with an empty object. */
final class ToolArgumentsJson {

    private static final ObjectMapper JSON = new ObjectMapper();

    private ToolArgumentsJson() {
    }

    static Object parseOriginal(String arguments) throws JsonProcessingException {
        return JSON.readValue(StringUtils.isBlank(arguments) ? "{}" : arguments, Object.class);
    }
}
