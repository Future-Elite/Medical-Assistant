package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Prescription;
import com.his.common.entity.PrescriptionItem;
import java.util.List;

public interface PrescriptionService extends IService<Prescription> {
    Prescription saveWithItems(Prescription prescription, List<PrescriptionItem> items);
}
