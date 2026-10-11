package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("visit")
public class Visit {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private String visitNo;
    private String department;
    private String doctor;
    private LocalDateTime visitTime;
    private Integer status;
    private String remark;
}
