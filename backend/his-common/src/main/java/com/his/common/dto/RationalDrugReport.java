package com.his.common.dto;

import lombok.Data;
import java.util.List;

@Data
public class RationalDrugReport {
    private long totalPrescriptions;
    private long violationPrescriptions;
    private double rationalRate;
    private List<RationalViolation> violations;
}
