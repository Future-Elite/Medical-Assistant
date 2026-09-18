package com.his.common.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@AllArgsConstructor
public class TimelineEvent {
    private String type;
    private LocalDateTime time;
    private Object data;
}
