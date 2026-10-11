package com.his.common.dto;

import lombok.Data;

@Data
public class PathwayVariance {
    private Long patientId;
    private Long admissionId;
    private String diagnosisName;
    private String varianceType;  // MISSING / WINDOW
    private String detail;
}
