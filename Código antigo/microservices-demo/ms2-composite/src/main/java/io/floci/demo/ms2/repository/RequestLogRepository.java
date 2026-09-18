package io.floci.demo.ms2.repository;

import io.floci.demo.ms2.entity.RequestLog;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface RequestLogRepository extends JpaRepository<RequestLog, UUID> {
}
