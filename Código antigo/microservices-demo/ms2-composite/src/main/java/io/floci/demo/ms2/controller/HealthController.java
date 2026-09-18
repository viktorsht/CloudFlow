package io.floci.demo.ms2.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Endpoint simples de healthcheck, separado do Actuator, para verificacao
 * rapida (manual ou via docker-compose healthcheck / load balancer) de que
 * o microsservico esta de pe e sabe dizer quem ele e e onde esta rodando.
 */
@RestController
public class HealthController {

    @Value("${app.provider}")
    private String provider;

    @Value("${app.ms-name}")
    private String msName;

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("ms_name", msName);
        body.put("provider", provider);
        body.put("timestamp", Instant.now().toString());
        return ResponseEntity.ok(body);
    }
}
