package org.wwz.ai.infrastructure.adapter.port;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.wwz.ai.domain.agent.adapter.port.ConversationFilePort;
import org.wwz.ai.domain.agent.model.valobj.ConversationUploadedFile;
import org.wwz.ai.infrastructure.gateway.AI4SFileGateway;
import org.wwz.ai.infrastructure.gateway.dto.ConversationUploadFileDTO;

import java.io.InputStream;

/**
 * 会话附件上传端口适配：转发到 AI4SFileGateway。
 */
@Component
@RequiredArgsConstructor
public class ConversationFilePortAdapter implements ConversationFilePort {

    private final AI4SFileGateway ai4sFileGateway;

    @Override
    public ConversationUploadedFile upload(String sessionId,
                                           String originalFileName,
                                           String contentType,
                                           long size,
                                           InputStream content) {
        ConversationUploadFileDTO dto = ai4sFileGateway.uploadConversationFile(
                sessionId, originalFileName, contentType, size, content);
        return ConversationUploadedFile.builder()
                .name(dto.getName())
                .url(dto.getUrl())
                .type(dto.getType())
                .size(dto.getSize())
                .downloadUrl(dto.getDownloadUrl())
                .previewUrl(dto.getPreviewUrl())
                .resourceKey(dto.getResourceKey())
                .mimeType(dto.getMimeType())
                .originFileName(dto.getOriginFileName())
                .build();
    }
}
