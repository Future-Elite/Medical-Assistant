package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.MedicalOrder;

public interface MedicalOrderService extends IService<MedicalOrder> {
    /** 停长期医嘱：置 stop_time=now、status=1。返回 null 表示不存在。 */
    MedicalOrder stop(Long id);
}
