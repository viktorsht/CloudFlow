package io.floci.demo.ms2.service;

import io.floci.demo.ms2.dto.RequestLogResponse;
import io.floci.demo.ms2.entity.RequestLog;
import io.floci.demo.ms2.repository.RequestLogRepository;
import io.floci.demo.ms2.util.HashUtil;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.time.LocalDateTime;

/**
 * Servico de um microsservico COMPOSTO: recebe a requisicao, calcula seu
 * proprio hash, chama outro microsservico (MS3) ATRAVES DO API GATEWAY
 * (nao diretamente no container do MS3), concatena o hash proprio com o
 * hash recebido, e so entao persiste e responde.
 *
 * Chamar via gateway (em vez de host:porta fixos do MS3) desacopla o MS2
 * de onde/como o MS3 esta hospedado: trocar o MS3 de provedor de nuvem,
 * reescreve-lo, escala-lo ou substitui-lo por outro MS nao exige nenhuma
 * mudanca aqui - so na configuracao de rota do gateway.
 */
@Service
public class RequestService {

    private final RequestLogRepository repository;
    private final RestTemplate restTemplate;

    @Value("${app.provider}")
    private String provider;

    @Value("${app.ms-name}")
    private String msName;

    @Value("${app.ms3-url}")
    private String ms3Url;

    public RequestService(RequestLogRepository repository, RestTemplate restTemplate) {
        this.repository = repository;
        this.restTemplate = restTemplate;
    }

    public RequestLogResponse process(String ip) {
        String ownHash = HashUtil.generateHash(ip, msName);
        String ms3Hash = callMs3(ip);

        // Requisito: concatenar o hash criado (proprio) com o hash recebido (de ms3)
        String finalHash = ownHash + ms3Hash;

        RequestLog entity = new RequestLog();
        entity.setIpRequest(ip);
        entity.setProvider(provider);
        entity.setMsName(msName);
        entity.setHash(finalHash);
        entity.setCreatedAt(LocalDateTime.now());

        RequestLog saved = repository.save(entity);
        return RequestLogResponse.fromEntity(saved);
    }

    /**
     * Chama o MS3 passando pelo API Gateway (ms3Url aponta para a rota
     * /ms3/** do gateway, nao para o container do MS3 diretamente).
     * O gateway acrescenta o IP deste container (MS2) ao header
     * X-Forwarded-For, mantendo o IP original do cliente como primeiro
     * valor da lista - por isso o MS3 continua enxergando o IP correto.
     */
    private String callMs3(String ip) {
        HttpHeaders headers = new HttpHeaders();
        // repassa o IP original do cliente; o gateway acrescenta o dele na cadeia
        headers.set("X-Forwarded-For", ip);
        HttpEntity<Void> requestEntity = new HttpEntity<>(headers);

        try {
            ResponseEntity<RequestLogResponse> response = restTemplate.exchange(
                    ms3Url, HttpMethod.POST, requestEntity, RequestLogResponse.class);

            RequestLogResponse body = response.getBody();
            if (body == null || body.getHash() == null) {
                throw new IllegalStateException("MS3 respondeu sem hash");
            }
            return body.getHash();
        } catch (RestClientException e) {
            throw new IllegalStateException("Falha ao chamar MS3 (via gateway) em " + ms3Url, e);
        }
    }
}
