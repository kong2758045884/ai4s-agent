package org.wwz.ai.domain.agent.ledger.model.tooloutput;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * deep_search 文档摘要。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class DeepSearchDoc {

    private String title;

    private String link;

    private String summary;

    private Map<String, Object> metadata = new LinkedHashMap<>();

    /**
     * 统一工厂，避免运行时依赖 Lombok Builder 内部类。
     */
    public static DeepSearchDoc of(String title, String link, String summary) {
        return of(title, link, summary, null);
    }

    public static DeepSearchDoc of(String title,
                                   String link,
                                   String summary,
                                   Map<String, Object> metadata) {
        return new DeepSearchDoc(title, link, summary,
                metadata == null ? new LinkedHashMap<>() : new LinkedHashMap<>(metadata));
    }
}
