package com.his.common.dto;

import lombok.Data;

@Data
public class ValidationReport {
    private RationalDrugReport rationalDrug;
    private PathwayReport pathway;
}
