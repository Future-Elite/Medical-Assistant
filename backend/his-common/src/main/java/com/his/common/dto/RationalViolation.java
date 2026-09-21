package com.his.common.dto;

import lombok.Data;

@Data
public class RationalViolation {
    private Long patientId;
    private Long prescriptionId;
    private String ruleType;      // INDICATION / INTERACTION / DOSE
    private Integer warningLevel;
    private String message;
}
