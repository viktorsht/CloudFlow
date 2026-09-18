package io.floci.demo.ms3.service;

import io.floci.demo.ms3.dto.RequestLogResponse;
import io.floci.demo.ms3.entity.RequestLog;
import io.floci.demo.ms3.repository.RequestLogRepository;
import io.floci.demo.ms3.util.HashUtil;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

/**
 * Servico de um microsservico SIMPLES: recebe a requisicao, calcula o hash
 * e persiste o registro, retornando os dados salvos.
 */
@Service
public class RequestService {

    private final RequestLogRepository repository;

    @Value("${app.provider}")
    private String provider;

    @Value("${app.ms-name}")
    private String msName;

    public RequestService(RequestLogRepository repository) {
        this.repository = repository;
    }

    public RequestLogResponse process(String ip) {
        String hash = HashUtil.generateHash(ip, msName);

        RequestLog entity = new RequestLog();
        entity.setIpRequest(ip);
        entity.setProvider(provider);
        entity.setMsName(msName);
        entity.setHash(hash);
        entity.setCreatedAt(LocalDateTime.now());

        RequestLog saved = repository.save(entity);
        return RequestLogResponse.fromEntity(saved);
    }
}
