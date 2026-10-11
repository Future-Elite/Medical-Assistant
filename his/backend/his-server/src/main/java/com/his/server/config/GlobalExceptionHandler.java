package com.his.server.config;

import com.his.common.common.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.validation.BindException;
import org.springframework.validation.BindingResult;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.stream.Collectors;

/**
 * 统一异常出口：任何异常都转换成 {@code Result{code,message,data}} 信封，
 * 而不是 Spring 默认的 {@code {timestamp,status,error,path}}，否则前端 axios 拦截器
 * （按 {@code code !== 0} 判定失败）拿不到可展示的 message。
 *
 * <p>泛化异常只回固定文案，不把异常明细/堆栈写进 {@code message}（避免信息泄露），
 * 明细走服务端日志。
 *
 * <p>HTTP 状态码保持 200，业务错误由信封里的 {@code code} 表达——这与本项目的统一返回体
 * 约定（以及前端拦截器的成功分支）一致，保证前端能看到信封中的 message。
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** {@code @RequestBody + @Valid} 校验失败：400 + 可读的字段提示。 */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public Result<Void> handleMethodArgumentNotValid(MethodArgumentNotValidException e) {
        return fail(e.getBindingResult());
    }

    /** 表单/查询参数绑定校验失败：同样是 400。 */
    @ExceptionHandler(BindException.class)
    public Result<Void> handleBind(BindException e) {
        return fail(e.getBindingResult());
    }

    /** 兜底：未预期异常一律 500 + 固定文案。 */
    @ExceptionHandler(Exception.class)
    public Result<Void> handleException(Exception e) {
        log.error("未处理的服务端异常", e);
        return Result.fail(500, "服务器内部错误");
    }

    private static Result<Void> fail(BindingResult br) {
        String detail = br.getFieldErrors().stream()
                .map(GlobalExceptionHandler::fieldMessage)
                .collect(Collectors.joining("; "));
        if (detail.isBlank()) {
            detail = "参数校验失败";
        }
        log.warn("请求参数校验失败: {}", detail);
        return Result.fail(400, detail);
    }

    private static String fieldMessage(FieldError fe) {
        String msg = fe.getDefaultMessage();
        return fe.getField() + (msg == null || msg.isBlank() ? " 参数不合法" : " " + msg);
    }
}
