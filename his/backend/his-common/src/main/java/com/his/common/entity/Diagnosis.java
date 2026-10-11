package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("diagnosis")
public class Diagnosis {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private String icdCode;
    private String diagnosisName;
    private String doctor;
    private LocalDateTime diagnosedAt;
}
