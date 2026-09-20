package com.his.server.service;

import com.his.common.dto.TimelineVO;

public interface TimelineService {
    TimelineVO getTimeline(Long patientId);
    TimelineVO getAdmissionTimeline(Long admissionId);
}
