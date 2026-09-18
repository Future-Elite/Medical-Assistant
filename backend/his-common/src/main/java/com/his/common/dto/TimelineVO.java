package com.his.common.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import java.util.List;

@Data
@AllArgsConstructor
public class TimelineVO {
    private Long patientId;
    private List<TimelineEvent> events;
}
