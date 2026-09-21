package com.his.common.dto;

import lombok.Data;
import java.util.List;

@Data
public class PathwayReport {
    private long enrolledAdmissions;
    private long totalSteps;
    private long fulfilledSteps;
    private double executionRate;
    private List<PathwayVariance> variances;
}
