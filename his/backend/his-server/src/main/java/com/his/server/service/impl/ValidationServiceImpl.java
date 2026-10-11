package com.his.server.service.impl;

import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import com.his.server.service.validation.PathwayValidator;
import com.his.server.service.validation.RationalDrugValidator;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ValidationServiceImpl implements ValidationService {

    private final RationalDrugValidator rationalDrugValidator;
    private final PathwayValidator pathwayValidator;

    @Override
    public ValidationReport run() {
        ValidationReport report = new ValidationReport();
        report.setRationalDrug(rationalDrugValidator.validate());
        report.setPathway(pathwayValidator.validate());
        return report;
    }
}
