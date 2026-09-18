package io.floci.demo.ms3.controller;

import io.floci.demo.ms3.dto.RequestLogResponse;
import io.floci.demo.ms3.service.RequestService;
import io.floci.demo.ms3.util.IpUtil;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class RequestController {

    private final RequestService service;

    public RequestController(RequestService service) {
        this.service = service;
    }

    @PostMapping("/process")
    public ResponseEntity<RequestLogResponse> process(HttpServletRequest request) {
        String ip = IpUtil.extractClientIp(request);
        return ResponseEntity.ok(service.process(ip));
    }
}
