package io.floci.demo.ms3.util;

import jakarta.servlet.http.HttpServletRequest;

public final class IpUtil {

    private IpUtil() {
    }

    /**
     * Extrai o IP real do cliente. Prioriza X-Forwarded-For (setado pelo
     * API Gateway) e cai para o IP remoto direto caso a chamada nao passe pelo gateway.
     */
    public static String extractClientIp(HttpServletRequest request) {
        String xff = request.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            return xff.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
