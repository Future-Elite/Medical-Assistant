package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Visit;
import com.his.common.mapper.VisitMapper;
import com.his.server.service.VisitService;
import org.springframework.stereotype.Service;

@Service
public class VisitServiceImpl extends ServiceImpl<VisitMapper, Visit> implements VisitService {
}
