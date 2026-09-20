package com.his.server.service;

import com.his.common.entity.Patient;
import java.util.List;
import java.util.Map;

public interface DashboardService {
    Patient randomPatient();
    List<Map<String, Object>> visitFrequency();
    List<Map<String, Object>> drugDistribution();
    Map<String, Object> admissionRatio();
    Map<String, Object> rationalDrugRate();
    Map<String, Object> pathwayRate();
}
