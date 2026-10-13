// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

package com.azure.ai.agents.voice;

import com.azure.ai.agents.AgentsAsyncClient;
import com.azure.ai.agents.BetaVoiceAgentsConversationsAsyncClient;
import com.azure.ai.agents.models.VoiceConversation;
import com.azure.ai.agents.models.VoiceConversationStatus;
import com.azure.core.exception.ResourceNotFoundException;
import com.azure.core.http.rest.RequestOptions;
import com.azure.core.util.BinaryData;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeoutException;

final class VoiceAgentRealtimeSampleUtils {
    static final int SAMPLE_RATE = 24000;
    private static final Duration PERSISTENCE_POLL_INTERVAL = Duration.ofSeconds(1);
    private static final Duration PERSISTENCE_TIMEOUT = Duration.ofMinutes(1);

    private VoiceAgentRealtimeSampleUtils() {
    }

    static Mono<Void> requireUnusedAgentName(AgentsAsyncClient client, String agentName) {
        return client.getAgent(agentName)
            .flatMap(agent -> Mono.<Void>error(new IllegalArgumentException(
                "Agent already exists; use a disposable FOUNDRY_VOICE_AGENT_NAME: " + agentName)))
            .onErrorResume(ResourceNotFoundException.class, ignored -> Mono.empty());
    }

    static Mono<Void> readConversation(BetaVoiceAgentsConversationsAsyncClient conversations, String agentName,
        String conversationId) {
        return waitForConversationPersistence(conversations, agentName, conversationId)
            .doOnNext(conversation -> System.out.printf("Conversation %s: status=%s, created=%s%n",
                conversation.getId(), conversation.getStatus(), conversation.getCreatedAt()))
            .thenMany(conversations.listAgentConversationItems(agentName, conversationId, new RequestOptions()))
            .doOnNext(VoiceAgentRealtimeSampleUtils::printConversationItem)
            .then();
    }

    private static Mono<VoiceConversation> waitForConversationPersistence(
        BetaVoiceAgentsConversationsAsyncClient conversations, String agentName, String conversationId) {
        return Mono.defer(() -> conversations.getAgentConversation(agentName, conversationId))
            .onErrorResume(ResourceNotFoundException.class, ignored -> Mono.empty())
            .flatMap(conversation -> {
                if (VoiceConversationStatus.COMPLETED.equals(conversation.getStatus())) {
                    return Mono.just(conversation);
                }
                if (VoiceConversationStatus.FAILED.equals(conversation.getStatus())) {
                    return Mono.error(new IllegalStateException(
                        "Persistence failed for conversation " + conversationId + "."));
                }
                return Mono.empty();
            })
            .repeatWhenEmpty(repeat -> repeat.delayElements(PERSISTENCE_POLL_INTERVAL))
            .timeout(PERSISTENCE_TIMEOUT, Mono.error(new TimeoutException(
                "Conversation " + conversationId + " was not persisted within one minute.")));
    }

    @SuppressWarnings("unchecked")
    private static void printConversationItem(BinaryData itemData) {
        Map<String, Object> item = itemData.toObject(Map.class);
        System.out.printf("  - %s id=%s%n", item.get("role") == null ? item.get("type") : item.get("role"),
            item.get("id"));
        Object contentValue = item.get("content");
        if (!(contentValue instanceof List<?>)) {
            return;
        }
        StringBuilder transcript = new StringBuilder();
        for (Object partValue : (List<?>) contentValue) {
            if (partValue instanceof Map<?, ?>) {
                Map<String, Object> part = (Map<String, Object>) partValue;
                Object text = part.get("transcript") == null ? part.get("text") : part.get("transcript");
                if (text != null && !text.toString().trim().isEmpty()) {
                    if (transcript.length() > 0) {
                        transcript.append(' ');
                    }
                    transcript.append(text.toString().trim());
                }
            }
        }
        if (transcript.length() > 0) {
            System.out.println("      " + transcript);
        }
    }
}
