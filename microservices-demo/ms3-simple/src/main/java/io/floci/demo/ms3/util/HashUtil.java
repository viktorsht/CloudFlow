package io.floci.demo.ms3.util;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.UUID;

public final class HashUtil {

    private HashUtil() {
    }

    /**
     * Gera um hash SHA-256 (hex) a partir do IP de origem, nome do microsservico,
     * timestamp atual e um UUID aleatorio, garantindo unicidade por requisicao.
     */
    public static String generateHash(String ip, String msName) {
        String raw = ip + "|" + msName + "|" + Instant.now() + "|" + UUID.randomUUID();
        return sha256Hex(raw);
    }

    public static String sha256Hex(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hashBytes = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(hashBytes.length * 2);
            for (byte b : hashBytes) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("Algoritmo SHA-256 indisponivel", e);
        }
    }
}
