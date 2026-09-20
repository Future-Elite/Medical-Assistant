package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("medical_order")
public class MedicalOrder {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long admissionId;
    private Integer orderType;
    private String itemName;
    private String dosage;
    private String frequency;
    private LocalDateTime startTime;
    private LocalDateTime stopTime;
    private Integer status;
    private String doctor;
}
