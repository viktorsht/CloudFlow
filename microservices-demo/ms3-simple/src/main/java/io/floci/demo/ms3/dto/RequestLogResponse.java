package io.floci.demo.ms3.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.floci.demo.ms3.entity.RequestLog;

import java.time.LocalDateTime;

public class RequestLogResponse {

    @JsonProperty("ip_request")
    private String ipRequest;

    @JsonProperty("provider")
    private String provider;

    @JsonProperty("ms_name")
    private String msName;

    @JsonProperty("hash")
    private String hash;

    @JsonProperty("created_at")
    @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss.SSS")
    private LocalDateTime createdAt;

    public static RequestLogResponse fromEntity(RequestLog entity) {
        RequestLogResponse response = new RequestLogResponse();
        response.setIpRequest(entity.getIpRequest());
        response.setProvider(entity.getProvider());
        response.setMsName(entity.getMsName());
        response.setHash(entity.getHash());
        response.setCreatedAt(entity.getCreatedAt());
        return response;
    }

    public String getIpRequest() {
        return ipRequest;
    }

    public void setIpRequest(String ipRequest) {
        this.ipRequest = ipRequest;
    }

    public String getProvider() {
        return provider;
    }

    public void setProvider(String provider) {
        this.provider = provider;
    }

    public String getMsName() {
        return msName;
    }

    public void setMsName(String msName) {
        this.msName = msName;
    }

    public String getHash() {
        return hash;
    }

    public void setHash(String hash) {
        this.hash = hash;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
