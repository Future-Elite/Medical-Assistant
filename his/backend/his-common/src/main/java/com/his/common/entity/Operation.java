package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/** 手术记录表：手术必须挂在一次住院下，时间必须落在该次住院区间内。 */
@Data
@TableName("operation")
public class Operation {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long admissionId;
    private Long patientId;
    private String operationCode;
    private String operationName;
    private Integer operationLevel;
    private String surgeon;
    private String assistant;
    private String anesthesiologist;
    private String anesthesiaType;
    private LocalDateTime startTime;
    private LocalDateTime endTime;
    private Integer status;
}
