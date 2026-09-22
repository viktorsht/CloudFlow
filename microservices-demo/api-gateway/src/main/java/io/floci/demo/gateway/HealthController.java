package io.floci.demo.gateway.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Endpoint simples de healthcheck do proprio gateway, separado do Actuator.
 * Uteis para checar rapidamente se o gateway (a "porta de entrada" do
 * sistema) esta de pe, alem de checar cada microsservico individualmente
 * em /ms1/health, /ms2/health e /ms3/health (roteados automaticamente).
 */
@RestController
public class HealthController {

    @GetMapping("/health")
    public Mono<Map<String, Object>> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("ms_name", "api-gateway");
        body.put("timestamp", Instant.now().toString());
        return Mono.just(body);
    }
}
